"""The collector has a closed core of variables, and recipes for everything else (`ADR-0024`).

The core is what nearly every destination needs, under the same names on both channels. A
destination with other needs is a recipe: a fragment in `deploy/compose/otel/recipes/`, selected
through a slot that already exists, reading its own values from an optional env file. These tests
keep the core from growing back one knob at a time, and keep a recipe from breaking what the core
guarantees — the kind of breakage a merged configuration hides and `otelcol validate` passes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"
OTEL = ROOT / "deploy" / "compose" / "otel"
BASE = OTEL / "collector-config.yaml"
RECIPES = sorted((OTEL / "recipes").glob("*.yaml"))
ENV_EXAMPLE = OTEL / "recipes" / "collector.env.example"
HEC = OTEL / "recipes" / "forward-splunk-hec.yaml"

BACKEND, FORWARD = "AIRA_OTEL_BACKEND_", "AIRA_OTEL_FORWARD_"

#: What each channel passes into the collector, by suffix. The three `…_CONFIG` slots are
#: substituted into the command line and never reach the container, so they are not here.
#:
#: **Closed on purpose.** A name joins only when nearly every destination needs it; anything else
#: is a recipe (`ADR-0024`). This set is where that decision is taken, not in Compose.
CORE = {
    "ENDPOINT",
    "ENCODING",
    "COMPRESSION",
    "AUTH_HEADER",
    "AUTHORIZATION",
    "INSECURE",
    "CA_FILE",
    "CLIENT_CERT_FILE",
    "CLIENT_KEY_FILE",
    "BATCH_SECONDS",
    "BATCH_SIZE",
    "CONSUMERS",
    "QUEUE",
    "RETRY_INITIAL",
}

#: The transport a channel does not default to: its address and its no-TLS switch (`FRD-620`).
TRANSPORT_SPECIFIC = {
    BACKEND: {"HTTP_ENDPOINT", "PLAINTEXT"},
    FORWARD: {"GRPC_ENDPOINT", "GRPC_PLAINTEXT"},
}

#: The pipelines each channel owns.
PIPELINES = {
    "backend": {"traces", "metrics", "logs"},
    "forward": {"traces/siem", "metrics/siem", "logs/siem"},
}

#: `${env:NAME}` or `${env:NAME:-fallback}`, with the fallback captured when there is one.
ENV_REFERENCE = re.compile(r"\$\{env:([A-Z0-9_]+)(:-[^}]*)?\}")


def _document(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _collector() -> dict:
    return _document(COMPOSE)["services"]["otel-collector"]


def _channel(recipe: Path) -> str:
    return "backend" if recipe.name.startswith("backend-") else "forward"


# --- the core ------------------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [BACKEND, FORWARD])
def test_the_core_is_closed(prefix: str) -> None:
    """**The variables grew from 32 to 66 in a week**, one destination at a time, each spelled in
    six places. A name added to Compose without changing `CORE` here fails, and the change that
    makes it pass is the one a reviewer sees."""
    expected = CORE | TRANSPORT_SPECIFIC[prefix]
    declared = {
        name[len(prefix) :] for name in _collector()["environment"] if name.startswith(prefix)
    }

    assert declared == expected, (
        f"{prefix}* differs from the core by {sorted(declared ^ expected)}. A setting only some "
        "destinations need is a recipe in deploy/compose/otel/recipes/, reading its values from "
        "the optional env file (ADR-0024)."
    )


def test_the_recipes_are_mounted_and_their_values_are_optional() -> None:
    """A recipe is selected by a path inside the container, and its values come from a file an
    installation may not have. Required, that file would stop every stack that uses no recipe."""
    collector = _collector()

    assert "./otel/recipes:/etc/otelcol-contrib/recipes:ro" in collector["volumes"]
    assert "./otel/custom:/etc/otelcol-contrib/custom:ro" in collector["volumes"]
    assert collector["env_file"] == [{"path": "./otel/custom/collector.env", "required": False}]


def test_an_installations_own_collector_files_are_not_committed() -> None:
    """`custom/` holds recipe secrets and an installation's own fragments."""
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "deploy/compose/otel/custom/*" in ignored
    assert "!deploy/compose/otel/custom/README.md" in ignored


# --- the recipes ---------------------------------------------------------------------------------


def test_there_are_recipes_to_check() -> None:
    """A guard on the guard: every parametrised test below passes vacuously over an empty glob."""
    assert {recipe.name for recipe in RECIPES} >= {
        "forward-splunk-hec.yaml",
        "forward-oauth2.yaml",
        "forward-azure-monitor.yaml",
    }


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda p: p.stem)
def test_a_recipe_moves_all_of_its_channels_pipelines_or_none(recipe: Path) -> None:
    """**A merged pipeline replaces its exporter list.** A recipe that moved two of three pipelines
    would leave the third on OTLP, which reads as *the destination is dropping things*; one that
    named the other channel's would move that channel too; and an observability pipeline without
    `debug` and `file/arrived` unhooks the arrivals."""
    channel = _channel(recipe)
    pipelines = _document(recipe).get("service", {}).get("pipelines") or {}

    assert not pipelines or set(pipelines) == PIPELINES[channel], sorted(pipelines)
    if channel == "backend":
        for pipeline in pipelines.values():
            assert {"debug", "file/arrived"} <= set(pipeline["exporters"])


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda p: p.stem)
def test_a_recipe_that_brings_an_extension_keeps_the_bases(recipe: Path) -> None:
    """**`service::extensions` is a list, and a merged list replaces.** A recipe that lists only its
    own extension stops both channels' header credentials — with `validate` clean, because an
    unstarted extension is a valid configuration. An extension defined and not listed never
    starts."""
    document = _document(recipe)
    brought = set(document.get("extensions") or {})
    listed = document.get("service", {}).get("extensions")

    if not brought:
        assert listed is None, "a recipe without an extension of its own has no reason to list them"
        return
    assert set(_document(BASE)["service"]["extensions"]) <= set(listed)
    assert brought <= set(listed)


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda p: p.stem)
def test_every_value_a_recipe_reads_is_the_core_or_has_a_fallback(recipe: Path) -> None:
    """A recipe's own values come from an env file Compose does not declare, so an unset one is
    absent and the inline fallback applies — which is what lets the collector start with a recipe
    selected and nothing filled in. An address falls back to a name that never resolves, a secret
    to the name of its variable. An `AIRA_` name must be a core variable: Compose carries its
    fallback, and any other would be a product setting nobody declared."""
    core = {
        f"{prefix}{suffix}"
        for prefix in (BACKEND, FORWARD)
        for suffix in CORE | TRANSPORT_SPECIFIC[prefix]
    }

    for name, fallback in ENV_REFERENCE.findall(recipe.read_text(encoding="utf-8")):
        if name.startswith("AIRA_"):
            assert name in core, f"{recipe.name} reads {name}, which is not in the core"
            continue
        assert fallback, f"{recipe.name} reads {name} without a fallback"
        if name.endswith(("ENDPOINT", "_URL")):
            assert ".invalid" in fallback, f"{name} should fall back to an unresolvable address"
        if name.endswith(("TOKEN", "SECRET")):
            assert f"{name}-is-not-set" in fallback, f"{name} should fall back to its own name"


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda p: p.stem)
def test_every_value_a_recipe_reads_is_in_the_example(recipe: Path) -> None:
    """The example is the one list of what the recipes read; a name missing there is a value
    nobody knows to set."""
    example = ENV_EXAMPLE.read_text(encoding="utf-8")

    for name, _ in ENV_REFERENCE.findall(recipe.read_text(encoding="utf-8")):
        if not name.startswith("AIRA_"):
            assert f"#{name}=" in example, f"{name} is read by {recipe.name} and not in the example"


def test_the_hec_recipe_sends_metrics_to_their_own_index_and_differs_in_nothing_else() -> None:
    """**`mstats` reads a metrics index and nothing else, and one `index` cannot be both kinds** —
    so metrics have an exporter of their own. Anything else that differed between the two would be a
    second configuration nobody chose."""
    document = _document(HEC)
    exporters = document["exporters"]
    spans = dict(exporters["splunk_hec/forward"])
    numbers = dict(exporters["splunk_hec/forward_metrics"])

    assert spans.pop("index") == "${env:SPLUNK_HEC_INDEX:-}"
    assert numbers.pop("index") == "${env:SPLUNK_HEC_METRICS_INDEX:-}"
    assert spans == numbers
    assert {name: p["exporters"] for name, p in document["service"]["pipelines"].items()} == {
        "traces/siem": ["splunk_hec/forward"],
        "logs/siem": ["splunk_hec/forward"],
        "metrics/siem": ["splunk_hec/forward_metrics"],
    }
