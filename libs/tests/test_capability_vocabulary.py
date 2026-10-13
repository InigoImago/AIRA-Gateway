"""The console can declare every capability the gateway enforces, and no others.

`Capability` is a **closed vocabulary** (`ADR-0012`): flags say *whether*, never *how*, and
undeclared means unsupported. The Angular console restates that vocabulary as a TypeScript array,
because a checkbox list has to come from somewhere — and a restatement in a second language is the
shape this codebase keeps getting caught by. `FRD-206` (the console answering questions only the
server can answer), `FRD-602` (a second entry point that forgot the scope rule) and `ADR-0015` (a
membership rule written twice, so one copy was inverted) are all the same defect.

Here it had already happened twice without anybody noticing, and it is **silent in the worst
direction**: `tools` shipped with `FRD-131` and `prompt_caching` with `FRD-133`, and neither was
in the console's list. A Global Administrator could price a model for prompt caching and had no
way to declare that it caches — the capability with no way in, which announces itself through
nothing at all, because absence of a checkbox looks exactly like a design decision.

So the two lists are compared **in both directions**, the same answer this repository has reached
three times before (`aira.rate-limits`, `aira.anomaly-rules`, `use_case_group.granted`): a value the
gateway enforces that the console cannot set is a feature nobody can reach, and a value the console
offers that the gateway does not know is a promise nothing keeps.
"""

from __future__ import annotations

import json
import pathlib
import re

from aira_common.models import Capability

REPO = pathlib.Path(__file__).resolve().parents[2]
REALM = REPO / "deploy" / "compose" / "keycloak" / "realms" / "aira-realm.json"
ENV_EXAMPLE = REPO / "deploy" / "compose" / ".env.example"

MODELS_TS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "management"
    / "frontend"
    / "src"
    / "app"
    / "core"
    / "api"
    / "types"
    / "catalog.ts"
)


def _console_capabilities() -> set[str]:
    """The values in `CAPABILITIES` in the SPA's model definitions.

    Parsed rather than imported, for the reason `test_declared_dependencies` gives: no Python
    environment can run TypeScript, and a test that needs one is a test that gets skipped.
    """
    source = MODELS_TS.read_text()
    match = re.search(r"export const CAPABILITIES[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert match, "CAPABILITIES is no longer an array literal in types/catalog.ts"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_console_can_declare_every_capability_the_gateway_enforces() -> None:
    missing = {member.value for member in Capability} - _console_capabilities()

    assert not missing, (
        f"the gateway enforces {sorted(missing)} and the console offers no way to declare it — a "
        "model can be priced for a capability it can never be given, and nothing anywhere fails"
    )


def test_the_console_offers_nothing_the_gateway_does_not_know() -> None:
    """The other direction, which fails differently: a checkbox the gateway ignores is a
    declaration an administrator believes they made."""
    unknown = _console_capabilities() - {member.value for member in Capability}

    assert not unknown, f"the console offers {sorted(unknown)}, which no model can be judged by"


def test_a_capability_this_version_cannot_read_is_dropped_rather_than_raising() -> None:
    """A newer Management may declare a capability this gateway has never heard of — in a shape it
    has never seen. Dropping it is the contract (`parse_capabilities`), because the gateway could
    not enforce it anyway; raising on an object failed the whole `model.upserted` event instead, so
    the model's other fields were never applied either."""
    from aira_common.models import parse_capabilities

    declared = ["tools", {"name": "speech"}, ["embed"], 7, "brand_new"]
    assert parse_capabilities(declared) == frozenset({Capability.TOOLS})


def _realm_group_paths(nodes: list[dict], prefix: str = "") -> set[str]:
    """Every group path the realm defines, as a token would carry it."""
    found: set[str] = set()
    for node in nodes:
        path = f"{prefix}/{node['name']}"
        found |= {path, *_realm_group_paths(node.get("subGroups", []), path)}
    return found


def _env_example(name: str) -> str:
    """One variable's value in the stack's `.env.example`, which `make env` copies."""
    for line in ENV_EXAMPLE.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip()
    raise AssertionError(f"{name} is not in {ENV_EXAMPLE.name}")


def test_every_role_is_conferred_by_a_group_the_realm_defines() -> None:
    """A role is held through a group and through nothing else (`ADR-0017`): neither plane reads
    `realm_access.roles`. So what the dev realm owes the code is a **group** for every role, not a
    realm role — one of those is a role somebody can be assigned in Keycloak with no effect
    anywhere, the plainest version of a control that looks present and is absent.

    Compared in both directions, through the parser both planes use: the stack's
    `AIRA_ROLE_GROUPS` names every role, and every group it names is one the realm defines —
    a mapping to a group that does not exist confers the role on nobody. And no account holds an
    AIRA role as a realm role, which would read as that role in Keycloak's console and grant
    nothing here.
    """
    from aira_common.roles import ALL_ROLES, parse_role_groups

    realm = json.loads(REALM.read_text())
    mapping = parse_role_groups(_env_example("AIRA_ROLE_GROUPS"))

    unconferred = sorted(str(role) for role in set(ALL_ROLES) - set(mapping))
    assert not unconferred, f"no group confers {unconferred}"
    groups = _realm_group_paths(realm.get("groups", []))
    missing = sorted(path for paths in mapping.values() for path in paths if path not in groups)
    assert not missing, f"AIRA_ROLE_GROUPS names groups the dev realm does not define: {missing}"

    named = {role.value for role in ALL_ROLES}
    holders = sorted(
        user["username"]
        for user in realm.get("users", [])
        if named & set(user.get("realmRoles") or [])
    )
    assert not holders, f"{holders} hold an AIRA role as a realm role, which grants nothing"


def _console_list(name: str, pattern: str = r"'([a-z0-9_/.+-]+)'") -> set[str]:
    """The values of one array literal in the SPA's model definitions."""
    source = MODELS_TS.read_text()
    match = re.search(rf"export const {name}[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert match, f"{name} is no longer an array literal in types/catalog.ts"
    return set(re.findall(pattern, match.group(1)))


def test_the_console_can_declare_every_thinking_mode() -> None:
    """The modes are a checkbox list, so they had to be restated — and a restated list drifts.

    A mode the console cannot tick is a mode nobody can declare from the console, which reads as
    "this model does not offer it" rather than as "there is no box". The capability list was
    missing two members for days on exactly that logic.
    """
    from aira_common.models import ThinkingMode

    console = _console_list("THINKING_MODES")
    known = {member.value for member in ThinkingMode}

    assert console == known, (
        f"console-only: {sorted(console - known)}, gateway-only: {sorted(known - console)}"
    )


def test_the_console_offers_exactly_the_media_types_the_gateway_accepts() -> None:
    """Both directions, and each fails differently.

    A type the gateway accepts and the console cannot tick is a document nobody can declare a
    model able to read — the model is then refused by name for a file it could have handled. A type
    the console offers and the gateway does not accept is worse: the declaration saves, the model
    looks capable, and the request is refused before any model is consulted, with a message about
    the media type rather than about the box somebody ticked.
    """
    from aira_gateway.attachments import DEFAULT_MEDIA_TYPES

    console = _console_list("MEDIA_TYPES")

    assert console == set(DEFAULT_MEDIA_TYPES), (
        f"console-only: {sorted(console - set(DEFAULT_MEDIA_TYPES))}, "
        f"gateway-only: {sorted(set(DEFAULT_MEDIA_TYPES) - console)}"
    )


def test_every_pipeline_step_exists_in_all_three_places() -> None:
    """The gateway runs it, Management validates it, the console offers it — one vocabulary.

    Three lists, so three chances to drift, and each absence fails differently: a step the console
    cannot offer looks exactly like a step that does not exist; one Management refuses cannot be
    saved after being built; one the gateway does not know is dropped from a stored config and the
    use case quietly loses a control it can still see in the builder.

    Compared in **both** directions, the answer this repository keeps arriving at.
    """
    import re

    from aira_gateway.pipeline.config import StepType

    gateway = {member.value for member in StepType}

    serializer = (
        pathlib.Path(__file__).resolve().parents[2]
        / "management/backend/src/aira_management/apps/pipelines/serializers.py"
    ).read_text()
    match = re.search(r"STEP_TYPES = \{([^}]*)\}", serializer)
    assert match, "STEP_TYPES is no longer a set literal in the pipeline serializer"
    management = set(re.findall(r'"([a-z_]+)"', match.group(1)))

    editor = (
        pathlib.Path(__file__).resolve().parents[2]
        / "management/frontend/src/app/features/pipelines/pipeline-steps.ts"
    ).read_text()
    offered = re.search(r"STEP_TYPES: StepType\[\] = \[([^\]]*)\]", editor)
    assert offered, "STEP_TYPES is no longer an array literal in the pipeline builder's steps"
    console = set(re.findall(r"'([a-z_]+)'", offered.group(1)))

    assert gateway == management, (
        f"gateway-only: {sorted(gateway - management)}, "
        f"management-only: {sorted(management - gateway)}"
    )
    assert gateway == console, (
        f"gateway-only: {sorted(gateway - console)}, console-only: {sorted(console - gateway)}"
    )
