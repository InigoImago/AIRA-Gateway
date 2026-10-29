"""What both planes agree on about the mail a budget sends (`FRD-409`).

Management validates the thresholds an administrator sets, and the gateway reads the same list from
the use case's event. One rule and one default, because a list one plane accepts and the other
reads differently is a mail that goes out at a share nobody set.
"""

from __future__ import annotations

#: The shares of a budget a use case mails at when its administrators set nothing: the moment it is
#: used up. Both planes default to it, so an event from an older Management changes nothing.
DEFAULT_BUDGET_MAIL_THRESHOLDS: tuple[int, ...] = (100,)

#: How many thresholds a use case may name. More than a handful is a mail at every step.
MAX_BUDGET_MAIL_THRESHOLDS = 5

#: How long the record of a mail outlives the period it was about, in days. The gateway deletes it
#: then, and the privacy notice says so from this number rather than restating it.
KEEP_NOTICES_DAYS = 30


class ThresholdsInvalid(ValueError):
    """Thresholds a use case cannot have, said in the words an administrator reads."""


def budget_mail_thresholds(value: object) -> list[int]:
    """``value`` as a use case keeps it: whole percentages from 1 to 100, distinct, ascending.

    Raises :class:`ThresholdsInvalid` naming what is wrong rather than dropping a value: a
    threshold an administrator typed and the system ignored is a mail they wait for in vain.
    """
    if not isinstance(value, list | tuple):
        raise ThresholdsInvalid("Thresholds are a list of percentages.")
    if len(value) > MAX_BUDGET_MAIL_THRESHOLDS:
        raise ThresholdsInvalid(f"At most {MAX_BUDGET_MAIL_THRESHOLDS} thresholds.")
    seen: set[int] = set()
    for item in value:
        # `bool` is an `int` to Python, and `true` is no percentage.
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 100:
            raise ThresholdsInvalid("Each threshold is a whole percentage from 1 to 100.")
        if item in seen:
            raise ThresholdsInvalid(f"{item} % is named twice.")
        seen.add(item)
    return sorted(seen)
