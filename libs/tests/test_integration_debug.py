"""The debug channel itself: the switch, the vocabulary, the outcome, and what it costs.

`FRD-617`. The wirings into the six systems are tested where they live — `test_kafka.py`,
`test_oidc_when_the_provider_is_gone.py`, `test_secrets.py`, `test_observability.py`, and the
gateway's `test_the_channel_is_wired_to_what_it_names.py`.
"""

from __future__ import annotations

import json

import pytest

from aira_common.integration_debug import (
    ALL,
    SYSTEMS,
    UnknownIntegration,
    configure_integration_debug,
    is_on,
    parse_systems,
    report,
    watch,
    watched,
)
from aira_common.logging import configure_logging


@pytest.fixture(autouse=True)
def _quiet() -> None:
    """Every case starts with the channel off and JSON logging on."""
    configure_logging("INFO", json_output=True)
    configure_integration_debug("")


def lines(capsys) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]


def calls(capsys) -> list[dict]:
    return [line for line in lines(capsys) if line["event"] == "integration_call"]


# --- the switch -------------------------------------------------------------------------------


@pytest.mark.parametrize("spec", ["", "   ", ",", " , ,"])
def test_an_empty_setting_means_off(spec: str) -> None:
    assert parse_systems(spec) == frozenset()


def test_a_selection_is_a_comma_separated_list() -> None:
    assert parse_systems("otel, KAFKA ") == {"otel", "kafka"}


def test_all_means_every_system_this_build_knows() -> None:
    assert parse_systems(ALL) == set(SYSTEMS)


def test_an_unknown_name_is_refused_and_says_what_is_valid() -> None:
    with pytest.raises(UnknownIntegration) as raised:
        parse_systems("otel,kafak")
    message = str(raised.value)
    assert "kafak" in message
    # The refusal has to be actionable: naming the mistake without naming the alternatives sends
    # the operator to the source.
    for system in SYSTEMS:
        assert system in message


def test_configuring_reports_what_is_now_watched() -> None:
    assert configure_integration_debug("vault,redis") == {"vault", "redis"}
    assert watched() == {"vault", "redis"}
    assert is_on("vault") and not is_on("kafka")


# --- off is off -------------------------------------------------------------------------------


def test_a_watched_call_emits_nothing_when_its_system_is_off(capsys) -> None:
    configure_integration_debug("kafka")
    with watch("vault", "read", target="http://vault:8200") as call:
        call.note(status=200)
    assert calls(capsys) == []


def test_a_direct_report_for_an_unwatched_system_says_nothing(capsys) -> None:
    """`report` is the way in for a call that is not call-shaped — a SQLAlchemy `handle_error`
    event, a Django `connection_created` signal — so its own gate has to hold on its own, while a
    *different* system is being watched. Written after the mutation that widens it to `if not
    _watched` survived: `watch` refuses first, so nothing reached this gate to prove it.
    """
    configure_integration_debug("kafka")
    report("postgres", "connect", target="postgresql://db:5432/aira")
    assert calls(capsys) == []


def test_the_handle_yielded_for_an_unwatched_system_is_shared_and_collects_nothing() -> None:
    """The cost of being ignored. A call site must not allocate, time itself, or collect fields to
    be switched off — and that has to hold **while another system is watched**, which is the
    ordinary case: `AIRA_DEBUG_INTEGRATIONS=kafka` leaves five systems on this path.
    """
    configure_integration_debug("kafka")
    with watch("vault", "read") as first, watch("redis", "script") as second:
        first.note(anything="at all")
        second.failed("nor this")
        assert first is second


# --- one line, whatever happened --------------------------------------------------------------


def test_a_call_that_returns_emits_one_line_saying_ok(capsys) -> None:
    configure_integration_debug("kafka")
    with watch("kafka", "producer.send", topic="aira.usecases") as call:
        call.note(partition=3, offset=91)

    (line,) = calls(capsys)
    assert line["system"] == "kafka"
    assert line["operation"] == "producer.send"
    assert line["outcome"] == "ok"
    assert line["topic"] == "aira.usecases"
    assert (line["partition"], line["offset"]) == (3, 91)
    assert line["duration_ms"] >= 0
    assert line["level"] == "info"


def test_a_call_that_succeeds_is_reported_too(capsys) -> None:
    """The property the whole channel rests on.

    "No errors" and "it arrived" are different statements — the sentence `tools/lab_status.py` was
    written for one layer further out. A channel that spoke only on failure would leave *nothing
    was sent* and *everything was sent* identical, which is the state this feature exists to end.
    """
    configure_integration_debug("otel")
    with watch("otel", "export", signal="traces"):
        pass
    assert [line["outcome"] for line in calls(capsys)] == ["ok"]


def test_a_raising_call_is_reported_as_failed_and_the_exception_is_untouched(capsys) -> None:
    configure_integration_debug("vault")
    with (
        pytest.raises(ConnectionRefusedError, match="nope"),
        watch("vault", "login", target="http://vault:8200"),
    ):
        raise ConnectionRefusedError("nope")

    (line,) = calls(capsys)
    assert line["outcome"] == "failed"
    assert line["error_type"] == "ConnectionRefusedError"
    assert line["error"] == "nope"
    assert line["level"] == "warning"


def test_a_timeout_is_told_apart_from_a_refusal(capsys) -> None:
    """Different lines because they send whoever reads them to different places."""
    configure_integration_debug("auth")
    with pytest.raises(TimeoutError), watch("auth", "jwks.fetch"):
        raise TimeoutError("timed out")
    assert calls(capsys)[0]["outcome"] == "timeout"


class ReadTimeout(Exception):
    """A client library's own timeout type, which is not a `TimeoutError`."""


def test_a_libraries_own_timeout_type_is_recognised_by_name(capsys) -> None:
    """httpx, redis-py, aiokafka and urllib each define one; importing four clients into a shared
    module so a classification can be exact is the worse trade."""
    configure_integration_debug("redis")
    with pytest.raises(ReadTimeout), watch("redis", "script"):
        raise ReadTimeout("too slow")
    assert calls(capsys)[0]["outcome"] == "timeout"


def test_a_failure_reported_as_a_value_is_still_a_failure(capsys) -> None:
    """An OTLP exporter answers `FAILURE` and raises nothing."""
    configure_integration_debug("otel")
    with watch("otel", "export", signal="logs") as call:
        call.failed("exporter returned FAILURE")

    (line,) = calls(capsys)
    assert line["outcome"] == "failed"
    assert line["detail"] == "exporter returned FAILURE"


def test_a_long_error_is_bounded(capsys) -> None:
    configure_integration_debug("postgres")
    with pytest.raises(RuntimeError), watch("postgres", "connect"):
        raise RuntimeError("x" * 5000)
    assert len(calls(capsys)[0]["error"]) == 200


# --- nothing that authenticates ---------------------------------------------------------------


def test_a_password_in_an_address_never_reaches_the_line(capsys) -> None:
    configure_integration_debug("redis")
    report("redis", "script", target="redis://aira:s3cret@redis:6379/0")
    line = calls(capsys)[0]
    assert "s3cret" not in json.dumps(line)
    assert line["target"] == "redis://aira:REDACTED@redis:6379/0"


def test_an_api_key_in_a_query_never_reaches_the_line(capsys) -> None:
    configure_integration_debug("otel")
    report("otel", "export", target="https://collector/v1/traces?key=AIzaSy-real")
    assert "AIzaSy-real" not in json.dumps(calls(capsys)[0])


# --- never the reason something fails ----------------------------------------------------------


class Unprintable:
    def __repr__(self) -> str:
        raise RuntimeError("boom")


def test_a_failure_inside_the_channel_is_swallowed(capsys) -> None:
    """`FR-6`. This runs inside an exporter's background thread and on the authentication path."""
    configure_integration_debug("kafka")
    report("kafka", "producer.send", key=Unprintable())
    # It said nothing rather than raising; the caller's work is unaffected.
    assert capsys.readouterr() is not None


def test_a_watched_call_returns_what_the_callee_returned() -> None:
    configure_integration_debug("kafka")
    with watch("kafka", "producer.send") as call:
        call.note(ok=True)
        result = "the callee's value"
    assert result == "the callee's value"


def test_a_timeout_a_library_wrapped_in_its_own_type_is_still_a_timeout(capsys) -> None:
    """Measured against PyJWT: `PyJWKClient` raises `PyJWKClientConnectionError` for a refused
    connection **and** for a read timeout, with the real answer one `__cause__` down. Without the
    chain walk the field meant to separate "the port is wrong" from "something is swallowing the
    packets" separated nothing on the one system where that question is asked per request.
    """
    configure_integration_debug("auth")

    class ConnectionProblem(Exception):
        pass

    with pytest.raises(ConnectionProblem), watch("auth", "jwks.fetch"):
        try:
            raise TimeoutError("timed out")
        except TimeoutError as inner:
            raise ConnectionProblem('Fail to fetch data from the url, err: "timed out"') from inner

    assert calls(capsys)[0]["outcome"] == "timeout"


def test_a_refusal_wrapped_in_the_same_type_is_still_a_refusal(capsys) -> None:
    """The other side of the pair — the chain walk must not make everything a timeout."""
    configure_integration_debug("auth")

    class ConnectionProblem(Exception):
        pass

    with pytest.raises(ConnectionProblem), watch("auth", "jwks.fetch"):
        try:
            raise ConnectionRefusedError(111, "Connection refused")
        except ConnectionRefusedError as inner:
            raise ConnectionProblem("Fail to fetch data from the url") from inner

    assert calls(capsys)[0]["outcome"] == "failed"


def test_the_cause_chain_walk_is_bounded(capsys) -> None:
    """A client library with a deep or cyclic chain must not turn a log line into a walk."""
    configure_integration_debug("redis")
    deepest = TimeoutError("far too deep")
    error: BaseException = deepest
    for _ in range(10):
        wrapper = RuntimeError("wrapped")
        wrapper.__cause__ = error
        error = wrapper

    with pytest.raises(RuntimeError), watch("redis", "script"):
        raise error

    assert calls(capsys)[0]["outcome"] == "failed"
