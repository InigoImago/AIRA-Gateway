"""Which fields a log line may carry, and how each is written (`FRD-631` FR-7).

A line leaves AIRA for whoever reads the log store and the SIEM, and neither has a role check. Until
this list existed the rule was the opposite one — every field went out as it was given, and a short
list named the fields to pseudonymise — so a field nobody had thought about went out in plain text:
`directory.search` wrote the name somebody looked up under `query`, and `anomaly_detected` the
person a finding is about under `target_value`. **An allow-list, never a deny-list** (`LESSONS.md`
§2): a field is written only if it is named here, as one of three kinds.

- ``PLAIN`` — written as it is: what happened, to which thing, how many, how long, which code.
- ``PERSON`` — a person: a pseudonym under strict data mode (`aira_common.pseudonym`).
- ``ADDRESS`` — a network address: as `AIRA_TELEMETRY_IP` says (`aira_common.addresses`).

**A field named nowhere is withheld**: removed from the line, and its *name* listed under
``withheld_fields``, so the omission is visible in the line and the next developer registers the
field rather than wondering where it went. `test_every_logged_field_is_registered.py` reads every
log call in the code and fails on a field this module does not know, so in practice nothing is
withheld — the runtime rule is the net under the test, for what the test cannot read
(``**fields``, a library's line).

Each field has **one type** in every line that carries it, because a SIEM or a log store maps a
field to a type when it first sees it and refuses the lines that disagree. The type is written here,
and `docs/LOGGING.md` is generated from this table.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from aira_common.addresses import ADDRESS_FIELDS, WRITTEN_ADDRESS_FIELDS
from aira_common.pseudonym import AUTHOR_FIELDS, PERSON_FIELDS, TARGET_FIELDS


class FieldKind(StrEnum):
    """How a field is written."""

    PLAIN = "plain"
    PERSON = "person"
    ADDRESS = "address"


@dataclass(frozen=True, slots=True)
class Field:
    """One field a line may carry: how it is written, its type, and what it says."""

    kind: FieldKind
    type: str
    meaning: str


#: Where the list of fields a line was given and did not carry goes.
WITHHELD = "withheld_fields"

#: Keys structlog uses while a line is built and removes before it is rendered: a library record's
#: own, and the error and the stack a call hands over to be described.
IN_FLIGHT = frozenset({"_record", "_from_structlog", "exc_info", "stack_info"})


def _plain(type_: str, meaning: str) -> Field:
    return Field(FieldKind.PLAIN, type_, meaning)


def _person(meaning: str) -> Field:
    return Field(FieldKind.PERSON, "str", meaning)


#: Every field a line may carry. Alphabetical; a new field is a new row, with its type and meaning.
FIELDS: dict[str, Field] = {
    "action": _plain("str", "What a rule does when it fires, or what was done to a thing."),
    "actor": _person("Who did it: the person signed in to the console."),
    "adapter": _plain("str", "Which upstream adapter."),
    "address": _plain("str", "The address of an infrastructure service (Vault, a broker)."),
    "api": _plain("str", "Which API surface answered: gemini or kira."),
    "applied": _plain("bool", "Whether a configuration event was applied."),
    "attempt": _plain("int", "Which attempt this is, counting from one."),
    "auth_method": _plain("str", "How the caller authenticated: api_key, oidc or demo."),
    "author": _person("Who decided a stop: user:<person>, or rule:<id> for the detector."),
    "category": _plain(
        "str",
        "What kind of record a security-relevant line is: request, authentication, "
        "configuration, data_access, suspension, finding or lifecycle.",
    ),
    "change_id": _plain("int", "The configuration history's row for a change, to look it up by."),
    "clock_skew_seconds": _plain("float", "The clock skew a token check tolerates."),
    "constraint": _plain("str", "The database constraint an error names; never the values."),
    "cost_nanos": _plain("int", "What a request cost, in nano-units of the currency."),
    "count": _plain("int", "How many."),
    "credential": _plain(
        "str", "The prefix of the API key that made a request — names the key, not its holder."
    ),
    "database": _plain("str", "The name of a database."),
    "database_calls": _plain("bool", "Whether calls to the database are traced."),
    "decisions": _plain("json", "What each pipeline step decided."),
    "dependency": _plain("str", "Which dependency."),
    "detail": _plain("str", "A sentence about what happened, written by AIRA."),
    "dropped": _plain("int", "How many were dropped."),
    "duration_ms": _plain("float", "How long it took, in milliseconds."),
    "environment": _plain("str", "The installation's stage: AIRA_ENVIRONMENT."),
    "error": _plain(
        "str",
        "What failed, as AIRA describes it — a database's detail with the values is cut. An "
        "unexpected error's own message is written only at AIRA_LOG_LEVEL=DEBUG.",
    ),
    "error_at": _plain("str", "Where an error was raised: module, line and function."),
    "error_type": _plain("str", "The type of an error."),
    "event": _plain(
        "str", "What happened, in snake_case — or, from a library, the library's own sentence."
    ),
    "event_id": _plain("int", "An outbox event's id."),
    "event_type": _plain("str", "A configuration event's type, as it travels over Kafka."),
    "exception": _plain("str", "The traceback, only at AIRA_LOG_LEVEL=DEBUG."),
    "expiry_leeway_seconds": _plain("float", "How long past its expiry a token is accepted."),
    "field": _plain("str", "The name of a configuration field."),
    "fields": _plain("list", "The names of fields."),
    "fields_changed": _plain("list", "The names of the fields a change changed; never values."),
    "flagged": _plain("bool", "Whether a pipeline step flagged the request."),
    "gave_up": _plain("bool", "Whether it was given up for good."),
    "granted_role": _plain("str", "The role a membership or a group grant gives."),
    "ground": _plain("str", "On what ground stored content was read."),
    "group": _plain("str", "A Kafka consumer group, or the path of a Keycloak group."),
    "hint": _plain("str", "What to do about it."),
    "host": _plain("str", "A host name."),
    "http_calls": _plain("bool", "Whether outgoing HTTP calls are traced."),
    "http_status": _plain("int", "The HTTP status a remote system answered."),
    "input_tokens": _plain("int", "Tokens a model read."),
    "invalid_fields": _plain("list", "The names of the fields a request got wrong."),
    "is_disconnect": _plain("bool", "Whether a database error was a lost connection."),
    "issued_by": _person("Who issued an API key."),
    "items": _plain("int", "How many items an OTLP batch carried."),
    "issuer": _plain("str", "The OpenID Connect issuer of a token."),
    "jwks_uri": _plain("str", "Where the identity provider's signing keys are read."),
    "key": _person(
        "A Kafka record's key, which names a member (use case|username) for a membership."
    ),
    "key_count": _plain("int", "How many keys a counter script touched."),
    "key_set_age_seconds": _plain("float", "How old the signing keys in hand are."),
    "keys": _plain("list", "The names of the secrets Vault supplied; never their values."),
    "kind": _plain("str", "Which kind of thing: a rule's kind, a mail's kind, a read's kind."),
    "latency_ms": _plain("int", "How long a request took, end to end, in milliseconds."),
    "length": _plain("int", "How long a text is, in characters."),
    "level": _plain("str", "debug, info, warning, error or critical."),
    "lifted_by": _person("Who lifted a stop."),
    "limit": _plain("int", "The bound that applies."),
    "local_only": _plain("bool", "Kept off the export (removed before the line is written)."),
    "log_level": _plain("str", "The log level a process runs at."),
    "logger": _plain("str", "Which library wrote a line AIRA did not write itself."),
    "max_age_seconds": _plain("float", "How old a heartbeat may grow."),
    "message": _plain("str", "A library's own message, after credentials are cut."),
    "messages": _plain("int", "How many Kafka messages were read."),
    "method": _plain("str", "An HTTP method, or for a content read how the reader authenticated."),
    "migrations": _plain("list", "The migrations a migration run applied."),
    "model": _plain("str", "Which model."),
    "models": _plain("list", "Which models."),
    "mount": _plain("str", "A Vault mount."),
    "notice": _plain("int", "A mail notice's id."),
    "notices": _plain("int", "How many mail notices."),
    "numeric_id": _plain("str", "A numeric model id that names more than one model."),
    "object": _plain(
        "str",
        "What a configuration change changed: use_case, membership, group_grant, api_key, "
        "pipeline, budget, rate_limit, model, anomaly_rule, role or installation_budget.",
    ),
    "object_id": _plain("str", "Which one: a slug, a key prefix, an id — never a person."),
    "observed": _plain("int", "What a detector measured."),
    "offset": _plain("int", "A Kafka offset."),
    "operation": _plain("str", "Which operation: generate, stream, embed, count or rerank."),
    "outcome": _plain("str", "How it ended, from a closed vocabulary."),
    "output_tokens": _plain("int", "Tokens a model wrote, its reasoning included."),
    "owner": _person("Whose API key it is."),
    "parked": _plain("int", "How many outbox events were set aside, never to be sent."),
    "partition": _plain("int", "A Kafka partition."),
    "path": _plain("str", "A path: an HTTP path without its query, a file or a Vault path."),
    "pattern": _plain("str", "A pattern an administrator configured."),
    "payload": _plain("str", "An OTLP batch as JSON, for debugging; never exported."),
    "payloads_cleared": _plain("int", "How many stored payloads were removed."),
    "pending": _plain("int", "How many outbox events wait to be sent."),
    "person": _person("Whom it is about: a member added or removed, a key's owner."),
    "plane": _plain("str", "Which plane: gateway or management."),
    "port": _plain("int", "A port."),
    "process": _plain(
        "str",
        "Which process of a plane wrote the line: api, consumer, retention, relay, migrate, or the "
        "Django command that was run.",
    ),
    "protocol": _plain("str", "A Kafka security protocol."),
    "provider": _plain("str", "Which upstream platform served a request."),
    "pseudonym_key": _plain(
        "str", "Whether pseudonyms are stable (installation) or differ per process (process)."
    ),
    "publisher": _plain("str", "Whose model served a request."),
    "published": _plain("int", "How many outbox events were sent."),
    "query_length": _plain("int", "How long a directory search was, in characters."),
    "quiet_seconds": _plain("float", "How long a worker has made no progress."),
    "reader": _person("Who read stored content."),
    "realm": _plain("str", "A Keycloak realm."),
    "reason": _plain("str", "Why, as AIRA says it."),
    "recipients": _plain("int", "How many people a mail went to."),
    "region": _plain("str", "Where a model served a request."),
    "rejected": _plain("int", "How many items a collector refused."),
    "request_bytes": _plain("int", "How large a request was."),
    "request_id": _plain("str", "The request's id, as X-AIRA-Request-Id carries it."),
    "request_log_id": _plain("str", "The audit row a content read opened."),
    "request_shape": _plain("str", "Which shape of request was refused."),
    "requested_model": _plain("str", "The model a caller asked for, before any fallback."),
    "resent": _plain("int", "How many were sent again."),
    "result": _plain("str", "What an exporter answered."),
    "results": _plain("int", "How many results a search found."),
    "retry_after": _plain("float", "Seconds until a refused caller may try again."),
    "retry_in_seconds": _plain("float", "Seconds until the next attempt."),
    "route": _plain("str", "The route a request matched, with its parameters as names."),
    "rows_deleted": _plain("int", "How many audit rows were deleted."),
    "rule": _plain("str", "An anomaly rule's name."),
    "rule_id": _plain("int", "An anomaly rule's id."),
    "scope": _plain("str", "What a limit is counted per."),
    "seconds_past_expiry": _plain("float", "How long ago a token expired."),
    "secret_id_source": _plain("str", "Where the Vault secret id was read from."),
    "service": _plain("str", "Which AIRA process: its AIRA_APP_NAME."),
    "settled": _plain("bool", "Whether a budget reservation was settled."),
    "shown": _plain("int", "How many items of a batch are shown."),
    "signal": _plain("str", "An OTLP signal: traces, metrics or logs."),
    "signals": _plain("list", "The OTLP signals a process exports."),
    "slug": _plain("str", "A use case's slug."),
    "source": _plain("str", "Where a search was answered from: keycloak, local or none."),
    "span_id": _plain("str", "The span the line was written in."),
    "sqlstate": _plain("str", "The SQLSTATE of a database error."),
    "stack": _plain("str", "The stack at the call, where a line asks for it."),
    "status": _plain("int", "An HTTP status."),
    "step": _plain("str", "Which step: of a pipeline, of a directory search."),
    "still_parked": _plain("int", "How many outbox events stay set aside."),
    "strict_data_mode": _plain("bool", "Whether strict data mode is on."),
    "subject": _person("Who made a request: the caller's identity."),
    "suppressed": _plain("int", "How many lines like this one were held back since the last."),
    "swept": _plain("int", "How many delivered outbox events were deleted."),
    "systems": _plain("list", "Which external systems are watched."),
    "table": _plain("str", "The database table an error names."),
    "target": _plain(
        "str",
        "What a rule or a stop is about (subject, credential, use_case), or "
        "the address an integration call went to.",
    ),
    "target_value": _plain(
        "str", "Which one a finding is about — a pseudonym where its target is a person."
    ),
    "telemetry_ip": _plain("str", "How addresses are written: pseudonym, network or full."),
    "threshold": _plain("float", "The threshold a rule fires at."),
    "timestamp": _plain("str", "When, ISO 8601, UTC."),
    "tools_called": _plain("int", "How many functions a model asked for."),
    "tools_offered": _plain("int", "How many functions a request offered."),
    "topic": _plain("str", "A Kafka topic."),
    "total_tokens": _plain("int", "Tokens in all."),
    "trace_id": _plain("str", "The trace the line was written in."),
    "traced": _plain("bool", "Whether a Kafka record carried a trace context."),
    "traces": _plain("int", "How many requests' traces an OTLP batch carried."),
    "unmodelled_fields": _plain("list", "Request fields the surface does not model."),
    "unscanned": _plain("int", "How many characters a check did not read."),
    "uptime_seconds": _plain("float", "How long a process ran."),
    "usage_basis": _plain("str", "What a request's token figures are based on."),
    "use_case": _plain("str", "A use case's slug."),
    "use_case_requested": _plain(
        "str", "The use case a refused request asked for; it was not attributed to it."
    ),
    "use_cases": _plain("int", "How many use cases."),
    "user": _plain("str", "The database account a service connects as."),
    "username": _person("A person's username."),
    "value": _plain("str", "A configuration value AIRA did not recognise."),
    "vendor": _plain("str", "A database vendor."),
    "version": _plain("str", "The version a process runs."),
    "why": _plain("str", "Why, as AIRA says it."),
    WITHHELD: _plain(
        "list",
        "The names of fields a line was given and did not carry: no rule says how to "
        "write them. A developer's mistake, made visible.",
    ),
    "worker": _plain("str", "Which worker: consumer, relay or retention."),
    # The fields of one line about one call to another system (`FRD-617`).
    "system": _plain("str", "Which external system a watched call went to."),
}

for _name in ADDRESS_FIELDS:
    FIELDS[_name] = Field(FieldKind.ADDRESS, "str", "Where a request came from: an IP address.")
FIELDS["source_net"] = _plain("str", "The network a request came from, in CIDR notation.")
FIELDS["source_id"] = _plain(
    "str", "A keyed pseudonym of the address a request came from: one address, one value."
)

#: Every field name a line may carry.
KNOWN = frozenset(FIELDS)


def keep_known_fields(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """The structlog processor: a field this module does not name leaves the line, by name."""
    withheld = [key for key in event if key not in KNOWN and key not in IN_FLIGHT]
    for key in withheld:
        del event[key]
    if withheld:
        event[WITHHELD] = sorted(withheld)
    return event


def _consistent() -> None:
    """The kinds here agree with the modules that write them: a field pseudonymised there is a
    person here, an address there is an address here. Checked at import, so a change to one that
    forgets the other fails the first test that logs."""
    persons = {name for name, field in FIELDS.items() if field.kind is FieldKind.PERSON}
    expected = set(PERSON_FIELDS | AUTHOR_FIELDS)
    if persons != expected:
        raise RuntimeError(
            f"person fields disagree: registered {sorted(persons)}, pseudonymised "
            f"{sorted(expected)}"
        )
    addresses = {name for name, field in FIELDS.items() if field.kind is FieldKind.ADDRESS}
    if addresses != set(ADDRESS_FIELDS):
        raise RuntimeError(f"address fields disagree: {sorted(addresses)}")
    if not (WRITTEN_ADDRESS_FIELDS - ADDRESS_FIELDS) <= KNOWN:
        raise RuntimeError("an address is written under a field this module does not name")
    if not set(TARGET_FIELDS) <= KNOWN:
        raise RuntimeError("a field pseudonymised by its target is not named here")


_consistent()
