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
