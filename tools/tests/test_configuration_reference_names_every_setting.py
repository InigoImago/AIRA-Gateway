"""`docs/CONFIGURATION.md` says it lists every `AIRA_*` variable. Nothing checked that it did.

The claim is in `CLAUDE.md` — *"every `AIRA_*` variable, defaults dumped from the settings classes
rather than remembered"* — and on 2026-08-18 nine were missing, five of them the entire Kafka
authentication family: `AIRA_KAFKA_SECURITY_PROTOCOL`, the three `SASL_*` and `SSL_CAFILE`.

That set is not an oversight of the harmless kind. `PLAINTEXT` is *refused outside `local`* because
both planes apply what arrives on those topics — the gateway builds the read-model its
authorization comes from out of them — so an operator deploying to production must configure them
and had nothing in the reference telling them how. `AIRA_TRUSTED_PROXY_HOPS` was missing too, and
getting it wrong lets a caller choose the address that lands in the audit trail.

This is the shape `CLAUDE.md` §4 already describes about the FRD headers: the copy that is read
every session stays true and the copy nobody opens rots. A reference document is the copy nobody
opens *until it matters*, which is the worst moment to discover it is short.

So both directions, as everywhere else in this repository:

1. every field of the settings classes is named in the reference;
2. every `AIRA_*` the reference names is a field that exists — a documented variable that does
   nothing is worse than an undocumented one, because somebody sets it and believes it took.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "CONFIGURATION.md"

#: Variables the reference names that belong to something other than a settings class: Compose's
#: own publishing variables, and the console's runtime configuration, which is baked by the
#: entrypoint rather than parsed by Pydantic. Each is documented where a reader needs it.
NOT_SETTINGS = {
    "AIRA_STACK",
    "AIRA_BIND_HOST",
    "AIRA_OIDC_ISSUER",
    "AIRA_OIDC_CLIENT_ID",
    "AIRA_CSP_CONNECT_SRC",
    "AIRA_ISSUER_ORIGIN",
    "AIRA_GATEWAY_UPSTREAM",
    "AIRA_MANAGEMENT_UPSTREAM",
    "AIRA_SEED_LOCAL_CHAT_MODEL",
    "AIRA_SEED_LOCAL_EMBED_MODEL",
    "AIRA_E2E_BASE_URL",
    "AIRA_E2E_GATEWAY_URL",
    "AIRA_E2E_KEYCLOAK_URL",
    "AIRA_GATEWAY_URL",
    "AIRA_CONSOLE_URL",
    "AIRA_DEMO_CHAT_MODEL",
    # Read by the **collector's** own configuration through `${env:…}` and by Compose, not by any
    # settings class — how much it says about what arrives, where to write it, and everything about
    # a second destination. Documented in §5a for the reason the whole file exists: an operator
    # looking for a knob does not care which process reads it.
    "AIRA_OTEL_DEBUG_VERBOSITY",
    "AIRA_OTEL_ARRIVED_FILE",
    "AIRA_OTEL_FORWARD_CONFIG",
    "AIRA_OTEL_FORWARD_ENDPOINT",
    "AIRA_OTEL_FORWARD_AUTHORIZATION",
    "AIRA_OTEL_FORWARD_INSECURE",
    "AIRA_OTEL_FORWARD_BATCH_SECONDS",
    "AIRA_OTEL_FORWARD_BATCH_SIZE",
    "AIRA_OTEL_FORWARD_CONSUMERS",
    "AIRA_OTEL_FORWARD_QUEUE",
    "AIRA_OTEL_FORWARD_RETRY_INITIAL",
    "AIRA_OTEL_FORWARD_AUTH_CONFIG",
    "AIRA_OTEL_FORWARD_TRACES_ENDPOINT",
    "AIRA_OTEL_FORWARD_LOGS_ENDPOINT",
    "AIRA_OTEL_FORWARD_METRICS_ENDPOINT",
    "AIRA_OTEL_FORWARD_ENCODING",
    "AIRA_OTEL_FORWARD_CA_FILE",
    "AIRA_OTEL_FORWARD_AZURE_CLIENT_ID",
    "AIRA_OTEL_FORWARD_AZURE_SCOPE",
    "AIRA_OTEL_FORWARD_PROTOCOL_CONFIG",
    "AIRA_OTEL_FORWARD_GRPC_ENDPOINT",
    "AIRA_OTEL_FORWARD_GRPC_PLAINTEXT",
    "AIRA_OTEL_FORWARD_COMPRESSION",
    "AIRA_OTEL_FORWARD_AUTH_HEADER",
    "AIRA_OTEL_FORWARD_BASIC_USERNAME",
    "AIRA_OTEL_FORWARD_BASIC_PASSWORD",
    "AIRA_OTEL_FORWARD_OAUTH_TOKEN_URL",
    "AIRA_OTEL_FORWARD_OAUTH_CLIENT_ID",
    "AIRA_OTEL_FORWARD_OAUTH_CLIENT_SECRET",
    "AIRA_OTEL_FORWARD_OAUTH_SCOPES",
    "AIRA_OTEL_FORWARD_OAUTH_CA_FILE",
    "AIRA_OTEL_FORWARD_CLIENT_CERT_FILE",
    "AIRA_OTEL_FORWARD_CLIENT_KEY_FILE",
    # The standing-in SIEM (`FRD-618`, `debug` profile): read by `tools/otlp_inspector.py`,
    # which is a debugging tool run in a container of its own rather than a plane with settings.
    "AIRA_OTLP_INSPECTOR_KEEP",
    # The host a browser types, read by the Keycloak realm import for twenty seconds.
    "AIRA_CONSOLE_HOST",
}


def _settings_fields() -> set[str]:
    for path in ("gateway/src", "libs/src", "management/backend/src"):
        candidate = str(ROOT / path)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    # **Both planes, and no `try`/`except` around the second.** The first version of this swallowed
    # an ImportError on Management's settings, which meant the check silently measured half the
    # product — and then reported thirteen of Management's own variables as documented-but-
    # nonexistent. A guard that quietly narrows its own scope is the thing this file was written
    # against, one level up.
    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    return {
        f"AIRA_{name}".upper()
        for settings in (GatewaySettings, ManagementSettings)
        for name in settings.model_fields
    }


def _named_in_reference(known: set[str]) -> set[str]:
    """Every variable the document names, **including the combined rows.**

    A row may read ``` `AIRA_POSTGRES_HOST` / `_PORT` / `_DB` ``` — one line for a family, which is
    better for a reader than five. The suffix forms are expanded against the stem so that a
    genuinely missing variable is still reported: the alternative is a check that passes whenever
    somebody uses the shorthand, which is a check that measures the notation and not the content.
    """
    text = REFERENCE.read_text()
    named = set(re.findall(r"`(AIRA_[A-Z0-9_]+)`", text))
    for line in text.splitlines():
        stem: str | None = None
        for token in re.findall(r"`(AIRA_[A-Z0-9_]+|_[A-Z0-9_]+)`", line):
            if token.startswith("AIRA_"):
                stem = token
            elif stem:
                parts = stem.split("_")
                for cut in range(1, len(parts)):
                    candidate = "_".join(parts[:-cut]) + token
                    if candidate in known:
                        named.add(candidate)
    return named


def test_the_reference_names_variables_at_all() -> None:
    """A guard on the guard: an empty document would satisfy direction 2 by naming nothing."""
    known = _settings_fields()

    assert len(known) > 50, len(known)
    assert len(_named_in_reference(known)) > 50


def test_every_setting_is_in_the_reference() -> None:
    known = _settings_fields()
    missing = sorted(known - _named_in_reference(known))

    assert not missing, (
        "These are settings the services read and the configuration reference does not name:\n  "
        + "\n  ".join(missing)
        + "\n\nThe document claims to list every `AIRA_*` variable, and an operator deploying this "
        "reads it instead of the source."
    )


def test_the_reference_names_nothing_that_does_not_exist() -> None:
    """The other direction. A documented variable that nothing reads is worse than a missing one:
    somebody sets it, sees no error, and believes the setting took."""
    known = _settings_fields()
    # **Table rows only.** The tables *are* the reference; the prose around them explains, and it
    # sometimes has to quote a name that is wrong on purpose — the note about `AIRA_VAULT_ADDRESS`
    # says so in as many words. A check that could not tell those apart would push the explanation
    # out of the document, which is the opposite of what it is for.
    rows = [line for line in REFERENCE.read_text().splitlines() if line.lstrip().startswith("|")]
    named = set(re.findall(r"`(AIRA_[A-Z0-9_]+)`", "\n".join(rows)))
    invented = sorted(
        named - known - NOT_SETTINGS - {n for n in named if n.startswith("AIRA_PUBLISH_")}
    )

    assert not invented, (
        "These are named in the configuration reference and are not settings of either service:\n  "
        + "\n  ".join(invented)
        + "\n\nEither the setting was renamed and the document was not, or the variable belongs to "
        "Compose or the console entrypoint — in which case add it to `NOT_SETTINGS` with a reason."
    )


# --- and the knobs that are not settings ---------------------------------------------------------


COMPOSE_DIR = ROOT / "deploy" / "compose"
ENV_EXAMPLE = COMPOSE_DIR / ".env.example"

#: Named in `.env.example` and deliberately absent from the reference, with the reason.
#:
#: Kept tiny on purpose. The point of the check below is that an operator meeting a variable in the
#: file they copy can look it up, so every entry here is a promise that they will not need to.
NOT_IN_THE_REFERENCE = {
    "AIRA_BIND_HOST6": "documented beside AIRA_BIND_HOST, which the reference does carry",
}


def _offered_by_the_example() -> set[str]:
    """`AIRA_*` names `.env.example` offers as settable, commented out or not."""
    return set(re.findall(r"^\s*#?\s*(AIRA_[A-Z0-9_]+)\s*=", ENV_EXAMPLE.read_text(), re.M))


def _documented_names() -> set[str]:
    """Every `AIRA_*` the reference names, **including the suffix shorthand it writes tables in**.

    A row reads ``AIRA_POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD`` — one row for five
    variables, which is right for the reader and invisible to a naive search. Expanding the
    shorthand here rather than exploding the table into five rows: the document is for a person,
    and the check exists to serve that document rather than to reshape it.
    """
    text = REFERENCE.read_text()
    names = set(re.findall(r"`(AIRA_[A-Z0-9_]+)`", text))
    # `AIRA_X_HOST` / `_PORT` — a bare suffix belongs to the last full name before it on the line.
    for line in text.splitlines():
        current = ""
        for token in re.findall(r"`(AIRA_[A-Z0-9_]+|_[A-Z0-9_]+)`", line):
            if token.startswith("AIRA_"):
                current = token
            elif current:
                # `AIRA_POSTGRES_HOST` + `_DB` -> `AIRA_POSTGRES_DB`. Every prefix is tried,
                # because a family's shared stem can be any depth — `AIRA_OLLAMA_URL` +
                # `_EMBEDDING_MODELS` is two segments — and pinning one depth made the check
                # report a variable that is documented on the very line it was reading.
                parts = current.split("_")
                for cut in range(len(parts), 0, -1):
                    names.add("_".join(parts[:cut]) + token)
    return names


def test_every_knob_the_example_offers_is_in_the_reference() -> None:
    """**The gap that let seventeen variables through.**

    The checks above compare the reference against the *settings classes*, so a knob read by the
    collector or by Compose is invisible to them — and on 2026-09-02 fifteen of seventeen new
    variables were in `.env.example`, wired into Compose, working, and named nowhere in the
    document that promises to list every one.

    That is this file's own docstring happening again one layer out: *"a reference document is the
    copy nobody opens until it matters, which is the worst moment to discover it is short."* The
    first check was written from the settings classes because that is where settings live, and the
    knobs that are not settings are exactly the ones nobody thinks to add.

    `AIRA_PUBLISH_*` is exempt as a family: they are ports, the reference explains the family once,
    and a table row per port would be fourteen rows saying the same thing.
    """
    offered = _offered_by_the_example()
    documented = _documented_names()
    missing = sorted(
        offered
        - documented
        - set(NOT_IN_THE_REFERENCE)
        - {name for name in offered if name.startswith("AIRA_PUBLISH_")}
    )

    assert not missing, (
        f"`.env.example` offers these and `CONFIGURATION.md` does not name them: {missing}. "
        "An operator who meets a variable in the file they copy has one place to look it up — "
        "add a row, or add it to NOT_IN_THE_REFERENCE with the reason it belongs nowhere."
    )


def test_the_exemptions_are_still_offered() -> None:
    """A waiver for a variable nobody offers any more is one that silently covers the next."""
    stale = sorted(set(NOT_IN_THE_REFERENCE) - _offered_by_the_example())
    assert not stale, f"These are exempt and `.env.example` no longer offers them: {stale}."
