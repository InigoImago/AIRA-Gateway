"""The showcase does not ask a use case for something its own release forbids.

Found on 2026-08-11, on the first `make showcase` run from an empty machine: the demo reported
**"served 9, refused 2"** where its own record says ten and one. The extra refusal was an embedding
batch sent to `entwicklung` — the use case the seed narrows to the chat model *on purpose*, to
demonstrate `FRD-308`. The demo was breaking the governance rule it exists to show.

It had been doing so for as long as both existed. Nothing noticed, because `:embedContent` reached
the provider **without the release being consulted at all** — the third instance of that bypass.
The moment the control was applied to every verb, the seed's own contradiction became a visible
refusal in front of whoever was watching.

So the property is not "the demo passes". It is **the demo's traffic and the demo's governance
agree**, checked by reading both rather than by running either: a stakeholder walkthrough is
exactly the wrong place to discover that they do not, and a run that needs the whole stack up is
exactly the check nobody performs before a demo.

Deliberately narrow. This does not verify that the models exist, are approved, or answer — the
live suites do that. It verifies one thing that is decidable from the source: **if the demo sends
a request as use case X, X must be allowed to serve it.**
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "management/backend/src/aira_management/apps/seed/contributions/showcase_data.py"
TRAFFIC = ROOT / "tools/demo_traffic.py"


def _constant(source: str, name: str) -> ast.expr | None:
    """The value assigned to a module-level name, as an AST node."""
    for node in ast.parse(source).body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value if not isinstance(node, ast.AnnAssign) else node.value
    return None


def _releases() -> dict[str, list[str]]:
    """`showcase.RELEASES`, with the model constants resolved to their default values.

    Read from the source rather than imported: importing the seed pulls in Django settings and a
    database, which is a heavy dependency for a question that is answerable from the text — and a
    check that needs the stack up is one nobody runs before a demo.
    """
    source = SEED.read_text()
    chat = re.search(r'CHAT_MODEL = os\.environ\.get\([^,]+,\s*"([^"]+)"\)', source)
    assert chat is not None, "CHAT_MODEL is not written the way this test reads it"

    node = _constant(source, "RELEASES")
    assert isinstance(node, ast.Dict), "RELEASES is not a literal dict any more"

    resolved: dict[str, list[str]] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        assert isinstance(key, ast.Constant), "a use case slug must be a literal"
        assert isinstance(value, ast.List), "a release must be a literal list"
        names: list[str] = []
        for entry in value.elts:
            if isinstance(entry, ast.Constant):
                names.append(str(entry.value))
            elif isinstance(entry, ast.Name) and entry.id == "CHAT_MODEL":
                names.append(chat.group(1))
            else:  # pragma: no cover - a new kind of entry should fail loudly, not silently pass
                raise AssertionError(f"unrecognised entry in RELEASES[{key.value!r}]")
        resolved[str(key.value)] = names
    return resolved


def _memberships() -> dict[str, list[str]]:
    """`showcase.MEMBERSHIPS`, as names per slug, read from the source like `RELEASES` above."""
    node = _constant(SEED.read_text(), "MEMBERSHIPS")
    assert isinstance(node, ast.Dict), "MEMBERSHIPS is not a literal dict any more"

    resolved: dict[str, list[str]] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        assert isinstance(key, ast.Constant), "a use case slug must be a literal"
        assert isinstance(value, ast.List), "a membership list must be a literal list"
        names: list[str] = []
        for entry in value.elts:
            assert isinstance(entry, ast.Tuple), "a membership is a (name, role) pair"
            first = entry.elts[0]
            assert isinstance(first, ast.Constant), "a member's name must be a literal"
            names.append(str(first.value))
        resolved[str(key.value)] = names
    return resolved


def _callers() -> dict[str, list[str | None]]:
    """`demo_traffic.CALLERS` — who each use case's traffic is sent as."""
    node = _constant(TRAFFIC.read_text(), "CALLERS")
    assert isinstance(node, ast.Dict), "CALLERS is not a literal dict any more"

    resolved: dict[str, list[str | None]] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        assert isinstance(key, ast.Constant), "a use case slug must be a literal"
        assert isinstance(value, ast.Tuple), "a caller list must be a literal tuple"
        people: list[str | None] = []
        for entry in value.elts:
            assert isinstance(entry, ast.Constant), "a caller is a literal name or None"
            people.append(None if entry.value is None else str(entry.value))
        resolved[str(key.value)] = people
    return resolved


def test_the_traffic_only_calls_as_people_the_seed_made_members() -> None:
    """A key is issued per **member** (`FRD-130` FR-4), so naming anybody else derives a key the
    seed never stored and every request made as them answers 401 — which looks like a broken
    gateway and is a mismatch between two files.

    `None` is the use case's own key, which is the first member's and always exists.
    """
    members = _memberships()

    for slug, people in _callers().items():
        assert slug in members, f"the traffic calls '{slug}', which the seed does not create"
        for person in people:
            if person is None:
                continue
            assert person in members[slug], (
                f"the traffic calls '{slug}' as '{person}', who is not a member of it — the seed "
                f"issues no key for them, so every such request is a 401. Members: {members[slug]}"
            )


def test_more_than_one_person_calls_somewhere() -> None:
    """The point of the second key. With one caller everywhere, `FRD-606`'s per-person table and the
    per-head budget on `kundenservice` each have exactly one row — a comparison with nothing to
    compare — and `ucuser` sees an empty *What you used* card."""
    assert any(len(people) > 1 for people in _callers().values()), (
        "every use case's traffic is sent as one person again"
    )


def test_a_partial_401_fails_the_run() -> None:
    """**A credential that does not exist is never a correct answer**, and a *partial* 401 used to
    pass: a run reported `served 8, refused 3` while two of its eleven requests were refused for a
    key the seed should have stored, because 401 was counted in the same column as a budget doing
    its job. 429 and 400 are controls working; 401 is wiring, and nothing after it means anything.
    """
    source = TRAFFIC.read_text()
    summary = source.index('print(f"\\nserved {served}')

    assert "if 401 in codes:" in source[summary:], (
        "the traffic no longer fails on a 401 it did not expect"
    )
    assert source.index("if 401 in codes:", summary) < source.index("if failed:", summary), (
        "the 401 check runs after the 5xx one, so a run with both reports the wrong cause first"
    )


def test_the_wait_probes_every_credential_the_traffic_uses() -> None:
    """The other half of the 401 check above, and the reason it is safe.

    A key reaches the gateway over Kafka, and one arriving says nothing about the next. Probing the
    use case's own key alone was survivable while a 401 merely reduced the figures; now that the
    traffic stops on one, a key still in flight would turn a timing window into a red run.
    """
    source = (ROOT / "tools/demo_wait_ready.py").read_text()

    assert "PROBE_KEYS" in source, "the wait probes a single key again"
    assert "CALLERS[PROBE_USE_CASE]" in source, (
        "the probed keys are no longer derived from the people the traffic actually calls as"
    )
    assert "for key in PROBE_KEYS:" in source, "the wait does not loop over them"


def _embedding_use_case() -> str:
    node = _constant(TRAFFIC.read_text(), "EMBEDDING_USE_CASE")
    assert isinstance(node, ast.Constant), "EMBEDDING_USE_CASE is not a literal any more"
    return str(node.value)


def _embed_model() -> str:
    match = re.search(r'EMBED = os\.environ\.get\([^,]+,\s*"([^"]+)"\)', TRAFFIC.read_text())
    assert match is not None, "EMBED is not written the way this test reads it"
    return match.group(1)


def test_the_two_files_still_say_what_this_test_reads() -> None:
    """The guard's own failure mode. Every assertion below is vacuous if the parsing silently
    returns nothing, and this project has shipped guards that could not fail — twice, both times
    silently green."""
    releases = _releases()

    assert releases, "no releases parsed; the seed's RELEASES has changed shape"
    assert "entwicklung" in releases, "the deliberately narrowed use case is gone from RELEASES"
    # The two parsers added with the second key, held to the same rule: a parser that silently
    # returns nothing makes every assertion built on it pass by describing nothing.
    assert _memberships(), "no memberships parsed; the seed's MEMBERSHIPS has changed shape"
    assert _callers(), "no callers parsed; the traffic's CALLERS has changed shape"
    assert _embedding_use_case()
    assert _embed_model()


def test_the_embedding_batch_goes_to_a_use_case_that_may_embed() -> None:
    """The defect itself: the demo asked a narrowed use case to do the one thing it may not."""
    slug = _embedding_use_case()
    released = _releases().get(slug)

    # `None` means "not in RELEASES", which the seed treats as *every approved model* — allowed,
    # and the state `kundenservice` is in. A named list has to contain the embedding model.
    assert released is None or _embed_model() in released, (
        f"the demo sends its embedding batch as '{slug}', which is released {released} — "
        f"'{_embed_model()}' is not among them, so the gateway will refuse it (`FRD-308`). "
        "Either send it as a use case that may embed, or release the model to this one. Do not "
        "widen the release just to make the demo pass: the narrowing is what the demo shows."
    )


def test_the_narrowed_use_case_is_still_narrowed() -> None:
    """The other half, and the reason the fix went into the traffic rather than the seed.

    Releasing everything to `entwicklung` would also have made the demo green, and would have
    deleted the governance decision it exists to demonstrate — a fix that removes the feature it
    was protecting.
    """
    released = _releases().get("entwicklung")

    assert released is not None and _embed_model() not in released, (
        "'entwicklung' demonstrates a use case released fewer models than the rest (`FRD-308`). "
        "Widening it removes the point of the use case."
    )


def test_the_wait_requires_every_model_the_demo_serves() -> None:
    """Found in CI, where sixty-one integration tests failed on a model that was still downloading.

    The pull loop fetches the chat model first and the embedding model second, and the seed
    catalogues only what the endpoint already serves (`FRD-130` §4d). Between the two downloads the
    catalogue holds one of the two — and this wait, whose whole purpose is to close that window,
    asked `CHAT in declared` and went green inside it.

    None of the sixty-one failures mentioned a download. A one-text batch reported "not in the model
    catalog", a two-text batch `EMBEDDING_AGGREGATION_NOT_SUPPORTED` (the aggregation check runs
    first), and the KIRA surface "No model with id 9002" — three symptoms of one absence, which is
    why the run read as flaky rather than as wrong for twenty runs.

    Asserted as a **shape**: the tuple's elements are the names the traffic imports, so dropping
    either one fails here, and renaming them cannot make this pass by finding nothing — the third
    guard in this repository rewritten after one went vacuously true.
    """
    source = (ROOT / "tools/demo_wait_ready.py").read_text()
    required = _constant(source, "REQUIRED_MODELS")

    assert isinstance(required, ast.Tuple), "REQUIRED_MODELS is not a literal tuple any more"
    named = {element.id for element in required.elts if isinstance(element, ast.Name)}
    assert named == {"CHAT", "EMBED"}, (
        f"the wait requires {named or 'nothing'}; the demo serves a chat model and an embedding "
        "model, and waiting for one of them is what let the suite run against half a catalogue"
    )
    # The condition itself, not only the constant: `REQUIRED_MODELS` was defined and the return
    # still read `CHAT in declared` for one draft of this fix — a dead definition, which is the
    # same mistake a mutation caught twice in `_was_served()` last round.
    assert "if name not in declared" in source, (
        "REQUIRED_MODELS is declared but the wait does not check every one of them"
    )
