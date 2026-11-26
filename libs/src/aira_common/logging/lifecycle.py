"""What a process says about itself when it starts and stops (`FRD-631` FR-3, FR-4, FR-10).

A log store that never sees a service start cannot tell a quiet service from a stopped one, and one
that never sees its configuration cannot tell why two processes disagree. So every process says, in
one line once its export exists, what it is and how it writes what it writes — the version, the
stage, strict data mode, how addresses are written, whether its pseudonyms are stable, which
signals it exports — and says when it stops, and that the last thing it does is hand over what it
has not exported yet.

**A key that is not stable is said where an alert can see it.** Without `AIRA_PSEUDONYM_SECRET`
each process draws its own key, so the SIEM sees one person as a different pseudonym in every
process and after every restart. Not a reason to refuse to start — an installation runs without it
today, and a secret is a change to somebody else's Vault — but a warning in the log store, and a
`degraded` readiness that names it.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from aira_common.addresses import AddressMode, address_mode, configure_addresses
from aira_common.logging.pipeline import get_logger
from aira_common.pseudonym import configure_pseudonyms, key_is_stable, persons_are_pseudonymised

_log = get_logger("aira_common.lifecycle")

#: What a line about a process's own life is, for a SIEM that filters by kind.
LIFECYCLE = "lifecycle"

#: When this process was configured, for the uptime its last line states.
_started_at = time.monotonic()


def configure_identities(
    *, strict_data_mode: bool, pseudonym_secret: str, telemetry_ip: str
) -> None:
    """How this process writes a person and an address from now on — both planes, every process."""
    configure_pseudonyms(enabled=strict_data_mode, secret=pseudonym_secret)
    configure_addresses(telemetry_ip)


def pseudonyms_in_use() -> bool:
    """Whether anything this process writes is a pseudonym: a person under strict data mode, or
    an address in the default mode."""
    return persons_are_pseudonymised() or address_mode() is AddressMode.PSEUDONYM


def pseudonym_state() -> tuple[bool, str]:
    """``(stable, what to say)`` about the key pseudonyms are written with, for readiness."""
    if not pseudonyms_in_use():
        return True, "no pseudonyms are written"
    if key_is_stable():
        return True, "one key for the installation (AIRA_PSEUDONYM_SECRET)"
    return False, (
        "each process draws its own key, so one person or address is a different pseudonym in "
        "every process and after every restart; set AIRA_PSEUDONYM_SECRET (from Vault, the same "
        "value for both planes)"
    )


def say_started(
    *,
    service: str,
    version: str,
    environment: str,
    signals: Iterable[str],
    log_level: str,
) -> None:
    """The line a process writes once it is configured and its export exists."""
    global _started_at
    _started_at = time.monotonic()
    _log.info(
        "process_started",
        category=LIFECYCLE,
        service=service,
        version=version,
        environment=environment,
        strict_data_mode=persons_are_pseudonymised(),
        telemetry_ip=str(address_mode()),
        pseudonym_key="installation" if key_is_stable() else "process",
        signals=sorted(signals),
        log_level=log_level.upper(),
    )
    stable, detail = pseudonym_state()
    if not stable:
        # Said once per process, after the export exists, so an alert on warnings in the log store
        # sees it: a SIEM that sees one person as several callers has nothing else to tell it why.
        _log.warning("pseudonym_key_per_process", category=LIFECYCLE, hint=detail)


def say_stopping(*, service: str) -> None:
    """The line a process writes when it has been told to stop, before it winds down."""
    _log.info("process_stopping", category=LIFECYCLE, service=service)


def say_stopped(*, service: str) -> None:
    """The last line a process writes, just before it hands its telemetry over."""
    _log.info(
        "process_stopped",
        category=LIFECYCLE,
        service=service,
        uptime_seconds=round(time.monotonic() - _started_at, 1),
    )
