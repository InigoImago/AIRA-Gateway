"""A setting the shipped stack cannot pass is a setting no containerised deployment has.

`docker-compose.apps.yml` complains about this defect **three times in its own comments** — the
Ollama timeout, then the Gemini model list and base URL, then two more timeouts — each time with
the same sentence: *a knob that is not wired is worse than an absent one, somebody turns it and
believes the result.* On 2026-08-17 it happened a fourth time and to a whole adapter: **Vertex**,
the EU-regional platform this product's residency story rests on, could not be configured through
the shipped stack at all. Only its timeout was passed. Found by setting an API key and watching
`/readyz` list Mock and Ollama.

The vault-seed loop had made it worse by naming `AIRA_VERTEX_SERVICE_ACCOUNT_JSON`, which is not a
setting anywhere in this codebase. That is the failure mode this file exists for: the one line
mentioning the credential was a name nothing reads, so the gap *looked* closed.

Two rules, and both are deliberately narrow enough to be true rather than aspirational:

1. every `AIRA_*` name the compose files use must exist as a setting somewhere, or be a documented
   compose-only name;
2. every setting that carries a **credential or an upstream address** must be passed to the
   container that needs it — those are the ones whose absence is invisible until somebody depends
   on them, which is exactly when it costs a day.
"""

from __future__ import annotations

import re
from pathlib import Path

import compose_files

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = [
    *compose_files.ALL,
]

#: Names that belong to Compose itself rather than to any settings class — ports, bind addresses,
#: image tags, and the seed's own inputs. Each is read by the compose file or an entrypoint script,
#: never by `GatewaySettings` or `ManagementSettings`.
COMPOSE_ONLY = {
    "AIRA_BIND_HOST",
    "AIRA_GATEWAY_PORT",
    "AIRA_MANAGEMENT_PORT",
    "AIRA_FRONTEND_PORT",
    "AIRA_GATEWAY_UPSTREAM",
    "AIRA_MANAGEMENT_UPSTREAM",
    "AIRA_DNS_RESOLVER",
    "AIRA_CSP_CONNECT_SRC",
    "AIRA_DEMO_CHAT_MODEL",
    "AIRA_DEMO_EMBED_MODEL",
    "AIRA_IMAGE_TAG",
    "AIRA_AZURE_API_KEY",
    "AIRA_OPENAI_API_KEY",
    # Read by the compose file's **own shell**, in the `while true; do … sleep ${…}` commands that
    # drive the relay and the retention sweep. Not settings, and not meant to be.
    "AIRA_RELAY_INTERVAL",
    "AIRA_RETENTION_INTERVAL",
    # Which host ports this stack publishes, and what its containers are called. Read by Compose
    # only. `AIRA_PUBLISH_*` is its own prefix on purpose: `AIRA_POSTGRES_PORT` is a *setting* —
    # the port the gateway connects to — and one name for both meanings would make moving the
    # published port silently redirect the in-network connection.
    "AIRA_STACK",
    # Which interface each family binds to. Read by Compose only — two names because Docker opens
    # one socket per published entry, so a stack bound on `0.0.0.0` alone is reset for every IPv6
    # caller. See `test_every_published_port_serves_both_families.py`.
    "AIRA_BIND_HOST6",
    # The console's published port, **resolved by Compose and handed to Keycloak's realm import**
    # so the client's redirect URIs follow it. Not a setting of either service: it exists for the
    # twenty seconds of a realm import. Its own name rather than reusing `AIRA_PUBLISH_FRONTEND_
    # PORT` because the realm's placeholder syntax takes one name and one default, while Compose's
    # takes a chain — and the chain has to be resolved somewhere.
    "AIRA_CONSOLE_PORT",
    "AIRA_PUBLISH_POSTGRES_PORT",
    "AIRA_PUBLISH_KEYCLOAK_PORT",
    "AIRA_PUBLISH_KEYCLOAK_HEALTH_PORT",
    "AIRA_PUBLISH_KAFKA_PORT",
    "AIRA_PUBLISH_SCHEMA_REGISTRY_PORT",
    "AIRA_PUBLISH_VAULT_PORT",
    "AIRA_PUBLISH_REDIS_PORT",
    "AIRA_PUBLISH_GRAFANA_PORT",
    "AIRA_PUBLISH_OTLP_GRPC_PORT",
    "AIRA_PUBLISH_OTLP_HTTP_PORT",
    "AIRA_PUBLISH_OLLAMA_PORT",
    "AIRA_PUBLISH_GATEWAY_PORT",
    "AIRA_PUBLISH_MANAGEMENT_PORT",
    "AIRA_PUBLISH_FRONTEND_PORT",
    # Written into `runtime-config.js` by the console's entrypoint, and read by the browser rather
    # than by any settings class.
    "AIRA_OIDC_CLIENT_ID",
}

#: The settings whose absence from the stack is invisible until somebody configures one — every
#: credential, every upstream address, every model list. Not "all settings": most have defaults
#: that are genuinely the right answer, and a test demanding all 90 of them would be noise nobody
#: reads. These are the ones where the default means *this adapter does not exist*.
MUST_REACH_THE_GATEWAY = {
    "AIRA_GOOGLE_API_KEY",
    "AIRA_GEMINI_MODELS",
    "AIRA_GEMINI_BASE_URL",
    "AIRA_VERTEX_PROJECT",
    "AIRA_VERTEX_CREDENTIALS",
    "AIRA_VERTEX_API_KEY",
    "AIRA_VERTEX_MODELS",
    "AIRA_FOUNDRY_ENDPOINT",
    "AIRA_FOUNDRY_API_KEY",
    "AIRA_FOUNDRY_DEPLOYMENTS",
    "AIRA_OPENAI_SERVERS",
    "AIRA_OLLAMA_URL",
    "AIRA_OLLAMA_MODELS",
    "AIRA_ALLOWED_REGIONS",
    "AIRA_REDIS_URL",
    "AIRA_KAFKA_BOOTSTRAP_SERVERS",
    "AIRA_OIDC_ISSUER",
    "AIRA_OIDC_AUDIENCE",
    "AIRA_ROLE_GROUPS",
}


def _text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in COMPOSE)


def _wiring() -> str:
    """The compose files with comments stripped.

    A comment that names a historical mistake — this file's own docstring names one — is
    documentation, and a guard that reads it as configuration would fail on the sentence explaining
    why it exists. Only what YAML actually passes counts.
    """
    lines = []
    for line in _text().splitlines():
        stripped = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        lines.append(stripped)
    return "\n".join(lines)


def _read_from_the_environment() -> set[str]:
    """Names some Python reads directly with `os.environ`, which is a legitimate wire too.

    The seed scripts take their model names that way rather than through a settings class, and a
    check that did not know it would report four working variables as phantoms — which is how a
    guard teaches people to add exemptions instead of reading it.
    """
    found: set[str] = set()
    for source in ROOT.glob("**/*.py"):
        if ".venv" in source.parts or "node_modules" in source.parts:
            continue
        pattern = r"""os\.environ(?:\.get)?[(\[]\s*["'](AIRA_[A-Z0-9_]+)"""
        found |= set(re.findall(pattern, source.read_text(encoding="utf-8", errors="ignore")))
    return found


def _settings_names() -> set[str]:
    """Every `AIRA_*` a settings class would accept, from both planes."""
    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    names: set[str] = set()
    for cls in (GatewaySettings, ManagementSettings):
        names |= {f"AIRA_{field.upper()}" for field in cls.model_fields}
    return names


def test_the_compose_files_are_readable() -> None:
    """A guard on the guard: a path typo would make every assertion below vacuous."""
    assert len(_text()) > 5_000, len(_text())


def test_no_compose_line_names_a_setting_that_does_not_exist() -> None:
    """`AIRA_VERTEX_SERVICE_ACCOUNT_JSON` sat here reading as though the credential were wired."""
    named = set(re.findall(r"\bAIRA_[A-Z0-9_]+\b", _wiring()))
    phantom = sorted(named - _settings_names() - _read_from_the_environment() - COMPOSE_ONLY)

    assert not phantom, (
        f"These names appear in the compose files and are settings nowhere: {phantom}. Either the "
        "setting was renamed and this line was not, or the line was written from memory — both "
        "read as a configured credential and pass nothing."
    )


def test_every_credential_and_upstream_setting_reaches_the_container() -> None:
    passed = set(re.findall(r"^\s+(AIRA_[A-Z0-9_]+):", _wiring(), re.M))
    unwired = sorted(MUST_REACH_THE_GATEWAY - passed)

    assert not unwired, (
        f"These settings cannot be set through the shipped stack: {unwired}. Each one's default "
        "means 'this adapter does not exist', so an operator who configures it sees nothing happen "
        "— the defect this file's docstring records four instances of."
    )


def test_the_exempt_names_are_still_absent_from_the_settings_classes() -> None:
    """A waiver that outlives its reason silently covers the next mistake."""
    stale = sorted(COMPOSE_ONLY & _settings_names())

    assert not stale, (
        f"These are listed as compose-only and are now real settings: {stale}. Remove them from "
        "COMPOSE_ONLY so the check above can see them."
    )


def test_no_published_port_is_a_literal() -> None:
    """Two stacks on one machine collide on a fixed port, and there is no way out but editing this
    file.

    Reported after somebody brought up a second system beside this one: eleven of the fourteen
    published ports were hard-coded — Postgres, Keycloak, Kafka, Redis, Grafana, Vault, the two
    OTLP ports, Ollama — while only the three application ports were variables. Each is
    `${AIRA_…_PORT:-<today's value>}` now, so the defaults are unchanged and a second copy moves
    with environment variables.
    """
    literal = re.findall(r'^\s+- "\$\{AIRA_BIND_HOST[^}]*\}:(\d+):', _wiring(), re.M)

    assert not literal, (
        "These host ports cannot be moved without editing the compose file: "
        f"{sorted(set(literal))}. A second stack on the same machine collides on every one."
    )


def test_no_container_name_is_a_literal() -> None:
    """The collision a port variable does not solve.

    Docker refuses two containers with one name, so even a stack whose ports were all moved could
    not start beside this one: both wanted to be `aira-postgres`. `${AIRA_STACK:-aira}` prefixes
    every name, which also gives the second stack a legible identity in `docker ps` rather than a
    generated suffix.
    """
    literal = re.findall(r"^\s+container_name:\s*(aira[-\w]*)\s*$", _wiring(), re.M)

    assert not literal, (
        f"These container names are fixed: {sorted(set(literal))}. Docker refuses a duplicate "
        "name, so a second stack cannot start beside this one whatever its ports are."
    )
