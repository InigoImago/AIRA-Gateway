"""A process says what it is when it starts and that it stops (`FRD-631` FR-3, FR-4, FR-10).

A log store that never sees a service start cannot tell a quiet service from a stopped one. So the
first line a process exports names its version, stage and how it writes people and addresses; a
key it drew for itself is a warning beside it, where an alert sees it; and the last thing it does is
hand over what the export still holds.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aira_common.addresses import AddressMode, configure_addresses
from aira_common.forwarded import TrustedProxies
from aira_common.logging import configure_logging, process_named
from aira_common.logging.lifecycle import (
    configure_identities,
    pseudonym_state,
    say_started,
    say_stopped,
    say_stopping,
)
from aira_common.observability import flush_telemetry
from aira_common.pseudonym import configure_pseudonyms


def _events(capsys: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def _start(proxies: str = "", environ: dict[str, str] | None = None, **identities: Any) -> None:
    configure_identities(**identities)
    configure_logging("INFO")
    say_started(
        service="aira-gateway",
        environment="test",
        signals=["logs", "metrics"],
        log_level="info",
        trusted_proxies=TrustedProxies.parse(proxies),
        environ=environ or {},
    )


def test_the_first_line_says_what_the_process_is_and_how_it_writes(
    capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release and the commit are the image's (`FRD-157`): what the build handed in."""
    monkeypatch.setenv("AIRA_BUILD_VERSION", "0.1.18")
    monkeypatch.setenv("AIRA_GIT_COMMIT", "0123456789abcdef")
    _start(strict_data_mode=True, pseudonym_secret="key", telemetry_ip="pseudonym")

    (line,) = [line for line in _events(capsys) if line["event"] == "process_started"]
    assert line["category"] == "lifecycle"
    assert (line["service"], line["version"], line["environment"]) == (
        "aira-gateway",
        "0.1.18",
        "test",
    )
    assert line["commit"] == "0123456789abcdef"
    assert (line["strict_data_mode"], line["telemetry_ip"], line["pseudonym_key"]) == (
        True,
        "pseudonym",
        "installation",
    )
    assert (line["signals"], line["log_level"]) == (["logs", "metrics"], "INFO")


def test_a_build_that_is_no_release_says_so_and_names_no_commit(capsys: Any) -> None:
    """Without the image's identity the line says `0.0.0+local`, never a number that looks like a
    release — the `0.1.0` every process said before (`FRD-157`)."""
    _start(strict_data_mode=True, pseudonym_secret="key", telemetry_ip="pseudonym")

    (line,) = [line for line in _events(capsys) if line["event"] == "process_started"]
    assert line["version"] == "0.0.0+local"
    assert line["commit"] is None


def test_the_first_line_says_how_the_callers_address_is_read(capsys: Any) -> None:
    """`FRD-156`: the connection's own address with no proxy named, and which proxies are."""
    _start(strict_data_mode=True, pseudonym_secret="key", telemetry_ip="pseudonym")
    _start(
        "172.18.0.0/16, 10.20.30.40",
        strict_data_mode=True,
        pseudonym_secret="key",
        telemetry_ip="pseudonym",
    )

    first, second = [line for line in _events(capsys) if line["event"] == "process_started"]
    assert (first["client_address"], first["trusted_proxies"]) == ("peer", [])
    assert (second["client_address"], second["trusted_proxies"]) == (
        "forwarded",
        ["172.18.0.0/16", "10.20.30.40/32"],
    )


def test_a_retired_setting_still_set_is_a_warning_naming_what_replaced_it(capsys: Any) -> None:
    """A setting nothing reads any more is one somebody believes took effect."""
    _start(
        environ={"AIRA_TRUSTED_PROXY_HOPS": "2", "AIRA_TRUST_FORWARDED_FOR": "true"},
        strict_data_mode=True,
        pseudonym_secret="key",
        telemetry_ip="pseudonym",
    )

    retired = [line for line in _events(capsys) if line["event"] == "setting_retired"]
    assert sorted(line["setting"] for line in retired) == [
        "AIRA_TRUSTED_PROXY_HOPS",
        "AIRA_TRUST_FORWARDED_FOR",
    ]
    assert all(
        line["level"] == "warning" and "AIRA_TRUSTED_PROXIES" in line["hint"] for line in retired
    )


def test_a_key_the_process_drew_itself_is_a_warning_beside_it(capsys: Any) -> None:
    _start(strict_data_mode=True, pseudonym_secret="", telemetry_ip="pseudonym")

    (warning,) = [line for line in _events(capsys) if line["event"] == "pseudonym_key_per_process"]
    assert warning["level"] == "warning"
    assert "AIRA_PSEUDONYM_SECRET" in warning["hint"]


def test_no_pseudonyms_written_is_no_key_to_warn_about(capsys: Any) -> None:
    _start(strict_data_mode=False, pseudonym_secret="", telemetry_ip="full")

    assert "pseudonym_key_per_process" not in [line["event"] for line in _events(capsys)]
    assert pseudonym_state() == (True, "no pseudonyms are written")


@pytest.mark.parametrize(
    ("strict", "secret", "mode", "stable"),
    [
        (True, "key", AddressMode.PSEUDONYM, True),
        (True, "", AddressMode.FULL, False),
        (False, "", AddressMode.PSEUDONYM, False),
        (False, "", AddressMode.NETWORK, True),
    ],
)
def test_readiness_is_told_whether_the_key_is_stable(
    strict: bool, secret: str, mode: AddressMode, stable: bool
) -> None:
    configure_pseudonyms(enabled=strict, secret=secret)
    configure_addresses(mode)
    assert pseudonym_state()[0] is stable


def test_every_line_names_the_process_once_it_is_named(capsys: Any) -> None:
    configure_logging("INFO")
    process_named("consumer")
    say_stopping(service="aira-gateway")
    say_stopped(service="aira-gateway")

    lines = _events(capsys)
    assert [line["event"] for line in lines] == ["process_stopping", "process_stopped"]
    assert {line["process"] for line in lines} == {"consumer"}
    assert isinstance(lines[-1]["uptime_seconds"], float)


def test_the_last_thing_is_to_hand_over_what_the_export_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every provider is asked, and one that fails is not why a process cannot stop."""
    from aira_common.observability import providers

    asked: list[str] = []

    class Provider:
        def __init__(self, name: str, fails: bool = False) -> None:
            self.name, self.fails = name, fails

        def force_flush(self, timeout_millis: int) -> bool:
            asked.append(self.name)
            if self.fails:
                raise RuntimeError("collector gone")
            return True

    monkeypatch.setattr(providers.trace, "get_tracer_provider", lambda: Provider("traces", True))
    monkeypatch.setattr(providers.metrics, "get_meter_provider", lambda: Provider("metrics"))
    monkeypatch.setattr(providers, "get_logger_provider", lambda: Provider("logs"))

    assert flush_telemetry() is False
    assert asked == ["traces", "metrics", "logs"]
