# Logging — what every AIRA process writes, and what a line may say

Every AIRA process writes everything it has to say as **one JSON object per line** on its standard
output, and — where the installation exports logs over OTLP (`AIRA_OTEL_ENABLED`,
`AIRA_OTEL_ENDPOINT` or `AIRA_OTEL_LOGS_ENDPOINT`) — sends the same line to the collector. Lines a
library writes (the web server's start and stop, Django's request failures, Python's warnings) are
rendered the same way, and a process that dies of an error says so in one line. Nothing is printed
beside the lines. This page is for whoever operates the log store or the SIEM the lines arrive in.

## Which line says what

Every line carries `event`, `level` and `timestamp`; the lines written while a request is answered
carry its `trace_id`; and every line names the process that wrote it in `process` (`api`,
`consumer`, `retention`, `relay`, `migrate`, or the Django command somebody ran, however it was
started). Where an OTLP export is configured, the record's
resource attributes name the service (`aira-gateway`, `aira-management`).

The lines a SIEM correlates on carry a `category`:

| `category` | `event` | Written when | Fields beyond the above |
| ---------- | ------- | ------------ | ----------------------- |
| `request` | `request_recorded` | for every request the gateway records — served, refused by a rule, failed, abandoned by its caller — and for every model call a pipeline step makes on its behalf | `request_id`, `api`, `operation`, `use_case`, `subject`, `auth_method`, `credential`, `issuer`, `model`, `requested_model`, `status`, `outcome`, `input_tokens`, `output_tokens`, `total_tokens`, `usage_basis`, `cost_nanos`, `latency_ms`, `provider`, `publisher`, `region`, `tools_offered`, `tools_called`, `flagged`, `request_bytes`, the source address |
| `authentication` | `auth_refused` | a request without a valid credential: `401`, and the `429` an address gets after too many | `outcome` (`unauthenticated`), `status`, `reason`, `request_id`, `api` or `route`, the source address, `suppressed` |
| `authorization` | `auth_refused` | a known caller refused a use case or a page: `403`, and a model call that names no use case | `outcome` (`forbidden`, `unattributed`), `status`, `subject`, `auth_method`, `credential`, `use_case_requested`, `reason`, the source address, `suppressed` |
| `configuration` | `config.changed` | every change to what the gateway enforces: use cases, members, group grants, API keys, pipelines, budgets, rate limits, anomaly rules, models, roles | `object`, `object_id`, `action`, `event_type`, `use_case`, `actor`, `person`, `granted_role`, `fields_changed`, `change_id` — never a value |
| `data_access` | `payload_read` | somebody opened a request's stored prompt and response | `request_log_id`, `use_case`, `subject`, `person`, `method`, `credential`, `ground` |
| `data_access` | `directory.search` | somebody searched the directory for a person or group to grant | `actor`, `query_length`, `results`, `source` — never the term |
| `suspension` | `suspension.created`, `suspension.lifted` | a stop was set or lifted | `object_id`, `actor`, `target`, `target_value`, `action`, `use_case` |
| `finding` | `anomaly_detected` | a rule found something | `rule`, `kind`, `target`, `target_value`, `observed`, `threshold` |
| `lifecycle` | `process_started` | a process is configured and its export exists | `service`, `version`, `environment`, `strict_data_mode`, `telemetry_ip`, `pseudonym_key`, `signals`, `log_level` |
| `lifecycle` | `pseudonym_key_per_process` (`warning`) | a process draws its own pseudonym key because `AIRA_PSEUDONYM_SECRET` is not set | `hint` |
| `lifecycle` | `process_stopping`, `process_stopped` | a process was told to stop, and the last thing it writes | `service`, `uptime_seconds` |

A request is re-identified through its `request_id` — the `X-AIRA-Request-Id` its caller received —
in the console's request view, by the roles that may see people; a configuration change through its
`change_id` under *Platform → Configuration changes*.

Lines without a category say what the processes do: `http_request` once per request each service
answers (`method`, `route` — the route's pattern, never the values in it — `status`, `duration_ms`,
and in the gateway `request_id`, `subject` and `use_case`, in Management `actor`); `relay_run` for
every run of the relay that did something (`published`, `parked`, `pending`, `swept`,
`still_parked`; an idle run only at `debug`); `retention_pruned` for every sweep; `worker_healthy`,
`worker_unhealthy`, `worker_stopped`; `migrations_applied` and `migrations_failed`; and the lines a
library writes, which name it in `logger`. A health probe (`/healthz`, `/readyz`) writes no line.

## How a person, an address and an error are written

Every field a line may carry is in the table below, and a field that is not is **left out**, its
name listed under `withheld_fields` — a developer's mistake made visible, never data sent by
accident.

- **A person** — `subject`, `actor`, `person`, `owner`, `reader`, … — is a **pseudonym** while strict
  data mode is on (`AIRA_STRICT_DATA_MODE`, on by default): `p-` and sixteen hexadecimal characters,
  keyed with `AIRA_PSEUDONYM_SECRET`, the same for one person in every process of both services. A
  finding's `target_value` is a pseudonym where its `target` is `subject`.
- **An address** is written as `AIRA_TELEMETRY_IP` says. By default, `source_net` — the network it
  belongs to, the first three parts of an IPv4 address or the first three groups of an IPv6 one —
  and `source_id`, a keyed pseudonym of the whole address (`ip-` and sixteen characters): the same
  address always the same value, so failed sign-ins can be counted per source. `network` writes only
  `source_net`, and `full` writes the address as `source_ip`. The audit trail keeps every address
  whole, for Global Administrator and IT Security.
- **An error** is written as `error_type` and `error_at` — where in AIRA's code it was raised — and,
  for a database error, `constraint`, `table` and `sqlstate`. Its message and traceback are written
  only at `AIRA_LOG_LEVEL=DEBUG`, because they quote what the failing code had in hand.
- **Never** a prompt, an answer, a search term, a credential or a query string.

**Without `AIRA_PSEUDONYM_SECRET`** each process draws its own key: nothing is written in plain text,
but one person is a different pseudonym in every process and after every restart. Each process says
so in a `pseudonym_key_per_process` warning when it starts, and both services' `/readyz` report
`degraded` with `checks.pseudonyms` until the secret is set.

**`auth_refused` is bounded**: one source address writes at most ten such lines a minute, and the
next line it writes after that says in `suppressed` how many were held back — an attack is visible
without filling the log store with it.

## The console's proxy

The console's nginx writes one JSON line per request too — `event` `http_request`, `logger` `nginx`,
`method`, `path`, `status`, `duration_seconds`, `bytes`, `upstream_status`, `upstream_seconds` —
without the query string, the caller's address, `X-Forwarded-For` or the browser's identification.
A request to `/api/` or `/gw/` is named by that prefix alone: the service behind it writes its own
line with the route. Its healthcheck writes none. nginx's own error lines about a request are not
written at all: each would name the caller's address and the whole request line with its query
string — a Gemini client's `?key=` is its API key — whenever an upstream is down, slow or
restarting, and nginx has no format to leave them out. The access line's `status`,
`upstream_status` and `duration_seconds` say what failed. Its start and stop notices remain, as
text that names nobody. These lines are on the container's output only;
nginx exports nothing over OTLP, so a platform that collects container output collects them, and
one that does not leaves them on the host.

## Delivery

Lines are exported in batches. A batch the collector refuses after its retries, or a queue that is
full, loses lines; the audit trail in the database is the record, and the lines are its copy for
somebody else's tooling. The lines a process writes before its export exists — reading its secrets
from Vault, its start-up warnings — are held and sent once the export is there, each with the time
it was written. When a process stops, it hands over what is still queued before it ends.

## Every field

<!-- fields: generated by tools/log_fields_reference.py — edit the register, not this -->

| Field | Type | Written | What it says |
| ----- | ---- | ------- | ------------ |
| `action` | str | as it is | What a rule does when it fires, or what was done to a thing. |
| `actor` | str | a pseudonym under strict data mode | Who did it: the person signed in to the console. |
| `adapter` | str | as it is | Which upstream adapter. |
| `address` | str | as it is | The address of an infrastructure service (Vault, a broker). |
| `api` | str | as it is | Which API surface answered: gemini or kira. |
| `applied` | bool | as it is | Whether a configuration event was applied. |
| `attempt` | int | as it is | Which attempt this is, counting from one. |
| `auth_method` | str | as it is | How the caller authenticated: api_key, oidc or demo. |
| `author` | str | a pseudonym under strict data mode | Who decided a stop: user:<person>, or rule:<id> for the detector. |
| `category` | str | as it is | What kind of record a security-relevant line is: request, authentication, configuration, data_access, suspension, finding or lifecycle. |
| `change_id` | int | as it is | The configuration history's row for a change, to look it up by. |
| `clock_skew_seconds` | float | as it is | The clock skew a token check tolerates. |
| `constraint` | str | as it is | The database constraint an error names; never the values. |
| `cost_nanos` | int | as it is | What a request cost, in nano-units of the currency. |
| `count` | int | as it is | How many. |
| `credential` | str | as it is | The prefix of the API key that made a request — names the key, not its holder. |
| `database` | str | as it is | The name of a database. |
| `database_calls` | bool | as it is | Whether calls to the database are traced. |
| `decisions` | json | as it is | What each pipeline step decided. |
| `dependency` | str | as it is | Which dependency. |
| `detail` | str | as it is | A sentence about what happened, written by AIRA. |
| `dropped` | int | as it is | How many were dropped. |
| `duration_ms` | float | as it is | How long it took, in milliseconds. |
| `environment` | str | as it is | The installation's stage: AIRA_ENVIRONMENT. |
| `error` | str | as it is | What failed, as AIRA describes it — a database's detail with the values is cut. An unexpected error's own message is written only at AIRA_LOG_LEVEL=DEBUG. |
| `error_at` | str | as it is | Where an error was raised: module, line and function. |
| `error_type` | str | as it is | The type of an error. |
| `event` | str | as it is | What happened, in snake_case — or, from a library, the library's own sentence. |
| `event_id` | int | as it is | An outbox event's id. |
| `event_type` | str | as it is | A configuration event's type, as it travels over Kafka. |
| `exception` | str | as it is | The traceback, only at AIRA_LOG_LEVEL=DEBUG. |
| `expiry_leeway_seconds` | float | as it is | How long past its expiry a token is accepted. |
| `field` | str | as it is | The name of a configuration field. |
| `fields` | list | as it is | The names of fields. |
| `fields_changed` | list | as it is | The names of the fields a change changed; never values. |
| `flagged` | bool | as it is | Whether a pipeline step flagged the request. |
| `gave_up` | bool | as it is | Whether it was given up for good. |
| `granted_role` | str | as it is | The role a membership or a group grant gives. |
| `ground` | str | as it is | On what ground stored content was read. |
| `group` | str | as it is | A Kafka consumer group, or the path of a Keycloak group. |
| `hint` | str | as it is | What to do about it. |
| `host` | str | as it is | A host name. |
| `http_calls` | bool | as it is | Whether outgoing HTTP calls are traced. |
| `http_status` | int | as it is | The HTTP status a remote system answered. |
| `input_tokens` | int | as it is | Tokens a model read. |
| `invalid_fields` | list | as it is | The names of the fields a request got wrong. |
| `is_disconnect` | bool | as it is | Whether a database error was a lost connection. |
| `issued_by` | str | a pseudonym under strict data mode | Who issued an API key. |
| `issuer` | str | as it is | The OpenID Connect issuer of a token. |
| `items` | int | as it is | How many items an OTLP batch carried. |
| `jwks_uri` | str | as it is | Where the identity provider's signing keys are read. |
| `key` | str | a pseudonym under strict data mode | A Kafka record's key, which names a member (use case\|username) for a membership. |
| `key_count` | int | as it is | How many keys a counter script touched. |
| `key_set_age_seconds` | float | as it is | How old the signing keys in hand are. |
| `keys` | list | as it is | The names of the secrets Vault supplied; never their values. |
| `kind` | str | as it is | Which kind of thing: a rule's kind, a mail's kind, a read's kind. |
| `latency_ms` | int | as it is | How long a request took, end to end, in milliseconds. |
| `length` | int | as it is | How long a text is, in characters. |
| `level` | str | as it is | debug, info, warning, error or critical. |
| `lifted_by` | str | a pseudonym under strict data mode | Who lifted a stop. |
| `limit` | int | as it is | The bound that applies. |
| `local_only` | bool | as it is | Kept off the export (removed before the line is written). |
| `log_level` | str | as it is | The log level a process runs at. |
| `logger` | str | as it is | Which library wrote a line AIRA did not write itself. |
| `max_age_seconds` | float | as it is | How old a heartbeat may grow. |
| `message` | str | as it is | A library's own message, after credentials are cut. |
| `messages` | int | as it is | How many Kafka messages were read. |
| `method` | str | as it is | An HTTP method, or for a content read how the reader authenticated. |
| `migrations` | list | as it is | The migrations a migration run applied. |
| `model` | str | as it is | Which model. |
| `models` | list | as it is | Which models. |
| `mount` | str | as it is | A Vault mount. |
| `notice` | int | as it is | A mail notice's id. |
| `notices` | int | as it is | How many mail notices. |
| `numeric_id` | str | as it is | A numeric model id that names more than one model. |
| `object` | str | as it is | What a configuration change changed: use_case, membership, group_grant, api_key, pipeline, budget, rate_limit, model, anomaly_rule, role or installation_budget. |
| `object_id` | str | as it is | Which one: a slug, a key prefix, an id — never a person. |
| `observed` | int | as it is | What a detector measured. |
| `offset` | int | as it is | A Kafka offset. |
| `operation` | str | as it is | Which operation: generate, stream, embed, count or rerank. |
| `outcome` | str | as it is | How it ended, from a closed vocabulary. |
| `output_tokens` | int | as it is | Tokens a model wrote, its reasoning included. |
| `owner` | str | a pseudonym under strict data mode | Whose API key it is. |
| `parked` | int | as it is | How many outbox events were set aside, never to be sent. |
| `partition` | int | as it is | A Kafka partition. |
| `path` | str | as it is | A path: an HTTP path without its query, a file or a Vault path. |
| `pattern` | str | as it is | A pattern an administrator configured. |
| `payload` | str | as it is | An OTLP batch as JSON, for debugging; never exported. |
| `payloads_cleared` | int | as it is | How many stored payloads were removed. |
| `pending` | int | as it is | How many outbox events wait to be sent. |
| `person` | str | a pseudonym under strict data mode | Whom it is about: a member added or removed, a key's owner. |
| `plane` | str | as it is | Which plane: gateway or management. |
| `port` | int | as it is | A port. |
| `process` | str | as it is | Which process of a plane wrote the line: api, consumer, retention, relay, migrate, or the Django command that was run. |
| `protocol` | str | as it is | A Kafka security protocol. |
| `provider` | str | as it is | Which upstream platform served a request. |
| `pseudonym_key` | str | as it is | Whether pseudonyms are stable (installation) or differ per process (process). |
| `published` | int | as it is | How many outbox events were sent. |
| `publisher` | str | as it is | Whose model served a request. |
| `query_length` | int | as it is | How long a directory search was, in characters. |
| `quiet_seconds` | float | as it is | How long a worker has made no progress. |
| `reader` | str | a pseudonym under strict data mode | Who read stored content. |
| `realm` | str | as it is | A Keycloak realm. |
| `reason` | str | as it is | Why, as AIRA says it. |
| `recipients` | int | as it is | How many people a mail went to. |
| `region` | str | as it is | Where a model served a request. |
| `rejected` | int | as it is | How many items a collector refused. |
| `request_bytes` | int | as it is | How large a request was. |
| `request_id` | str | as it is | The request's id, as X-AIRA-Request-Id carries it. |
| `request_log_id` | str | as it is | The audit row a content read opened. |
| `request_shape` | str | as it is | Which shape of request was refused. |
| `requested_model` | str | as it is | The model a caller asked for, before any fallback. |
| `resent` | int | as it is | How many were sent again. |
| `result` | str | as it is | What an exporter answered. |
| `results` | int | as it is | How many results a search found. |
| `retry_after` | float | as it is | Seconds until a refused caller may try again. |
| `retry_in_seconds` | float | as it is | Seconds until the next attempt. |
| `route` | str | as it is | The route a request matched, with its parameters as names. |
| `rows_deleted` | int | as it is | How many audit rows were deleted. |
| `rule` | str | as it is | An anomaly rule's name. |
| `rule_id` | int | as it is | An anomaly rule's id. |
| `scope` | str | as it is | What a limit is counted per. |
| `seconds_past_expiry` | float | as it is | How long ago a token expired. |
| `secret_id_source` | str | as it is | Where the Vault secret id was read from. |
| `service` | str | as it is | Which AIRA process: its AIRA_APP_NAME. |
| `settled` | bool | as it is | Whether a budget reservation was settled. |
| `shown` | int | as it is | How many items of a batch are shown. |
| `signal` | str | as it is | An OTLP signal: traces, metrics or logs. |
| `signals` | list | as it is | The OTLP signals a process exports. |
| `slug` | str | as it is | A use case's slug. |
| `source` | str | as it is | Where a search was answered from: keycloak, local or none. |
| `source_id` | str | as it is | A keyed pseudonym of the address a request came from: one address, one value. |
| `source_ip` | str | as `AIRA_TELEMETRY_IP` says | Where a request came from: an IP address. |
| `source_net` | str | as it is | The network a request came from, in CIDR notation. |
| `span_id` | str | as it is | The span the line was written in. |
| `sqlstate` | str | as it is | The SQLSTATE of a database error. |
| `stack` | str | as it is | The stack at the call, where a line asks for it. |
| `status` | int | as it is | An HTTP status. |
| `step` | str | as it is | Which step: of a pipeline, of a directory search. |
| `still_parked` | int | as it is | How many outbox events stay set aside. |
| `strict_data_mode` | bool | as it is | Whether strict data mode is on. |
| `subject` | str | a pseudonym under strict data mode | Who made a request: the caller's identity. |
| `suppressed` | int | as it is | How many lines like this one were held back since the last. |
| `swept` | int | as it is | How many delivered outbox events were deleted. |
| `system` | str | as it is | Which external system a watched call went to. |
| `systems` | list | as it is | Which external systems are watched. |
| `table` | str | as it is | The database table an error names. |
| `target` | str | as it is | What a rule or a stop is about (subject, credential, use_case), or the address an integration call went to. |
| `target_value` | str | as it is | Which one a finding is about — a pseudonym where its target is a person. |
| `telemetry_ip` | str | as it is | How addresses are written: pseudonym, network or full. |
| `threshold` | float | as it is | The threshold a rule fires at. |
| `timestamp` | str | as it is | When, ISO 8601, UTC. |
| `tools_called` | int | as it is | How many functions a model asked for. |
| `tools_offered` | int | as it is | How many functions a request offered. |
| `topic` | str | as it is | A Kafka topic. |
| `total_tokens` | int | as it is | Tokens in all. |
| `trace_id` | str | as it is | The trace the line was written in. |
| `traced` | bool | as it is | Whether a Kafka record carried a trace context. |
| `traces` | int | as it is | How many requests' traces an OTLP batch carried. |
| `unmodelled_fields` | list | as it is | Request fields the surface does not model. |
| `unscanned` | int | as it is | How many characters a check did not read. |
| `uptime_seconds` | float | as it is | How long a process ran. |
| `usage_basis` | str | as it is | What a request's token figures are based on. |
| `use_case` | str | as it is | A use case's slug. |
| `use_case_requested` | str | as it is | The use case a refused request asked for; it was not attributed to it. |
| `use_cases` | int | as it is | How many use cases. |
| `user` | str | as it is | The database account a service connects as. |
| `username` | str | a pseudonym under strict data mode | A person's username. |
| `value` | str | as it is | A configuration value AIRA did not recognise. |
| `vendor` | str | as it is | A database vendor. |
| `version` | str | as it is | The version a process runs. |
| `why` | str | as it is | Why, as AIRA says it. |
| `withheld_fields` | list | as it is | The names of fields a line was given and did not carry: no rule says how to write them. A developer's mistake, made visible. |
| `worker` | str | as it is | Which worker: consumer, relay or retention. |

<!-- fields: end -->
