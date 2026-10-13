"""The two rate grammars this service's settings are written in, readable before Django is.

`config.security` checks the rates while Django's settings module is still executing, and nothing
here may import DRF: `rest_framework.throttling` reads DRF's settings when it is imported, so
importing it then binds every throttle to DRF's defaults for the life of the process — no
throttle, and no permission class either.

- **DRF's** (`AIRA_THROTTLE_USER`): ``<count>/<period>``, the period read by its first letter
  (`s`, `m`, `h`, `d`). Restated because DRF cannot be imported here;
  `test_the_api_is_rate_limited.py` holds it equal to DRF's own `parse_rate`.
- **The failed-authentication bound's** (`AIRA_THROTTLE_AUTH_FAILURES`, `apps.api.attempts`):
  the same shape with whole period names, where a count of `0` switches the bound off.
"""

from __future__ import annotations

#: Seconds per period name in the failed-authentication bound's notation.
PERIODS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "h": 3600,
    "hour": 3600,
    "d": 86400,
    "day": 86400,
}

#: The first letters DRF's `SimpleRateThrottle.parse_rate` reads a period by.
DRF_PERIOD_LETTERS = frozenset("smhd")


def bound_rate_problem(rate: str) -> str | None:
    """Why ``rate`` is not ``<n>/<period>`` with ``n`` ≥ 0 and a period in `PERIODS`, or None."""
    count, separator, period = str(rate).partition("/")
    try:
        limit = int(count)
    except ValueError:
        limit = -1
    if not separator or limit < 0 or period.strip().lower() not in PERIODS:
        known = ", ".join(sorted(name for name in PERIODS if len(name) > 1))
        return f"'{rate}' is not '<count>/<period>' with a count of at least 0 and one of {known}"
    return None


def drf_rate_problem(rate: str) -> str | None:
    """Why DRF's throttle could not read ``rate`` or would admit nothing with it, or ``None``."""
    parts = str(rate).split("/")
    try:
        count = int(parts[0]) if len(parts) == 2 else None
    except ValueError:
        count = None
    if count is None or not parts[1] or parts[1][0] not in DRF_PERIOD_LETTERS:
        return f"'{rate}' is not '<count>/<period>', such as '600/minute'"
    if count < 1:
        return f"'{rate}' admits no request at all"
    return None
