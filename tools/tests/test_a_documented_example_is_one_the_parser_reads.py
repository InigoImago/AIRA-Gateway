"""Every value the documentation shows for a list-shaped setting is one its own parser reads.

**Why this exists.** `docs/CONFIGURATION.md` and `docs/INTEGRATIONS.md` documented
`AIRA_FOUNDRY_DEPLOYMENTS` as `model=deployment@region`. The parser splits on `|`, so it read a
deployment called `deployment@region` in no region at all — and an entry without a region was
served without the residency check every other platform is held to. The compose `.env.example`
showed a third form, `name|model|region`, which refused to start. The integrated configuration
example listed Vertex models without the `region/publisher/` the parser requires. None of it was
wrong in a way any test could see, because nothing ever read the documentation with the code.

These settings are small grammars — `;` between entries, `|` and `=` inside them — and a reader
copies the example, not the grammar. So every example written as an assignment (`AIRA_X=value`)
in the documents a reader copies from goes through the parser the gateway starts with.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from aira_gateway.config import GatewaySettings
from aira_gateway.residency import parse_allowed, parse_model_regions
from aira_gateway.upstreams.foundry import parse_deployments
from aira_gateway.upstreams.openai import parse_servers
from aira_gateway.upstreams.vertex.adapters import OPEN_MODEL_PUBLISHERS
from aira_gateway.upstreams.vertex.regions import VertexModel

ROOT = Path(__file__).resolve().parents[2]

#: The pages a reader copies a value from. The development log and the lessons are history: an
#: example there is what something *was*, and may be quoted there precisely because it was wrong.
SOURCES = sorted(
    {
        ROOT / ".env.example",
        ROOT / "deploy" / "compose" / ".env.example",
        ROOT / "README.md",
        ROOT / "config" / "README.md",
        ROOT / "deploy" / "compose" / "README.md",
        *(ROOT / "docs").rglob("*.md"),
    }
    - {ROOT / "docs" / "DEVLOG.md", ROOT / "docs" / "LESSONS.md"}
)


def _vertex_models(value: str) -> None:
    spoken = {"google", "anthropic", *OPEN_MODEL_PUBLISHERS}
    for spec in (part.strip() for part in value.split(",") if part.strip()):
        model = VertexModel.parse(spec)
        assert model.publisher in spoken, f"no Vertex adapter speaks publisher '{model.publisher}'"


def _issuers(value: str) -> None:
    GatewaySettings(oidc_issuers=value).issuers()


#: Each list-shaped setting and the function the gateway reads it with at startup.
PARSERS: dict[str, Callable[[str], object]] = {
    "AIRA_FOUNDRY_DEPLOYMENTS": parse_deployments,
    "AIRA_VERTEX_MODELS": _vertex_models,
    "AIRA_OPENAI_SERVERS": parse_servers,
    "AIRA_ALLOWED_MODEL_REGIONS": parse_model_regions,
    "AIRA_ALLOWED_REGIONS": parse_allowed,
    "AIRA_OIDC_ISSUERS": _issuers,
}

#: `AIRA_X=value` — the value up to whitespace, a backtick or a closing quote. A Markdown table
#: writes a literal pipe as `\|`, which is unescaped before parsing.
_ASSIGNMENT = re.compile(
    r"\b(" + "|".join(PARSERS) + r")=(?P<quote>['\"]?)(?P<value>(?:\\\||[^\s`'\"])+)(?P=quote)"
)


def _examples() -> list[tuple[str, str, str]]:
    """``(where, setting, value)`` for every documented assignment worth parsing."""
    found: list[tuple[str, str, str]] = []
    for page in SOURCES:
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1):
            for match in _ASSIGNMENT.finditer(line):
                value = match.group("value").replace("\\|", "|")
                # Interpolations are Compose's, not an example of the grammar.
                if "${" in value:
                    continue
                found.append((f"{page.relative_to(ROOT)}:{number}", match.group(1), value))
    return found


def test_the_documents_show_examples_to_check() -> None:
    """A guard on the guard: a pattern that matched nothing would make the test below pass by
    checking nothing, and the documents do carry examples of these settings."""
    examples = _examples()
    settings = {setting for _, setting, _ in examples}
    assert len(examples) >= 5, examples
    assert {"AIRA_FOUNDRY_DEPLOYMENTS", "AIRA_ALLOWED_MODEL_REGIONS"} <= settings, settings


@pytest.mark.parametrize(("where", "setting", "value"), _examples())
def test_a_documented_example_parses(where: str, setting: str, value: str) -> None:
    try:
        PARSERS[setting](value)
    except Exception as exc:  # noqa: BLE001 — every refusal is the finding, whatever its type
        pytest.fail(
            f"{where}: the documented `{setting}={value}` is refused by the parser the gateway "
            f"starts with ({type(exc).__name__}: {exc}). A reader copies the example, not the "
            "grammar — correct the example."
        )
