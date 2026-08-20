"""What a cell may start with, in a file somebody opens in Excel (`FRD-602`).

**Two CSV exports, one hazard, and it lives here because neither of them owns it.** The gateway
renders the usage report (`aira_gateway.reporting.csv_export`); Management renders a smoke-test
evaluation (`apps/smoketests/views.py`). The second was written months after the first and says so
— *"the same conventions `FRD-602` had to get right once already"* — and it copied the BOM, the
CRLF and the quoting. It could not copy this one, because this one was in neither.

**A leading `=`, `+`, `-` or `@` makes a spreadsheet evaluate the cell.** Excel, LibreOffice and
Google Sheets all do it. `=HYPERLINK("http://…"&A1,"open")` sends the row it sits beside to
whoever wrote it, the moment a reader clicks what looks like a link in their own export; older
installations still accept `=cmd|'/c …'!A0`. A leading tab or carriage return is the same thing
with the marker moved out of sight.

**The content is a caller's, which is what makes this more than theory here.**

- `AuditTrail.served_model` falls back to `requested_model` for a request that never reached a
  model, so a `404 model_not_found` row carries the string out of the URL — bounded to 128
  characters and free of control characters (`catalog.is_lookupable`) and otherwise anything
  somebody cares to type. Measured on 2026-08-20 against the hermetic app: one refused request for
  a model named `=1+1`, and the month's export by model carries `=1+1,1,0,…` as its first data row.
  Every oversight role can download it and nothing in the file says where that came from.
- The smoke-test export carries a **model's own answer** in a column, which needs no attacker at
  all: ask a model for a spreadsheet formula and it will give you one.

Prefixed with `'`, which every spreadsheet reads as *the rest of this cell is text* and does not
display. Deliberately **not stripped and not refused**: an export has to say what the audit trail
holds, and a row quietly missing from a governance document is a worse failure than an odd-looking
one. The JSON report is untouched — it has no such hazard, and `csv_export`'s own docstring already
sends a script there.
"""

from __future__ import annotations

#: The characters a spreadsheet reads as *this cell is a formula*.
#:
#: `-` is here although a lone negative number is a formula nowhere: `-2+3+cmd|'…'!A0` is one, and
#: no column in either export is legitimately negative — every figure is a count, a duration or a
#: cost. So the cost of being strict is nothing, and the cost of being clever is a bypass.
FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")

#: What makes the rest of a cell text. A single quote, which is the spreadsheet's own escape and is
#: not displayed — so the reader sees the value they expect and the file cannot execute it.
TEXT_MARKER = "'"


def safe_cell(value: object) -> object:
    """``value``, made safe to write into a CSV somebody will open in a spreadsheet.

    Anything that is not a string is returned untouched: a number handed to ``csv.writer`` is
    written as a number, and there is nothing for a formula to begin with.

    Idempotent in the way that matters — a value already beginning with ``'`` is not a formula
    starter, so it is left exactly as it is rather than accumulating quotes.
    """
    if isinstance(value, str) and value.startswith(FORMULA_STARTERS):
        return f"{TEXT_MARKER}{value}"
    return value
