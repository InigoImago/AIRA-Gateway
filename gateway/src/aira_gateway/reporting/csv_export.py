"""The usage report as a spreadsheet (`FRD-602`).

A **renderer over the report that already exists**, never a second query: `FRD-601`'s visibility
rule is one function, and a second entry point is a second chance to forget it.

- **A BOM**, so Excel reads `Müller` as a name rather than mojibake. Nothing else minds it.
- **Commas, not semicolons** (RFC 4180, `.` as the decimal separator). German Excel would prefer
  semicolons and every other tool and script would be worse off; the download link says Excel may
  ask about the separator.
- **Every cell through `safe_cell`** (`aira_common.spreadsheet`): a cell starting with `=` is a
  formula, and the `key` column is caller content.
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
    """``aira-usage_<breakdown>_<from>_<to>.csv``, dates trimmed to the day so it can be typed."""
    return f"aira-usage_{breakdown}_{start[:10]}_{end[:10]}.csv"


def render(report: dict[str, Any], breakdown: str, currency: str) -> str:
    """One breakdown of the report as RFC 4180 CSV.

    Money is formatted for display, not written as nano-units: a spreadsheet is read by people. The
    integer stays in the JSON, which is what a script should read.
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
        # Every cell through `safe_cell`, not only `key`: the rule is about the file, so a column
        # added later is covered without anybody remembering it.
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
        # The screen's caveat, kept in the document that gets forwarded.
        writer.writerow([])
        writer.writerow(
            [
                f"# {unpriced} request(s) used a model with no price on file. The cost column is a "
                "lower bound."
            ]
        )

    return BOM + buffer.getvalue()
