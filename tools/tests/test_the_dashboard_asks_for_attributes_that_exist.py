"""The dashboard's queries name attributes the code actually sets (`FRD-615`).

A provisioned dashboard is a **hand-written list with no counterpart** — the shape `LESSONS.md`
records six times. It names `span.aira.outcome`, `span.aira.use_case`, `messaging.kafka.offset`;
the code sets those strings somewhere else entirely, and renaming one leaves a panel that returns
nothing. An empty panel is indistinguishable from *"nothing happened"*, which is exactly the
reading a dashboard exists to prevent — so it would be believed rather than noticed.

Cheap to compare, because both sides are text: every attribute the dashboard asks for must be a
string some source file writes.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "deploy" / "compose" / "grafana" / "dashboards" / "aira-overview.json"
SOURCES = [ROOT / "gateway" / "src", ROOT / "libs" / "src", ROOT / "management" / "backend" / "src"]

#: `span.aira.use_case`, `.messaging.kafka.offset` — the attribute, without TraceQL's scope prefix.
_ATTRIBUTE = re.compile(r"\b(?:span|resource)?\.(aira\.[a-z_.]+|messaging\.[a-z_.]+)")


def queried_attributes() -> set[str]:
    text = DASHBOARD.read_text()
    return set(_ATTRIBUTE.findall(text))


def written_attributes() -> str:
    return "\n".join(
        path.read_text(errors="ignore") for source in SOURCES for path in source.rglob("*.py")
    )


def test_the_dashboard_has_queries_to_check() -> None:
    """A guard on the guard: if the pattern stops matching, everything below passes vacuously."""
    assert len(queried_attributes()) >= 8


def test_every_attribute_a_panel_asks_for_is_one_the_code_sets() -> None:
    written = written_attributes()
    missing = sorted(name for name in queried_attributes() if f'"{name}"' not in written)

    assert not missing, (
        f"the dashboard queries {missing}, and no source file sets them. A renamed attribute "
        "leaves a panel returning nothing, which reads as 'nothing happened' rather than as a "
        "broken query — rename it in the dashboard too, or point the panel at what replaced it."
    )


def test_the_dashboard_asks_for_no_payload() -> None:
    """**Prompts and responses are not in telemetry and must not become findable through it.**

    They live in `request_logs`, behind the use case's storage switch, its retention clock and a
    role check (`FRD-505`, `ADR-0016`). A panel that surfaced one would route around all three, in
    a Grafana whose whole point is that everybody who operates the stack can read it.
    """
    text = DASHBOARD.read_text().lower()
    for forbidden in ("payload", "prompt", "request_payload", "response_payload", "content"):
        assert f"aira.{forbidden}" not in text
