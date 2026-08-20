"""The usage report as a spreadsheet (FRD-602).

A **renderer over the report that already exists**, never a second query. The obvious way to build
an export is a new endpoint that queries and formats, and that is exactly how an export ends up
returning more than the screen does: `FRD-601`'s visibility rule is one function, and a second
entry point is a second chance to forget it. Here the scope decision has already happened by the
time any of this runs.

Two smaller decisions worth stating, because both look like oversights:

**A BOM.** UTF-8 with a byte-order mark, so Excel reads `Müller` as a name rather than as mojibake.
Nothing else needs it and nothing else minds it.

**Commas, not semicolons.** RFC 4180, with `.` as the decimal separator. German Excel opens
semicolon files directly and would be happier — and every non-German tool and every script would
be worse off. The honest fix is not to pick the other surprise but to say, on the download link,
that Excel may ask about the separator.

**And a third, which was missing rather than decided:** a cell that begins with `=` is a *formula*
to the spreadsheet this file is written for, and the `key` column is caller content — see
`aira_common.spreadsheet`, which owns the rule because Management's smoke-test export has the same
one and copied everything above this line and not this.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from aira_common.money import format_display
from aira_common.spreadsheet import safe_cell

#: The breakdowns the report offers. A CSV is *one* table, so the caller picks — silently choosing
#: one of three would be a guess presented as a document.
BREAKDOWNS = ("use_case", "model", "member")

#: `FRD-601`'s row, in the order a reader scans it: who, how much traffic, how much of it failed,
#: what it consumed, what it cost, and how long it took.
COLUMNS = (
    "key",
    "requests",
    "failed",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "unpriced_requests",
    "avg_latency_ms",
    "max_latency_ms",
)

#: Excel needs this to read a UTF-8 file as UTF-8. It is invisible to every other consumer.
BOM = "﻿"


class UnknownBreakdown(Exception):
    """A breakdown the report does not have. Named rather than defaulted."""


def filename(breakdown: str, start: str, end: str) -> str:
    """`aira-usage_<breakdown>_<from>_<to>.csv` — sortable, and it says what it contains.

    Dates are trimmed to the day: a filename carrying a full timestamp is one nobody can type, and
    the window is in the file's own header row anyway.
    """
    return f"aira-usage_{breakdown}_{start[:10]}_{end[:10]}.csv"


def render(report: dict[str, Any], breakdown: str, currency: str) -> str:
    """One breakdown of the report as RFC 4180 CSV.

    Money is formatted for display rather than written as nano-units: a spreadsheet is read by
    people, and a column of integers in billionths is a column nobody can sum in their head. The
    integer stays in the JSON, which is what a script should be reading.
    """
    if breakdown not in BREAKDOWNS:
        raise UnknownBreakdown(
            f"'{breakdown}' is not a breakdown. Available: {', '.join(BREAKDOWNS)}."
        )

    rows = report.get(f"by_{breakdown}") or []
    buffer = io.StringIO()
    # `\r\n` is what RFC 4180 specifies, and what Excel expects from a file it did not write.
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

    writer.writerow([*COLUMNS[:6], f"cost_{currency.lower()}", *COLUMNS[7:]])
    for row in rows:
        # Every cell through `safe_cell`, not only the one that is caller content today. `key` is
        # the one that is — a use case slug, a model name, a subject — and naming it here would be
        # a guard about a column rather than about a file: the next column somebody adds is added
        # to this list, not to a rule they have to remember. Nothing else changes, because the
        # figures are integers and a rendered amount never starts with one of those characters.
        writer.writerow(
            [
                safe_cell(row.get("key", "")),
                safe_cell(row.get("requests", 0)),
                safe_cell(row.get("failed", 0)),
                safe_cell(row.get("prompt_tokens", 0)),
                safe_cell(row.get("completion_tokens", 0)),
                safe_cell(row.get("total_tokens", 0)),
                safe_cell(format_display(int(row.get("cost_nanos") or 0))),
                safe_cell(row.get("unpriced_requests", 0)),
                safe_cell(row.get("avg_latency_ms", 0)),
                safe_cell(row.get("max_latency_ms", 0)),
            ]
        )

    unpriced = sum(int(row.get("unpriced_requests") or 0) for row in rows)
    if unpriced:
        # The screen carries this caveat, and a spreadsheet that dropped it would understate spend
        # in exactly the document where understating it matters most — the one that gets forwarded.
        writer.writerow([])
        writer.writerow(
            [
                f"# {unpriced} request(s) used a model with no price on file. The cost column is a "
                "lower bound."
            ]
        )

    return BOM + buffer.getvalue()
