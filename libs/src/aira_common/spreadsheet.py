"""What a cell may start with, in a CSV somebody opens in a spreadsheet (`FRD-602`).

A leading `=`, `+`, `-` or `@` makes Excel, LibreOffice and Google Sheets evaluate the cell —
`=HYPERLINK("http://…"&A1,"open")` sends its row to whoever wrote it — and a leading tab or
carriage return hides the same marker. Both CSV exports carry text a caller controls: the usage
report (`aira_gateway.reporting.csv_export`) can carry a requested model name taken from the URL,
and the smoke-test export (`apps/smoketests/views/export.py`) a model's own answer. The rule
lives here because neither export owns it.

Such a value is prefixed with `'`, which spreadsheets read as "the rest is text" and do not
display. Not stripped and not refused: an export must say what the audit trail holds.
"""

from __future__ import annotations

#: The characters a spreadsheet reads as *this cell is a formula*. `-` is included although a lone
#: negative number is harmless: `-2+3+cmd|'…'!A0` is a formula, and no exported column is
#: legitimately negative.
FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")

#: What makes the rest of a cell text: the spreadsheet's own escape, which is not displayed.
TEXT_MARKER = "'"


def safe_cell(value: object) -> object:
    """``value``, made safe to write into a CSV somebody will open in a spreadsheet.

    Non-strings are returned untouched — a number has nothing for a formula to begin with. A value
    already starting with ``'`` is not a formula starter, so quotes never accumulate.
    """
    if isinstance(value, str) and value.startswith(FORMULA_STARTERS):
        return f"{TEXT_MARKER}{value}"
    return value
