"""Security hardening of the gateway request path (ADR-0007).

Covers the body-size ceiling, use-case selector validation, credential redaction in traces,
and the bounds that keep an operator-supplied regex from stalling a worker.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aira_common.observability import redact_query_string
from aira_gateway.app import create_app, redact_span_query
from aira_gateway.auth.attribution import is_valid_use_case
from aira_gateway.config import GatewaySettings
from aira_gateway.pipeline.classifiers import (
    MAX_CUSTOM_PATTERNS,
    MAX_SCANNED_CHARS,
    HeuristicInjectionClassifier,
)
from aira_gateway.pipeline.config import MAX_FALLBACK_MODELS, MAX_STEPS, Pipeline
from aira_gateway.upstreams.base import ProviderRegistry

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
_URL = "/v1beta/models/mock-1:generateContent"


# -- request body ceiling ------------------------------------------------------------------


def test_declared_oversized_body_is_rejected() -> None:
    app = create_app(GatewaySettings(auth_required=False, max_request_bytes=100))
    with TestClient(app) as client:
        resp = client.post(_URL, json={"contents": [{"parts": [{"text": "x" * 500}]}]})
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == 413


def test_streamed_oversized_body_is_rejected() -> None:
    """No Content-Length: the body is counted as it arrives and aborted mid-flight."""
    app = create_app(GatewaySettings(auth_required=False, max_request_bytes=100))

    def chunks() -> Iterator[bytes]:
        yield b'{"contents":[{"parts":[{"text":"'
        yield b"x" * 500
        yield b'"}]}]}'

    with TestClient(app) as client:
        resp = client.post(_URL, content=chunks(), headers={"content-type": "application/json"})
    assert resp.status_code == 413


def test_body_within_limit_passes() -> None:
    app = create_app(GatewaySettings(auth_required=False, max_request_bytes=8192))
    with TestClient(app) as client:
        resp = client.post(_URL, json=_BODY)
    assert resp.status_code == 200


# -- use-case selector validation ----------------------------------------------------------


@pytest.mark.parametrize("slug", ["demo-uc", "a", "a1-b2", "x" * 64])
def test_valid_use_case_slugs(slug: str) -> None:
    assert is_valid_use_case(slug)


@pytest.mark.parametrize("slug", ["", "UPPER", "with space", "sql'injection", "x" * 65, "../etc"])
def test_invalid_use_case_slugs(slug: str) -> None:
    assert not is_valid_use_case(slug)


def test_invalid_selector_header_is_rejected() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    with TestClient(app) as client:
        resp = client.post(_URL, json=_BODY, headers={"x-aira-use-case": "x" * 500})
    assert resp.status_code == 400
    assert resp.json()["error"]["status"] == "INVALID_ARGUMENT"


# -- credential redaction in traces --------------------------------------------------------


def test_redact_query_string_masks_credentials() -> None:
    assert redact_query_string("alt=sse&key=aira_abc_def") == "alt=sse&key=REDACTED"
    assert redact_query_string("Key=secret") == "Key=REDACTED"
    assert redact_query_string("access_token=t&token=u&password=p") == (
        "access_token=REDACTED&token=REDACTED&password=REDACTED"
    )


def test_redact_query_string_leaves_harmless_params() -> None:
    assert redact_query_string("alt=sse") == "alt=sse"
    assert redact_query_string("") == ""
    assert redact_query_string("flag") == "flag"


class _Span:
    def __init__(self, recording: bool = True) -> None:
        self.attributes: dict[str, str] = {}
        self._recording = recording

    def is_recording(self) -> bool:
        return self._recording

    def set_attribute(self, key: str, value: str) -> None:
        self.attributes[key] = value


def test_span_hook_redacts_query() -> None:
    span = _Span()
    redact_span_query(span, {"path": "/v1beta/models/m:generateContent", "query_string": b"key=s"})
    assert span.attributes["url.query"] == "key=REDACTED"
    assert "key=REDACTED" in span.attributes["http.target"]
    assert "=s" not in span.attributes["http.target"]


def test_span_hook_ignores_non_recording_or_empty() -> None:
    silent = _Span(recording=False)
    redact_span_query(silent, {"path": "/", "query_string": b"key=s"})
    assert silent.attributes == {}

    span = _Span()
    redact_span_query(span, {"path": "/", "query_string": b""})
    assert span.attributes == {}

    redact_span_query(None, {"path": "/", "query_string": b"key=s"})  # must not raise


# -- regex / config bounds -----------------------------------------------------------------


async def test_custom_patterns_are_capped() -> None:
    classifier = HeuristicInjectionClassifier(
        tuple(f"pattern{i}" for i in range(MAX_CUSTOM_PATTERNS + 20)), use_builtins=False
    )
    assert len(classifier._compiled) == MAX_CUSTOM_PATTERNS
    assert await classifier.is_injection("pattern0") is True


async def test_overlong_patterns_are_dropped() -> None:
    classifier = HeuristicInjectionClassifier(("a" * 5_000,), use_builtins=False)
    assert classifier._compiled == []


async def test_scanned_text_is_bounded() -> None:
    classifier = HeuristicInjectionClassifier(("needle",), use_builtins=False)
    beyond = "x" * MAX_SCANNED_CHARS + "needle"
    assert await classifier.is_injection(beyond) is False
    assert await classifier.is_injection("needle" + beyond) is True


def test_pipeline_from_dict_bounds_steps_and_fallbacks() -> None:
    pipeline = Pipeline.from_dict(
        {
            "steps": [{"type": "allow_check"}] * (MAX_STEPS + 10),
            "fallback_models": [f"m{i}" for i in range(MAX_FALLBACK_MODELS + 10)],
        }
    )
    assert len(pipeline.steps) == MAX_STEPS
    assert len(pipeline.fallback_models) == MAX_FALLBACK_MODELS


def test_pipeline_from_dict_tolerates_malformed_steps() -> None:
    pipeline = Pipeline.from_dict(
        {"steps": ["not-a-dict", {"type": "nope"}, {"type": "allow_check", "config": "bad"}]}
    )
    assert len(pipeline.steps) == 1
    assert pipeline.steps[0].config == {}


def test_malformed_content_length_is_treated_as_unknown() -> None:
    """A junk Content-Length must not bypass the ceiling — the body is still counted."""
    app = create_app(GatewaySettings(auth_required=False, max_request_bytes=100))
    with TestClient(app) as client:
        resp = client.post(
            _URL,
            content=b'{"contents":[{"parts":[{"text":"' + b"x" * 500 + b'"}]}]}',
            headers={"content-type": "application/json", "content-length": "not-a-number"},
        )
    assert resp.status_code == 413


def test_trusted_forwarded_for_without_header_uses_peer() -> None:
    app = create_app(GatewaySettings(auth_required=False, trust_forwarded_for=True))
    with TestClient(app) as client:
        resp = client.post(_URL, json=_BODY)
    assert resp.status_code == 200


def test_stream_error_is_recorded_with_the_real_status() -> None:
    """A stream that dies mid-flight must not be audited as a success."""
    from collections.abc import AsyncIterator

    from aira_gateway.core.canonical import CanonicalChunk, CanonicalRequest
    from aira_gateway.upstreams.base import UpstreamError, UpstreamModel

    recorded: list[int] = []

    class _Failing:
        def models(self) -> list[UpstreamModel]:
            return [UpstreamModel("mock-1", "mock-1", ("streamGenerateContent",))]

        async def generate(self, request: CanonicalRequest) -> object:
            raise NotImplementedError

        async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
            yield CanonicalChunk(text_delta="partial")
            raise UpstreamError("upstream went away", 503)

        async def embed(self, model: str, text: str) -> list[float]:
            return [0.0]

    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_Failing()])

    async def _record(request, **kwargs) -> None:  # noqa: ANN001
        recorded.append(kwargs["status"])

    import aira_gateway.api.gemini.routes as routes

    original = routes.record_request
    routes.record_request = _record
    try:
        with TestClient(app) as client:
            resp = client.post("/v1beta/models/mock-1:streamGenerateContent", json=_BODY)
            assert resp.status_code == 200  # headers were already on the wire
    finally:
        routes.record_request = original

    assert recorded == [503]
