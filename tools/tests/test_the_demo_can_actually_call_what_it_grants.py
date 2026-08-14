"""The seed's members and the realm's groups are one list written twice.

Reported from the console: *"I tested with the use-case admin and the global admin on the Coding
use case — the dry run works for neither."* Measured before anything was changed, and every part of
it was true:

- `usecases_usecasemembership` held three rows for `coding-assistant`;
- `use_case_groups` in the **gateway** held none for it, and the realm had no
  `/use-cases/coding-assistant` group at all;
- so the console — which asks Management's `is_member` — showed all three as members, and the
  gateway, which reads a token's Keycloak groups, refused every one of them.

`personalwesen` had the second version of the same thing: the group existed and **nobody was in
it**, while the seed named `admin` as its administrator.

The two lists are `MEMBERSHIPS` in the showcase seed and `users[].groups` in the realm file, and
nothing compared them. That is this repository's most repeated shape — a hand-written list with no
counterpart — and the rule it has already paid for is: **compare in both directions**. A member the
realm cannot serve is a promise the demo breaks; a group naming a use case the seed does not create
is a grant to nothing.

Deliberately a *file* comparison and not a live one: it has to fail in CI on the change that
introduces it, not on the machine where somebody finally runs the demo.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
REALM = ROOT / "deploy" / "compose" / "keycloak" / "realms" / "aira-realm.json"
SEED = (
    ROOT
    / "management"
    / "backend"
    / "src"
    / "aira_management"
    / "apps"
    / "seed"
    / "contributions"
    / "showcase.py"
)

#: The gateway resolves a group path to a use case two ways (`aira_common.access`): this
#: convention, which needs no configuration at all, and a group grant replicated over Kafka. The
#: demo uses the convention, so the check can read the realm file alone.
CONVENTION = "/use-cases/"


def _realm() -> dict:
    return json.loads(REALM.read_text())


def _seeded_memberships() -> dict[str, set[str]]:
    """``{use case: {username, …}}`` from the seed's own declaration.

    Parsed rather than imported: importing it needs Django settings, a database and the whole
    control plane, and this is a question about two text files.
    """
    source = SEED.read_text()
    match = re.search(r"^MEMBERSHIPS: [^=]+= (\{.*?^\})", source, re.S | re.M)
    assert match, (
        "MEMBERSHIPS is no longer a literal in the showcase seed — move this check with it"
    )

    # `UseCaseMembership.ADMIN` and friends are attribute references, so the literal is read
    # structurally instead of `eval`-ed: only the usernames matter here.
    tree = ast.parse(match.group(1).strip(), mode="eval")
    assert isinstance(tree.body, ast.Dict)
    out: dict[str, set[str]] = {}
    for key, value in zip(tree.body.keys, tree.body.values, strict=True):
        assert isinstance(key, ast.Constant) and isinstance(value, ast.List)
        names: set[str] = set()
        for pair in value.elts:
            assert isinstance(pair, ast.Tuple)
            first = pair.elts[0]
            assert isinstance(first, ast.Constant)
            names.add(str(first.value))
        out[str(key.value)] = names
    return out


def _held() -> dict[str, set[str]]:
    """``{use case: {username, …}}`` that the **realm** grants through the convention."""
    out: dict[str, set[str]] = {}
    for user in _realm().get("users", []):
        for path in user.get("groups", []) or []:
            if path.startswith(CONVENTION):
                out.setdefault(path[len(CONVENTION) :], set()).add(user["username"])
    return out


def _group_paths() -> set[str]:
    def walk(nodes: list[dict], prefix: str = "") -> set[str]:
        found: set[str] = set()
        for node in nodes:
            path = f"{prefix}/{node['name']}"
            found.add(path)
            found |= walk(node.get("subGroups", []) or [], path)
        return found

    return walk(_realm().get("groups", []) or [])


def test_the_lists_are_not_empty() -> None:
    """A guard on the guard. Both sides are parsed out of files, and a parser that quietly returns
    nothing turns every assertion below into a comparison of two empty sets — this repository has
    shipped two guards that could not fail."""
    seeded = _seeded_memberships()
    assert len(seeded) >= 4, seeded
    assert "coding-assistant" in seeded, "the use case this check was written for is gone"
    assert len(_held()) >= 4, _held()


def test_everybody_the_seed_makes_a_member_can_reach_it_from_their_token() -> None:
    """The reported defect. A Django row is what the *console* reads; a Keycloak group is what the
    *gateway* reads, and only the second one can serve a request."""
    held = _held()
    unreachable = {
        slug: sorted(names - held.get(slug, set()))
        for slug, names in _seeded_memberships().items()
        if names - held.get(slug, set())
    }

    assert not unreachable, (
        "the showcase seeds these people as members, and the demo realm puts them in no group that "
        f"reaches the use case, so the gateway refuses them: {unreachable}\n\n"
        "The console shows them as members either way — `is_member` is Management's answer and the "
        "gateway has its own. Add the person to `/use-cases/<slug>` in the realm file (and add the "
        "group, if it is not there)."
    )


def test_a_use_case_group_names_a_use_case_the_demo_creates() -> None:
    """The other direction, which is the half that gets skipped. A group granting access to a use
    case nobody seeds is a grant to nothing — harmless until somebody reads it as evidence that the
    use case exists."""
    seeded = set(_seeded_memberships())
    # Not seeded by `MEMBERSHIPS` and deliberately so: created by the integration/e2e suites and by
    # the smoke-test feature, which books its runs to a use case of its own (`FRD-504`).
    fixtures = {"demo-uc", "other-uc", "smoke-test"}
    granted = {path[len(CONVENTION) :] for path in _group_paths() if path.startswith(CONVENTION)}

    orphans = sorted(granted - seeded - fixtures)
    assert not orphans, (
        f"the realm grants access to use cases the showcase never creates: {orphans}. Either seed "
        "them, name them in the fixture list above, or drop the group."
    )
