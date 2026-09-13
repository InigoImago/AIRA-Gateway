"""The register as a spreadsheet (`FRD-608` §2.2).

A **renderer over the register that already exists**, never a second query (`FRD-602`): the scope
decision has happened by the time this runs. The CSV is read by people — often ones who never open
the console — so its columns are wordier than the JSON's.

The conventions are `csv_export.py`'s — BOM, CRLF, commas — and so is the one that is not a
convention: **every cell goes through `safe_cell`**, because a cell starting with `=` is a formula
and half of these columns are text somebody typed.
"""

from __future__ import annotations

import csv
import io

from aira_common.spreadsheet import safe_cell
from aira_gateway.reporting.csv_export import BOM
from aira_gateway.reporting.register import Entry, Register

#: One row per use case, in the order a governance reader scans it: who, what for, how, with what,
#: where, kept how long, and who can see it.
COLUMNS = (
    "use_case",
    "name",
    "status",
    "purpose",
    "processing",
    "models",
    "provider_region",
    "unreleased_or_unapproved",
    "prompts_stored",
    "retention_days",
    "own_requests_only",
    "tools",
    "prompt_caching",
    "cache_ttl",
    "reasoning",
    "members",
    "groups",
    "requests",
    "processed_in",
    "regions_outside_the_configuration",
)


def filename(start: str, end: str) -> str:
    """``aira-register_<from>_<to>.csv``, dates trimmed to the day — the window is in the file."""
    return f"aira-register_{start[:10]}_{end[:10]}.csv"


def _models(entry: Entry) -> str:
    """The released models, one per line inside the cell.

    Not a column per model — a table whose width depends on its widest row cannot be sorted — and
    not commas, which are the file's delimiter.
    """
    return "\n".join(model.name for model in entry.models)


def _provenance(entry: Entry) -> str:
    """Where the catalogue says each model lives — the third-country-transfer column. A model
    addressed by name says so rather than leaving its region blank."""
    lines = []
    for model in entry.models:
        where = ", ".join(model.regions) if model.regions else "no region (addressed by name)"
        lines.append(f"{model.name}: {model.provider or 'uncatalogued'} · {where}")
    return "\n".join(lines)


def _unreleased(entry: Entry) -> str:
    """Models this use case names that the installation will not serve.

    *Not catalogued* (the two planes disagree) and *not approved* (a decision is pending,
    `FRD-307`) are kept apart because they need different actions.
    """
    faults = []
    for model in entry.models:
        if not model.catalogued:
            faults.append(f"{model.name}: not in the catalogue")
        elif not model.approved:
            faults.append(f"{model.name}: not approved")
    return "\n".join(faults)


def _processed(entry: Entry) -> str:
    return "\n".join(
        f"{where.region} · {where.provider or 'unknown provider'}: {where.requests}"
        for where in entry.processed_in
    )


def _yes_no(value: bool) -> str:
    """Words, not `True`/`False`, which a spreadsheet would turn into checkboxes."""
    return "yes" if value else "no"


def render(register: Register, start: str, end: str) -> str:
    """The register as RFC 4180 CSV, with the window stated in its own header."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

    # The window first: the measured half of the register cannot be checked without it.
    writer.writerow([safe_cell(f"# AIRA register of processing activities, {start} to {end}")])
    writer.writerow([])
    writer.writerow(list(COLUMNS))

    for entry in register.entries:
        writer.writerow(
            [
                safe_cell(entry.slug),
                safe_cell(entry.name),
                safe_cell(entry.status),
                safe_cell(entry.purpose),
                safe_cell(entry.processing),
                safe_cell(_models(entry)),
                safe_cell(_provenance(entry)),
                safe_cell(_unreleased(entry)),
                safe_cell(_yes_no(entry.prompts_stored)),
                # Empty where nothing is stored: a deadline for data never written claims nothing.
                safe_cell("" if entry.retention_days is None else str(entry.retention_days)),
                safe_cell(_yes_no(entry.own_requests_only)),
                safe_cell(_yes_no(entry.tools)),
                safe_cell(_yes_no(entry.prompt_caching)),
                safe_cell(entry.cache_ttl if entry.prompt_caching else ""),
                safe_cell(_yes_no(entry.reasoning)),
                safe_cell(entry.members),
                safe_cell(entry.groups),
                safe_cell(entry.requests),
                safe_cell(_processed(entry)),
                safe_cell(", ".join(entry.unexpected_regions)),
            ]
        )

    if register.last_erasure is not None:
        # The evidence, under the deadlines it is evidence for.
        erasure = register.last_erasure
        writer.writerow([])
        writer.writerow([safe_cell("# the last retention pass")])
        writer.writerow(["ran_at", "payloads_cleared", "rows_deleted"])
        writer.writerow(
            [
                safe_cell(erasure.ran_at.isoformat()),
                safe_cell(erasure.payloads_cleared),
                safe_cell(erasure.rows_deleted),
            ]
        )
    else:
        # Said rather than left off: a missing section reads as "not applicable".
        writer.writerow([])
        writer.writerow(
            [
                safe_cell(
                    "# the retention sweep has no recorded pass — the deadlines above are "
                    "unverified"
                )
            ]
        )

    if register.processed_in:
        # The installation's own total, including traffic that names no use case — below the
        # table, so it is never read as a use case.
        writer.writerow([])
        writer.writerow([safe_cell("# where this installation processed requests in this period")])
        writer.writerow(["region", "provider", "requests"])
        for where in register.processed_in:
            writer.writerow(
                [
                    safe_cell(where.region),
                    safe_cell(where.provider or "unknown provider"),
                    safe_cell(where.requests),
                ]
            )

    return BOM + buffer.getvalue()
