"""Every signal can be seen arriving, and the two questions stay apart.

*What arrived* and *what was forwarded* are different, and a green collector log cannot tell them
apart — the sentence `tools/lab_status.py` was written for, one hop earlier. The reference stack
answered the first for traces and logs and **not for metrics**: that pipeline had no `debug`
exporter at all, which is an asymmetry rather than a decision and the hardest absence to notice,
because a metric that never arrives looks exactly like one whose value did not change.

Read as YAML rather than as text: a pipeline's exporter list is a list, and a check that grepped
for a name would pass on a name inside a comment explaining why it is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "deploy" / "compose" / "otel" / "collector-config.yaml"

#: What every signal must be able to be seen through.
SEEN_THROUGH = ("debug", "file/arrived")


def _pipelines() -> dict:
    # `${env:…}` is not YAML's, but it is a plain scalar, so it parses.
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["service"]["pipelines"]


def test_the_config_still_describes_three_pipelines() -> None:
    """A guard on the guard: a renamed key would make every assertion below vacuous."""
    assert set(_pipelines()) == {"traces", "metrics", "logs"}


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
@pytest.mark.parametrize("exporter", SEEN_THROUGH)
def test_every_signal_can_be_seen_arriving(signal: str, exporter: str) -> None:
    exporters = _pipelines()[signal]["exporters"]

    assert exporter in exporters, (
        f"the {signal} pipeline has no `{exporter}`, so nothing arriving on it can be looked at. "
        f"Metrics were missing this until 2026-09-02 and nobody noticed, because an absent metric "
        f"and an unchanged one look the same."
    )


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_every_signal_still_reaches_the_backend(signal: str) -> None:
    """The inspection exporters are additions. A pipeline that lost its real destination while
    gaining a debug one would look fine in a log and store nothing."""
    assert "otlp/backend" in _pipelines()[signal]["exporters"]


def test_the_inspection_exporters_are_configured_by_variable() -> None:
    """Both are off or quiet by default and turned up without editing this file — the property
    that makes them usable on a stack somebody else is running."""
    exporters = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["exporters"]

    assert exporters["debug"]["verbosity"] == "${env:AIRA_OTEL_DEBUG_VERBOSITY}"
    assert exporters["file/arrived"]["path"] == "${env:AIRA_OTEL_ARRIVED_FILE}"
    assert exporters["file/arrived"]["format"] == "json"
