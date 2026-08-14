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

import pathlib
import re

from aira_common.models import Capability

MODELS_TS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "management"
    / "frontend"
    / "src"
    / "app"
    / "core"
    / "api"
    / "models.ts"
)


def _console_capabilities() -> set[str]:
    """The values in `CAPABILITIES` in the SPA's model definitions.

    Parsed rather than imported, for the reason `test_declared_dependencies` gives: no Python
    environment can run TypeScript, and a test that needs one is a test that gets skipped.
    """
    source = MODELS_TS.read_text()
    match = re.search(r"export const CAPABILITIES[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert match, "CAPABILITIES is no longer an array literal in models.ts"
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


def test_the_realm_defines_exactly_the_roles_that_exist() -> None:
    """The dev realm and the code must name the same roles.

    `ADR-0017` abolished `use-case-admin` and `use-case-user`; they stayed in the `Role` enum, in
    the seed, in the console's specs **and in the realm file** for a day afterwards. A realm role
    nothing reads is a role somebody can be assigned in Keycloak with no effect anywhere — the
    plainest version of a control that looks present and is absent.

    Compared in both directions, the answer this repository keeps arriving at: a role in the code
    that the realm cannot confer is unreachable, and a role in the realm that the code does not
    know is a promise nothing keeps.
    """
    import json

    from aira_common.roles import ALL_ROLES

    realm_file = (
        pathlib.Path(__file__).resolve().parents[2]
        / "deploy"
        / "compose"
        / "keycloak"
        / "realms"
        / "aira-realm.json"
    )
    realm = json.loads(realm_file.read_text())
    defined = {role["name"] for role in realm["roles"]["realm"]}
    known = {role.value for role in ALL_ROLES}

    assert defined == known, (
        f"realm-only: {sorted(defined - known)}, code-only: {sorted(known - defined)}"
    )


def _console_list(name: str, pattern: str = r"'([a-z0-9_/.+-]+)'") -> set[str]:
    """The values of one array literal in the SPA's model definitions."""
    source = MODELS_TS.read_text()
    match = re.search(rf"export const {name}[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert match, f"{name} is no longer an array literal in models.ts"
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
        / "management/frontend/src/app/features/pipelines/pipeline-editor.ts"
    ).read_text()
    offered = re.search(r"stepTypes: StepType\[\] = \[([^\]]*)\]", editor)
    assert offered, "stepTypes is no longer an array literal in the pipeline editor"
    console = set(re.findall(r"'([a-z_]+)'", offered.group(1)))

    assert gateway == management, (
        f"gateway-only: {sorted(gateway - management)}, "
        f"management-only: {sorted(management - gateway)}"
    )
    assert gateway == console, (
        f"gateway-only: {sorted(gateway - console)}, console-only: {sorted(console - gateway)}"
    )
