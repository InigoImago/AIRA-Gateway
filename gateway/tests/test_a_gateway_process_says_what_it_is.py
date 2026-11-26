"""A gateway process says what it is once its export exists (`FRD-631` FR-1, FR-3, FR-10).

The first line a log store receives from a process names its version, its stage, how it writes a
person and an address and which signals it exports — and, where it drew its pseudonym key itself,
a warning beside it. Written by `configure_process`, which every gateway process calls.

And every line names the process, the first ones too: reading the settings from Vault writes a
process's first lines, so each entry point is named before it reads them.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import structlog

from aira_common.logging import lifecycle, process_name
from aira_gateway.config import GatewaySettings, configure_process


class _Read(Exception):
    """Raised where an entry point reads its settings, so it goes no further."""


def _reader(seen: list[str | None], result: GatewaySettings | None = None) -> Callable[..., Any]:
    """A stand-in for the settings that notes which process is named when they are read."""

    def read(*_args: Any, **_kwargs: Any) -> GatewaySettings:
        seen.append(process_name())
        if result is None:
            raise _Read
        return result

    return read


def _lines(capsys: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line[:1] == "{"]


def test_the_first_line_names_the_process_and_how_it_writes(
    capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "_log", structlog.get_logger("aira_common.lifecycle"))
    capsys.readouterr()

    configure_process(GatewaySettings(pseudonym_secret="key"), process="consumer")

    (line,) = [line for line in _lines(capsys) if line["event"] == "process_started"]
    assert (line["service"], line["process"], line["category"]) == (
        "aira-gateway",
        "consumer",
        "lifecycle",
    )
    assert (line["telemetry_ip"], line["pseudonym_key"], line["strict_data_mode"]) == (
        "pseudonym",
        "installation",
        True,
    )


def test_the_api_is_named_before_it_reads_its_settings(
    monkeypatch: pytest.MonkeyPatch, settings: GatewaySettings
) -> None:
    from aira_gateway import app

    seen: list[str | None] = []
    monkeypatch.setattr(app, "GatewaySettings", _reader(seen, settings))

    app.create_app()

    assert seen == ["api"]


@pytest.mark.parametrize(
    ("module", "settings_from", "process"),
    [
        ("aira_gateway.consumer.worker", "aira_gateway.consumer.worker", "consumer"),
        ("aira_gateway.retention", "aira_gateway.config", "retention"),
    ],
)
def test_a_worker_is_named_before_it_reads_its_settings(
    monkeypatch: pytest.MonkeyPatch, module: str, settings_from: str, process: str
) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(importlib.import_module(settings_from), "GatewaySettings", _reader(seen))

    with pytest.raises(_Read):
        importlib.import_module(module).main([])

    assert seen == [process]


def test_a_migration_is_named_before_it_reads_its_settings() -> None:
    """Alembic runs `env.py` as a script, so the order is read from it."""
    source = (Path(__file__).resolve().parents[1] / "migrations" / "env.py").read_text("utf-8")

    assert source.index('process_named("migrate")') < source.index("GatewaySettings()")


def test_a_key_the_process_drew_itself_is_a_warning_in_the_log_store(
    capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "_log", structlog.get_logger("aira_common.lifecycle"))
    capsys.readouterr()

    configure_process(GatewaySettings(pseudonym_secret=""))

    events = [line["event"] for line in _lines(capsys)]
    assert events.index("process_started") < events.index("pseudonym_key_per_process")
