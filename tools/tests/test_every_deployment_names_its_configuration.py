"""A deployment document names the configuration file for that deployment.

`config/showcase.example.yaml` is named after a deployment, and
`docs/deployment/showcase.md` — 499 lines, the document somebody follows to *run* that
deployment — did not mention it once. Nor did `standalone.md` or `integrated.md` mention theirs.
All three files existed, all three were correct, and the reader they were written for had no way
to arrive at them.

That is `LESSONS.md`'s *"an instruction with no destination"*, inverted: a destination nothing
points at. It goes unnoticed for the same reason — nothing fails. The file is there, the document
is there, and only somebody who already knows both makes the connection.

**`DEVLOG.md` and `LESSONS.md` do not count.** They mention every file eventually, because they are
the history; naming something there is a record that it happened, not an instruction anybody
follows. Counting them would make this test pass on exactly the state it was written to catch —
`showcase.example.yaml` *was* mentioned in the DEVLOG, and the gap was real anyway.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "config").glob("*.example.yaml"))

#: Where a reader looking for "how do I run this shape" actually goes.
DEPLOYMENT_DOCS = ROOT / "docs" / "deployment"

#: History, not instruction. See the module docstring.
NARRATIVE = {"DEVLOG.md", "LESSONS.md"}


def test_there_are_configuration_examples_to_check() -> None:
    """A guard on the guard: a glob that matches nothing passes every assertion below."""
    assert EXAMPLES, "no config/*.example.yaml found — has the directory moved?"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_the_deployment_document_names_its_own_example(example: Path) -> None:
    """`config/<mode>.example.yaml` is named by `docs/deployment/<mode>.md`.

    The pairing is by name on purpose: it is the one relationship a reader assumes without being
    told, so it is the one that must actually hold.
    """
    mode = example.name.removesuffix(".example.yaml")
    document = DEPLOYMENT_DOCS / f"{mode}.md"

    assert document.exists(), (
        f"{example.name} is named after a deployment with no document: expected "
        f"{document.relative_to(ROOT)}. Either the file is misnamed or the document is missing — "
        "and a configuration example nobody is sent to is one nobody uses."
    )
    assert example.name in document.read_text(), (
        f"{document.relative_to(ROOT)} never mentions {example.name}, so a reader following it "
        "has no way to learn that the deployment is configurable at all. Name the file, say what "
        "rendering it does, and say what it does not carry."
    )


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_the_document_says_how_the_file_is_applied(example: Path) -> None:
    """Naming the file is not enough: editing it does nothing on its own.

    The file is rendered into `deploy/compose/.env`, and a reader who edits it and restarts sees no
    change and no error. So the document that names it has to name the renderer in the same breath
    — the gap this test exists for is between *"there is a file"* and *"here is what to do with
    it"*, and only the second is worth anything.
    """
    mode = example.name.removesuffix(".example.yaml")
    text = (DEPLOYMENT_DOCS / f"{mode}.md").read_text()

    assert "config_render.py" in text, (
        f"docs/deployment/{mode}.md names {example.name} without naming the renderer. Editing the "
        "file changes nothing until it is rendered into deploy/compose/.env, and a reader who "
        "edits and restarts gets no change and no error."
    )
