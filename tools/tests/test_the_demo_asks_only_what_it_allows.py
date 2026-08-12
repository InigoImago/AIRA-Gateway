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
SEED = ROOT / "management/backend/src/aira_management/apps/seed/contributions/showcase.py"
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
