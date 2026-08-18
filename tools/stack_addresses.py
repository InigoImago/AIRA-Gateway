"""Where the local stack answers — resolved once, from the variables that publish it.

**One variable moves a port, and everything that talks to that port follows.** On 2026-08-18 the
fourteen published ports in Compose became `${AIRA_PUBLISH_…_PORT:-<today's value>}`, because a
second system on the same machine had collided with them and the only way out was editing the
Compose file. That fixed the *stack*. It did not fix anything that talks to the stack: the
Makefile carried twenty literal `http://localhost:8001`-shaped addresses, `tools/` five more,
`tests/integration/` a dozen, `e2e/` a handful. Move a port to dodge a collision and the stack
comes up correctly while `make showcase` waits forever on the old one, `make test-integration`
fails as though nothing were running, and the error names none of it.

That is the same defect the console had the day before — an address stated in three mechanisms, so
changing it meant finding all three — and the fix is the same: **one place decides, everybody
asks.** This module is that place for Python; `e2e/stack-addresses.ts` is its counterpart for the
browser tests, and `tools/tests/test_one_owner_for_the_stack_addresses.py` fails when a fifth
literal appears anywhere.

Resolution order, and it is Compose's own so that a value never means two different things
depending on who read it:

1. the environment — `AIRA_PUBLISH_GATEWAY_PORT=9001 make test-integration` works;
2. `deploy/compose/.env`, which is what `docker compose` itself would read next;
3. the default written in the Compose file, which is today's value.

`AIRA_BIND_HOST` is honoured the same way, with one correction that has to live here rather than
at fifty call sites: the bind address `0.0.0.0` means *every interface* to a listener and is not a
valid address to **connect** to. A stack published on `0.0.0.0` is reached at `localhost`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "deploy" / "compose" / ".env"
COMPOSE_FILES = (
    ROOT / "deploy" / "compose" / "docker-compose.yml",
    ROOT / "deploy" / "compose" / "docker-compose.apps.yml",
)

#: Every service this repository publishes, and the variable that decides its host port. The
#: defaults are **not** written here — they are read from the Compose files below, so that this
#: module cannot drift from the thing it describes. A fifteenth service added to Compose with a
#: `AIRA_PUBLISH_…` port and not added here is caught by the guard test, which compares both ways.
PUBLISHED = {
    "gateway": "AIRA_PUBLISH_GATEWAY_PORT",
    "management": "AIRA_PUBLISH_MANAGEMENT_PORT",
    "console": "AIRA_PUBLISH_FRONTEND_PORT",
    "keycloak": "AIRA_PUBLISH_KEYCLOAK_PORT",
    "keycloak_health": "AIRA_PUBLISH_KEYCLOAK_HEALTH_PORT",
    "postgres": "AIRA_PUBLISH_POSTGRES_PORT",
    "redis": "AIRA_PUBLISH_REDIS_PORT",
    "kafka": "AIRA_PUBLISH_KAFKA_PORT",
    "schema_registry": "AIRA_PUBLISH_SCHEMA_REGISTRY_PORT",
    "vault": "AIRA_PUBLISH_VAULT_PORT",
    "grafana": "AIRA_PUBLISH_GRAFANA_PORT",
    "otlp_grpc": "AIRA_PUBLISH_OTLP_GRPC_PORT",
    "otlp_http": "AIRA_PUBLISH_OTLP_HTTP_PORT",
    "ollama": "AIRA_PUBLISH_OLLAMA_PORT",
}


def _env_file_values(path: Path | None = None) -> dict[str, str]:
    """`KEY=value` lines from the Compose env file, which is where `docker compose` looks next.

    Absent is not empty and both are fine: a developer who never copied `.env.example` still gets
    the Compose defaults, which is exactly what their stack is running on.
    """
    path = path or ENV_FILE
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolution_chain() -> dict[str, tuple[list[str], str]]:
    """Per published service: the variables Compose consults, in order, and its written default.

    **The chain, not just the outermost name.** Compose writes
    ``${AIRA_PUBLISH_GATEWAY_PORT:-${AIRA_GATEWAY_PORT:-8001}}`` for the three application services
    — the inner name is the one that existed before the `AIRA_PUBLISH_` family and is still what
    `docs/SETUP.md` tells a reader to set. Reading only the outer name made this module disagree
    with Compose for exactly those three: the stack published on the port the reader chose and
    every tool went on asking for 8001, which is the defect this file exists to prevent, committed
    by the file itself.

    Parsed rather than restated, for the same reason the defaults are: a chain written down here
    would drift, and it would drift silently in the direction where only the tools are wrong.
    """
    chains: dict[str, tuple[list[str], str]] = {}
    outer = re.compile(r"\$\{(AIRA_PUBLISH_[A-Z_]+):-(.*?)\}\}?\"?:")
    for compose in COMPOSE_FILES:
        if not compose.exists():
            continue
        for line in compose.read_text().splitlines():
            match = outer.search(line)
            if not match:
                continue
            name, rest = match.group(1), match.group(2)
            names = [name, *re.findall(r"\$\{([A-Z_]+):-", rest)]
            digits = re.findall(r"(\d+)", rest)
            if digits:
                chains.setdefault(name, (names, digits[-1]))
    return chains


def port(service: str) -> int:
    """The host port `service` is published on, by Compose's own resolution order.

    Environment first, then `deploy/compose/.env`, then Compose's literal default — and *within*
    each of those, the same chain of variable names Compose itself consults, outermost first.
    """
    variable = PUBLISHED[service]
    chains = _resolution_chain()
    if variable not in chains:
        raise KeyError(
            f"{variable} is not published with a default in the Compose files, so nothing can say "
            f"which port '{service}' is on. Give it one, or remove it from PUBLISHED."
        )
    names, default = chains[variable]
    from_file = _env_file_values()
    for name in names:
        for source in (os.environ, from_file):
            value = source.get(name)
            if value:
                return int(value)
    return int(default)


def host() -> str:
    """The address to **connect** to, which is not always the address Compose binds.

    `AIRA_BIND_HOST=0.0.0.0` publishes on every interface — a listener instruction. Handing it to
    a client is a connection to nowhere on some stacks and to a surprising interface on others, so
    it resolves to `localhost` here, once, rather than being got right at each call site.
    """
    bound = os.environ.get("AIRA_BIND_HOST") or _env_file_values().get("AIRA_BIND_HOST") or ""
    if not bound or bound in {"0.0.0.0", "::", "[::]"}:  # noqa: S104 - compared, not bound
        return "localhost"
    return bound


def url(service: str, scheme: str = "http") -> str:
    """`http://<host>:<port>` for a published service, with no trailing slash."""
    return f"{scheme}://{host()}:{port(service)}"


def netloc(service: str) -> str:
    """`<host>:<port>`, for the clients that take an address rather than a URL — Kafka, Postgres."""
    return f"{host()}:{port(service)}"


def as_make() -> str:
    """Every address on one line, as `name=value` pairs, for a Makefile to slice up.

    One line and one process, because this is evaluated at **parse** time on every `make`
    invocation — including `make help`. Fourteen separate calls would put several seconds in front
    of every target, and a developer who pays that on `make help` starts working around the
    Makefile. It is written to be run by plain `python3`: this module imports nothing outside the
    standard library precisely so that no dependency resolver sits on that path.
    """
    pairs = [f"{name}={url(name)}" for name in PUBLISHED]
    pairs += [f"{name}.netloc={netloc(name)}" for name in PUBLISHED]
    return " ".join(pairs)


if __name__ == "__main__":  # pragma: no cover - the Makefile's entry point
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "make":
        print(as_make())
    else:
        what, service = sys.argv[1], sys.argv[2]
        print(url(service) if what == "url" else netloc(service))
