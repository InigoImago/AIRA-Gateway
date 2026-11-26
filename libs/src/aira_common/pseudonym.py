"""A person's name, as telemetry and logs write it under strict data mode (`FRD-627` FR-14).

Spans, the SIEM feed and log lines leave AIRA's role checks behind: whoever operates the tracing
backend, the SIEM or the log store reads them. Under strict data mode they therefore carry a
**pseudonym** instead of a person's name — the same person always the same pseudonym, so a SIEM can
still correlate one caller's requests, and nobody can read the name off it.

**Keyed, not merely hashed.** Usernames are a short, guessable list; an unkeyed hash of one is
reversed by hashing the directory. The key is `AIRA_PSEUDONYM_SECRET`, from Vault like every
credential, and shared by both planes so a person has one pseudonym everywhere. Without it each
process draws its own key: no name leaks, but pseudonyms differ between processes and restarts.
Re-identifying a person is not this key's job: a span carries its trace id, and the request's audit
row — readable by Global Administrator and IT Security — names the caller.

The same key, under a domain of its own, writes a network address (`aira_common.addresses`), which
is pseudonymised whether or not the mode is on.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import MutableMapping
from typing import Any

#: What a pseudonym starts with, so a reader knows the value is one and not a username.
PREFIX = "p-"

#: Hex characters kept: 64 bits, enough that two people in one installation do not collide.
LENGTH = 16

#: The log fields that name a person. A structured line carries a caller under one of these —
#: and a Kafka record's `key`, which for a membership is `<use case>|<username>`.
PERSON_FIELDS = frozenset(
    {"subject", "person", "username", "reader", "lifted_by", "actor", "owner", "issued_by", "key"}
)

#: Fields that name a person *or* a rule: `user:<name>` is a person's decision, `rule:<id>` the
#: detector's, and only the first is anybody's name.
AUTHOR_FIELDS = frozenset({"author"})
PERSON_AUTHOR = "user:"

#: Fields that name a person only where another field of the same line says so: a finding's or a
#: stop's `target_value` is a person when its `target` is `subject`, and a key's prefix or a use
#: case's slug otherwise — which stay as they are, since they name nobody.
TARGET_FIELDS = {"target_value": ("target", "subject")}

#: This process's own key, drawn once, for when no secret is configured.
_drawn = secrets.token_bytes(32)

#: `AIRA_PSEUDONYM_SECRET`, or ``None`` while it is not set.
_secret: bytes | None = None

#: The key a person's name is written with, or ``None`` while names are written as they are (the
#: mode is off).
_key: bytes | None = None


def configure_pseudonyms(*, enabled: bool, secret: str) -> bool:
    """Write pseudonyms from now on, or names again. Returns whether the key is stable.

    ``secret`` empty with the mode on uses the key this process drew: safe, and unstable.
    """
    global _key, _secret
    _secret = secret.encode("utf-8") if secret else None
    if not enabled:
        _key = None
        return True
    _key = _secret or _drawn
    return bool(secret)


def key_is_stable() -> bool:
    """Whether pseudonyms are the same in every process and after every restart."""
    return _secret is not None


def persons_are_pseudonymised() -> bool:
    """Whether a person's name is written as a pseudonym (strict data mode is on)."""
    return _key is not None


def keyed_digest(data: bytes) -> str:
    """HMAC-SHA256 of ``data`` under the installation's key, else this process's, in hex.

    For a value that is pseudonymised whatever the mode — a network address — so it needs a key
    while names are written as they are.
    """
    return hmac.new(_secret or _drawn, data, hashlib.sha256).hexdigest()


def pseudonym(value: Any) -> Any:
    """``value`` as telemetry may carry it: a pseudonym while the mode is on, else unchanged.

    Empty and non-text values pass through — there is nobody to hide behind ``None``.
    """
    if _key is None or not isinstance(value, str) or not value:
        return value
    if value.startswith(PREFIX) and len(value) == len(PREFIX) + LENGTH:
        return value
    digest = hmac.new(_key, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return PREFIX + digest[:LENGTH]


def pseudonymise_event(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """The structlog processor: every person field of a log line, pseudonymised.

    One place for every line, because a log call is written wherever something happens and a rule
    each call site had to remember is one the next call site forgets.
    """
    if _key is None:
        return event
    for field in PERSON_FIELDS & event.keys():
        event[field] = pseudonym(event[field])
    for field in AUTHOR_FIELDS & event.keys():
        value = event[field]
        if isinstance(value, str) and value.startswith(PERSON_AUTHOR):
            event[field] = PERSON_AUTHOR + pseudonym(value.removeprefix(PERSON_AUTHOR))
    for field, (decided_by, person) in TARGET_FIELDS.items():
        if field in event and str(event.get(decided_by, "")) == person:
            event[field] = pseudonym(event[field])
    return event
