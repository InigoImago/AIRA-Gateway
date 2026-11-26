"""Recording one configuration change: a row with before and after, and a line (`FRD-632`).

"Who changed this permission, this budget, this model's release, and what was there before?" had no
answer — not in the log, which said nothing about a change, and not in the database, which kept a
history for roles only. The outbox is no substitute: it carries the state after a change, names
nobody, and deletes what it delivered after thirty days.

**Every change that reaches the gateway is already announced in one place** (`usecases.events`),
from inside the transaction that makes it, so that is where it is recorded: a row per event, in the
same transaction, so a change that rolled back has no history and a row never describes a change
that did not happen. The state before is the state the last row about the same thing recorded —
or, for a thing last changed before this history began, what the outbox last sent for it.

**The row names people; the line does not.** The row keeps the actor and the person concerned in
plain text, behind `incident.investigate`. The line, ``config.changed``, says *that* something
changed — what kind, which one by its id, what was done, by whom as the pipeline writes a person,
and the names of the fields — never a value, since a value can be anybody's name and a log store
has no role check. It is written once the transaction commits.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from aira_common.logging import get_logger, process_name
from aira_common.observability import trace_context_fields
from aira_management.apps.api.access import current_request
from aira_management.apps.history.models import ConfigurationChange
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.outbox.subscriber import address_of

_log = get_logger("aira_management.history")

#: What kind of thing each event's type names.
KINDS = {
    "usecase": "use_case",
    "membership": "membership",
    "use_case_group": "group_grant",
    "api_key": "api_key",
    "pipeline": "pipeline",
    "budget": "budget",
    "ratelimit": "rate_limit",
    "model": "model",
    "anomaly_rule": "anomaly_rule",
    "role": "role",
}

#: What each verb of an event type did. An upsert is `updated` where the state before is known;
#: where it is not, `created` when a `POST` made it — a request that adds to a collection — and
#: `saved` otherwise: a command, or a thing last changed before this history and its outbox began.
ACTIONS = {
    "created": "created",
    "granted": "granted",
    "revoked": "revoked",
    "removed": "removed",
    "deleted": "deleted",
    "purged": "purged",
}

#: A use case's deletion retires it: its record stays, as a tombstone (`FRD-607`).
_RETIRED = "usecase.deleted"

#: The verbs after which the thing no longer exists, so what comes next starts from nothing.
_GONE = frozenset({"revoked", "removed", "deleted", "purged"})

#: Never kept in a row: what verifies a key is not the history's to hold.
NEVER_KEPT = frozenset({"key_hash"})

#: What a line says a configuration change is, for a SIEM that filters by kind.
CATEGORY = "configuration"


def _kind_and_verb(event_type: str) -> tuple[str, str]:
    noun, _, verb = event_type.rpartition(".")
    return KINDS.get(noun, noun), verb


def _kept(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in NEVER_KEPT}


def _before(
    kind: str, key: str, topic: str, event_type: str, payload: dict[str, Any]
) -> tuple[bool, dict[str, Any] | None]:
    """``(known, state)`` of the thing before this change.

    From the last row about it; else from what the outbox last sent for it — this change's own
    outbox row, written a moment ago in this transaction, is the newest there and is passed over.
    """
    previous = ConfigurationChange.objects.filter(kind=kind, object_key=key).first()
    if previous is not None:
        _, verb = _kind_and_verb(previous.event_type)
        return True, None if verb in _GONE else previous.after
    sent = list(OutboxEvent.objects.filter(topic=topic, key=key).order_by("-id")[:2])
    if sent and sent[0].event_type == event_type and sent[0].payload == payload:
        sent = sent[1:]
    if not sent:
        return False, None
    _, verb = _kind_and_verb(sent[0].event_type)
    return True, None if verb in _GONE else _kept(dict(sent[0].payload))


def _changed(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    old, new = before or {}, after or {}
    return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))


#: The payload field naming each kind's use case; every other kind's is `use_case`.
_USE_CASE_FIELD = {"use_case": "slug", "membership": "slug", "group_grant": "slug"}
#: The payload field naming the person a change is about, for the kinds that name one.
_PERSON_FIELD = {
    "membership": "username",
    "api_key": "subject",
    "budget": "subject",
    "rate_limit": "subject",
}
#: The payload field naming the thing by an id that names nobody; every other kind's is `id`. A
#: membership is named by its use case, since its own key is the member's name.
_ID_FIELD = {
    "use_case": "slug",
    "membership": "slug",
    "group_grant": "group",
    "api_key": "prefix",
    "pipeline": "use_case",
    "model": "name",
    "role": "slug",
}


def _subject_of(kind: str, payload: dict[str, Any]) -> tuple[str, str, str]:
    """``(use case, person, id)``: which use case, whom it is about, and which one it is."""

    def field(name: str | None) -> str:
        return str(payload.get(name) or "") if name else ""

    return (
        field(_USE_CASE_FIELD.get(kind, "use_case")),
        field(_PERSON_FIELD.get(kind)),
        field(_ID_FIELD.get(kind, "id")),
    )


def _actor() -> str:
    request = current_request()
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    return str(user.get_username())


def _posted() -> bool:
    """Whether this change is made by a request that adds to a collection."""
    request = current_request()
    return getattr(request, "method", None) == "POST"


def record_change(event_type: str, payload: dict[str, Any]) -> None:
    """The `usecases.events` subscriber: one row for this change, and its line once committed."""
    topic, key = address_of(event_type, payload)
    kind, verb = _kind_and_verb(event_type)
    after = _kept(payload)
    known, before = _before(kind, key, topic, event_type, payload)
    if event_type == _RETIRED:
        action = "retired"
    elif verb == "upserted":
        if before is not None:
            action = "updated"
        elif known or _posted():
            action = "created"
        else:
            action = "saved"
    else:
        action = ACTIONS.get(verb, verb)
    use_case, person, identifier = _subject_of(kind, payload)
    actor = _actor()
    process = "" if actor else (process_name() or "")
    row = ConfigurationChange.objects.create(
        actor=actor,
        process=process,
        event_type=event_type,
        kind=kind,
        action=action,
        object_key=key,
        use_case=use_case,
        person=person,
        before=before,
        after=after,
        fields_changed=_changed(before, after),
        trace_id=trace_context_fields().get("trace_id") or "",
    )
    line = {
        "category": CATEGORY,
        "object": kind,
        "object_id": identifier,
        "action": action,
        "event_type": event_type,
        "use_case": use_case or None,
        "actor": actor or None,
        "person": person or None,
        "granted_role": payload.get("role") if kind in ("membership", "group_grant") else None,
        "fields_changed": row.fields_changed,
        "change_id": row.pk,
    }
    # Once the change is the database's: a line about a change that rolled back is a false record.
    transaction.on_commit(
        lambda: _log.info(
            "config.changed", **{name: value for name, value in line.items() if value is not None}
        )
    )
