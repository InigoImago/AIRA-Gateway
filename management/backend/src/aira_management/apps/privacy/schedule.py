"""When somebody has to read the privacy notice again (`FRD-625` FR-4).

One pure function, so the rule is tested without a database and read in one place: the view asks
it, and the console is told its answer rather than working one out.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from aira_common.clock import month_starts


class NoticeMode(StrEnum):
    """``AIRA_PRIVACY_NOTICE_MODE``."""

    #: Once per edition: a changed notice is shown again, an unchanged one never.
    ONCE = "once"
    #: Per edition, and again at the first sign-in of every calendar month.
    MONTHLY = "monthly"
    #: On every start of the console — for reviewing the notice, not for everyday use.
    ALWAYS = "always"


class DueReason(StrEnum):
    """Why the window opens, so it can say so in the reader's language."""

    #: This person has never acknowledged any edition.
    FIRST = "first"
    #: They acknowledged an earlier edition, and this one differs.
    CHANGED = "changed"
    #: They acknowledged this edition, but not yet this month.
    MONTH = "month"
    #: The installation shows it every time.
    ALWAYS = "always"


@dataclass(frozen=True, slots=True)
class Acknowledged:
    """The last acknowledgement of one person: which edition, and when."""

    version: str
    at: dt.datetime


def month_start(now: dt.datetime, zone: dt.tzinfo = dt.UTC) -> dt.datetime:
    """Midnight on the first of ``now``'s month on the installation's clock (``zone``,
    `FRD-629`): the month a reader's console shows them is the month they are asked in."""
    return month_starts(now, zone)


def acknowledgement_due(
    mode: NoticeMode,
    version: str,
    last_of_version: Acknowledged | None,
    acknowledged_any: bool,
    now: dt.datetime,
    zone: dt.tzinfo = dt.UTC,
) -> DueReason | None:
    """Why this person must acknowledge ``version`` now, or ``None`` when they need not.

    ``last_of_version`` is their latest acknowledgement **of this version**; ``acknowledged_any``
    whether they ever acknowledged any. A changed notice is due in every mode — the reader agreed
    to a text that no longer exists.
    """
    if mode is NoticeMode.ALWAYS:
        return DueReason.ALWAYS
    if last_of_version is None or last_of_version.version != version:
        return DueReason.CHANGED if acknowledged_any else DueReason.FIRST
    if mode is NoticeMode.MONTHLY and last_of_version.at < month_start(now, zone):
        return DueReason.MONTH
    return None
