"""A count as a person reads it, the same on every surface — a mail, a message, the console.

Thousands are grouped by a **narrow no-break space**: `12 840`, `1 234 567`. That is how the SI
writes a number, and DIN 5008 too; a comma groups thousands in English and marks the decimals in
German, a point the other way round, and a space means neither, so one figure reads the same to
both. No-break, so a line never splits a number. The console groups the same way
(`core/ui/money.ts` `displayCount`).
"""

from __future__ import annotations

#: U+202F NARROW NO-BREAK SPACE, between every group of three digits.
GROUP_SEPARATOR = " "


def display_count(value: int) -> str:
    """``value`` with its thousands grouped: `1234567` is `1 234 567`."""
    return f"{value:,}".replace(",", GROUP_SEPARATOR)
