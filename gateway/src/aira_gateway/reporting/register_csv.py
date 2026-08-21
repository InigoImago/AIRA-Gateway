"""The register as a spreadsheet (`FRD-608` §2.2).

A **renderer over the register that already exists**, never a second query — the shape `FRD-602`
settled for the usage report and for the reason it gives: the visibility rule is one function, and
a second entry point is a second chance to forget it. By the time any of this runs, the scope
decision has already happened.

The CSV is the deliverable rather than a convenience. Printed, one row per use case with purpose,
recipients, third-country transfer and erasure deadline is close to a *Verzeichnis von
Verarbeitungstätigkeiten* — assembled from configuration the gateway enforces rather than from a
spreadsheet somebody maintains beside it. That is also why the columns are wide and wordy where the
JSON is compact: the JSON is read by a script and the CSV by a person, and often by a person who
has never opened this console.

The conventions come from `csv_export.py` — BOM, CRLF, commas — and so does the one that is not a
convention: **every cell goes through `safe_cell`**, because a cell beginning with `=` is a formula
to the spreadsheet all of the above is for, and half of these columns are text somebody typed.
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
    """``aira-register_<from>_<to>.csv`` — sortable, and it says what it contains.

    Dates trimmed to the day, like the usage export: a filename carrying a full timestamp is one
    nobody can type, and the window is in the file's own header row anyway.
    """
    return f"aira-register_{start[:10]}_{end[:10]}.csv"


def _models(entry: Entry) -> str:
    """The released models, one per line inside the cell.

    A separator rather than a column per model: a use case may release one or twenty, and a table
    whose width depends on its widest row is a table no spreadsheet can sort. Newline rather than
    a comma because the file's delimiter is a comma, and quoting a comma-joined list is how a
    reader ends up with one cell they cannot read.
    """
    return "\n".join(model.name for model in entry.models)


def _provenance(entry: Entry) -> str:
    """Where the catalogue says each model lives — the third-country-transfer column.

    A model whose name is its whole address contributes its provider and no region, and says so:
    an empty region here means *this platform does not have one*, not *nobody checked*.
    """
    lines = []
    for model in entry.models:
        where = ", ".join(model.regions) if model.regions else "no region (addressed by name)"
        lines.append(f"{model.name}: {model.provider or 'uncatalogued'} · {where}")
    return "\n".join(lines)


def _unreleased(entry: Entry) -> str:
    """Models this use case names that the installation will not serve.

    Two different faults with one consequence, kept apart because they need different actions:
    *not catalogued* is a disagreement between the two planes, and *not approved* is a decision
    somebody has to take (`FRD-307`). Both mean the same thing to a reader of the register — a
    model in the configuration that no request will ever reach.
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
    """Words, not `True`/`False`. This file is read by people, and half of them in a spreadsheet
    that would helpfully turn `TRUE` into a checkbox."""
    return "yes" if value else "no"


def render(register: Register, start: str, end: str) -> str:
    """The register as RFC 4180 CSV, with the window stated in its own header."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

    # The window, before the table. A register printed without the period its measured half covers
    # is a document whose second half cannot be checked against anything.
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
                # Empty rather than a number where nothing is stored: an erasure deadline for data
                # that was never written is a claim about nothing.
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
        # **Evidence, under the deadlines it is evidence for.** Every row above states a retention
        # period; this states that the sweep enforcing them ran, when, and how much it took.
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
        # Said out loud rather than left off. A missing section reads as "not applicable"; the
        # sentence reads as what it is, which is that the erasure this document promises has no
        # record of having happened.
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
        # The installation's own total, including traffic that names no use case. Below the table
        # rather than as a row in it: it is not a use case, and a row that looks like one in a
        # register of use cases is exactly the kind of thing somebody later cites as one.
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
