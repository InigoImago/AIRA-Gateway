"""The core Compose file is the product, and nothing in it is there for the demo.

**Why this is a test and not a convention.** On 2026-08-26 `docker-compose.apps.yml` was 625
lines, and about a third of it existed for the showcase: a development Keycloak realm, a `-dev`
Vault refilled on every start, five seeded demo accounts. Somebody deploying AIRA onto their own
Keycloak, Vault and Postgres had to read the whole thing and work out which half applied to them.
The split moved the demo into `docker-compose.showcase.yml`.

A split like that survives exactly as long as the next person resists adding "just one" demo
service back where the YAML anchors happen to be — which is a real pull, because the anchors live
in the core file and a new demo service needs them. `management-seed` shows the answer: `extends`
reaches across files, so the anchors are not a reason to move back.

The rules below are the ones that would let it rot:

1. no demo service in the core file;
2. no core service depending on a demo service — that edge belongs in the showcase file, where
   Compose merges it in, and putting it in the core file makes the core stack unstartable alone;
3. the core stack must be a valid project by itself — the property everything else is for.
"""

from __future__ import annotations

import pathlib
import re

import compose_files
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Read from the files rather than listed here, so a new service is covered the day it is added.
KNOWN_CONTAINER_SUFFIXES = {
    name
    for path in (
        ROOT / "deploy" / "compose" / "docker-compose.yml",
        ROOT / "deploy" / "compose" / "docker-compose.apps.yml",
        ROOT / "deploy" / "compose" / "docker-compose.showcase.yml",
    )
    for name in re.findall(r"^  ([a-z][a-z0-9-]*):$", path.read_text(), re.M)
}


def _services(path: pathlib.Path) -> dict[str, dict]:
    return yaml.safe_load(path.read_text()).get("services") or {}


def test_the_core_file_defines_no_demo_service() -> None:
    core = _services(compose_files.APPS)
    strays = sorted(set(compose_files.DEMO_ONLY) & set(core))
    assert not strays, (
        f"{strays} are demo provisioning and belong in {compose_files.SHOWCASE_ONLY.name}. The "
        "core file is what somebody deploys onto their own infrastructure; every service in it "
        "that they do not need is a paragraph they have to read to find that out."
    )


def test_the_showcase_file_defines_all_of_them() -> None:
    """The mirror. A name removed from `DEMO_ONLY` would silence the test above by deleting the
    rule rather than by satisfying it, so the list is checked against reality from both sides."""
    showcase = _services(compose_files.SHOWCASE_ONLY)
    missing = sorted(set(compose_files.DEMO_ONLY) - set(showcase))
    assert not missing, f"{missing}: named as demo-only and defined nowhere"


def test_no_core_service_depends_on_demo_provisioning() -> None:
    """The edge is the coupling, not the service.

    `gateway-migrate` and `management-migrate` waited on `vault-init`. Written in the core file
    that edge makes the core stack refuse to start on its own — Compose rejects a project whose
    `depends_on` names a service no active file defines. Written in the showcase file it is
    additive: Compose merges `depends_on` across `-f` files, so the wait exists exactly when the
    demo provisioning does.
    """
    offenders: list[str] = []
    for name, body in _services(compose_files.APPS).items():
        for target in body.get("depends_on") or {}:
            if target in compose_files.DEMO_ONLY:
                offenders.append(f"{name} -> {target}")
    assert not offenders, (
        f"{offenders}: a core service waiting on demo provisioning. Move the edge to "
        f"{compose_files.SHOWCASE_ONLY.name}, where compose merges it in when the demo is layered "
        "on — the core stack must start without it."
    )


def test_the_core_stack_names_only_services_it_defines() -> None:
    """`docker compose config` is the real judge; this is the same question without a daemon.

    Every `depends_on` target in the two core files must be defined by one of them. A stray edge
    is not a warning in Compose — the whole project is rejected, and the message names one
    service while the stack that will not start is all of them.
    """
    defined: set[str] = set()
    edges: list[tuple[str, str]] = []
    for path in compose_files.CORE:
        for name, body in _services(path).items():
            defined.add(name)
            edges.extend((name, target) for target in (body.get("depends_on") or {}))
    dangling = sorted({f"{a} -> {b}" for a, b in edges if b not in defined})
    assert not dangling, f"{dangling}: named by the core stack and defined by no core file"


def test_the_showcase_file_only_adds() -> None:
    """A service the showcase file *redefines* is a second definition to keep in step.

    It may name a core service to bolt an edge on — that is the whole mechanism — but if it
    starts restating `image`, `environment` or `command`, the two files have begun to disagree
    about the same container and nothing will say which one is current.
    """
    core = set(_services(compose_files.APPS)) | set(_services(compose_files.INFRA))
    redefined: list[str] = []
    for name, body in _services(compose_files.SHOWCASE_ONLY).items():
        if name in core and set(body) - {"depends_on", "profiles"}:
            redefined.append(f"{name}: {sorted(set(body) - {'depends_on', 'profiles'})}")
    assert not redefined, (
        f"{redefined}: the showcase file may add a dependency to a core service, not restate it"
    )


def test_the_showcase_layer_adds_its_edge_without_losing_the_core_ones() -> None:
    """**The mechanism the whole split rests on**, asserted rather than assumed.

    Moving `vault-init` out was only safe because Compose merges `depends_on` *per key* across
    `-f` files: the core file says the migrations wait for Postgres, the showcase file says they
    also wait for the dev-Vault provisioning, and both hold. If the merge replaced the mapping
    instead, layering the showcase on would silently drop the Postgres edge — the migrations
    would start against a database that is not up yet, intermittently, on a cold start only.

    Read from the files rather than from `docker compose config`, so this runs without a daemon.
    """
    merged: dict[str, dict] = {}
    for path in compose_files.SHOWCASE:
        for service, body in _services(path).items():
            existing = merged.setdefault(service, {})
            for key, value in (body or {}).items():
                if isinstance(value, dict) and isinstance(existing.get(key), dict):
                    existing[key] = {**existing[key], **value}
                else:
                    existing[key] = value

    for job in ("gateway-migrate", "management-migrate"):
        edges = set(merged[job].get("depends_on") or {})
        assert {"postgres", "vault-init"} <= edges, (
            f"{job} waits for {sorted(edges)}. The core file's Postgres edge and the showcase "
            "file's vault-init edge must both survive the merge; losing either is a cold-start "
            "race that no warm machine will ever show you."
        )


def test_a_second_stack_is_isolated_by_one_variable() -> None:
    """`AIRA_STACK` must namespace **everything**, or a second stack is worse than none.

    It already prefixes every `container_name`, which reads as "set this and run a stack beside
    the existing one". The project name and the network did not follow, and a fixed network name
    is shared by every Compose project on the machine: both stacks join one bridge, and
    `postgres`, `kafka` and `keycloak` resolve to whichever container answers first.

    The failure is silent and total — measured, not imagined. A second stack came up healthy with
    every container reporting its own name, its own database stayed empty, and its seed reported
    success against the *first* stack's Postgres. Nothing in either stack said anything was wrong.
    """
    compose = compose_files.INFRA.read_text()
    # Anchored at the start of a line, both of them. Written without the leading newline, the
    # project's check matched the *network's* indented line as a substring and the mutation that
    # pins the project name back to a literal went unnoticed — a guard that reads its own
    # neighbour's evidence.
    for line, what in (
        ("\nname: ${AIRA_STACK:-aira}\n", "the compose project"),
        ("\n    name: ${AIRA_STACK:-aira}\n", "the network"),
    ):
        assert line in compose, (
            f"{what} does not follow AIRA_STACK. Every container_name already does, so a second "
            "stack looks isolated and shares a bridge with the first — where one service name "
            "answers for two containers."
        )

    prefixed = compose.count("container_name: ${AIRA_STACK:-aira}-")
    named = compose.count("container_name:")
    assert prefixed == named, f"{named - prefixed} container(s) do not follow AIRA_STACK"


def test_nothing_addresses_a_container_by_a_name_aira_stack_could_move() -> None:
    """`AIRA_STACK` moves every container name, so anything naming one has to move with it.

    The compose files follow it. The Makefile did not: `make showcase` waits for the model pull
    with `docker inspect … aira-ollama-pull`, and on a stack with a prefix that container does not
    exist. `docker inspect` then fails, the fallback says `gone`, the loop breaks on its first
    iteration, and the seed runs against models that are still downloading — which is precisely
    the failure the comment above that loop exists to prevent, reintroduced by the one literal
    underneath it.

    Silent, again: a demo with no models in it is a demo that starts.
    """
    offenders: list[str] = []
    for path in (ROOT / "Makefile", *compose_files.ALL):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("@#"):
                continue
            # Not followed by `:` — `image: aira-gateway:${AIRA_IMAGE_TAG:-dev}` is an *image*
            # name, and images are shared build artifacts that deliberately do not move with the
            # stack. A container reference is followed by whitespace or the end of the word.
            for match in re.finditer(r"(?<![\w${:-])aira-([a-z][a-z0-9-]*)(?![:\w-])", line):
                if match.group(1) in KNOWN_CONTAINER_SUFFIXES:
                    offenders.append(f"{path.name}:{number}: {stripped[:80]}")
    assert not offenders, (
        f"{offenders}: a container addressed by a literal name. Write "
        "`${AIRA_STACK:-aira}-<service>` so it follows the variable that moves it."
    )
