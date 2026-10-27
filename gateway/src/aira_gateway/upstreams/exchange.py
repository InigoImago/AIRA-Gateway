"""One HTTP exchange with an upstream, judged the same way on every platform.

Three transports reach three platforms — Vertex, Google AI Studio, an OpenAI-compatible server —
and an outage, a refusal or a broken body must read the same on all three: which vendor is down
must not decide whether the caller sees a reason, a status or a 500. So the judgement lives here
once, and a transport only names itself:

    refused         a non-200 answer → `UpstreamError` with its status, and a 400's or 413's stated
                    reason
    unreachable     no answer at all → `UpstreamError` without a status, saying who stopped — and
                    the gateway's own `504`/`503` where it was this gateway's time or pool
    json_object     a 200 whose body is not a JSON object → `UpstreamError` 502
    opened_stream   a streamed request, judged before it is read; a refusal is read for its
                    reason and closed
    sse_payloads    each `data:` payload of a stream, parsed; a stream that breaks while it is
                    read is an `UpstreamError`, not whatever the HTTP library or the parser raised

Every failure becomes an `UpstreamError` because that is what the dispatch chain moves on for and
what both surfaces render as the upstream's fault (`serving.REFUSALS`). Anything else escapes as a
500 that blames the gateway and never reaches the configured fallback.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx

from aira_gateway.upstreams.base import UpstreamError, upstream_reason
from aira_gateway.upstreams.diagnosis import from_response as diagnosis_of_response
from aira_gateway.upstreams.diagnosis import from_transport as diagnosis_of_transport

#: How long establishing a connection may take, apart from waiting for the answer. A model may
#: think for minutes before its first byte; a host that accepts no connection within seconds is
#: down, and the regional failover and the fallback chain exist for exactly that outage — so the
#: chain must not wait out the whole read allowance before moving on.
CONNECT_TIMEOUT_SECONDS = 10.0

#: The status for an answer the upstream should not have given: a 200 whose body is not JSON (a
#: proxy's maintenance page) or a stream event that does not parse. A bad answer from the server
#: behind the gateway — and one regional failover moves on for, like any other 5xx.
BAD_ANSWER = 502


def timeout(seconds: float) -> httpx.Timeout:
    """``seconds`` to read, write and wait for a pooled connection; at most
    `CONNECT_TIMEOUT_SECONDS` to connect."""
    return httpx.Timeout(seconds, connect=min(seconds, CONNECT_TIMEOUT_SECONDS))


#: The statuses whose stated reason is carried to the caller: both name a fault in the body *we*
#: built from the caller's request — refused as written, or too large for the provider — which is
#: the most actionable thing anybody gets.
_REASON_CARRIED = frozenset({400, 413})


def refused(response: httpx.Response, name: str) -> UpstreamError:
    """A non-200 answer, with its status so 429/503/504 pass through for every vendor.

    The provider's reason is carried for a ``400`` and a ``413`` only (`upstream_reason`): each
    names a fault in the body *we* built. A 401/403 is about our credentials and a 5xx is the
    provider's internal noise. For every status the **cause it stated** rides along (`FRD-141`):
    its reason word in the message, so a caller can report it, and its explanation on the error,
    for the operator. The message begins with the provider's name, so a reader knows whose answer
    it was.
    """
    detail = upstream_reason(response) if response.status_code in _REASON_CARRIED else ""
    diagnosis = diagnosis_of_response(response)
    said = f" Reason: {diagnosis.reason}." if diagnosis else ""
    return UpstreamError(
        f"{name} returned {response.status_code}.{detail}{said}",
        response.status_code,
        diagnosis,
    )


def unreachable(exc: httpx.HTTPError, name: str) -> UpstreamError:
    """No answer, or an answer that broke off: the exception's type, never its text, which can
    carry a URL with a credential in it — and, where the chain shows it, why (`FRD-141`).

    Said as **who stopped**, because that is what a caller cannot see: this gateway giving up on a
    provider that sent nothing for its time limit is not the provider refusing, and an answer the
    provider broke off is not the gateway failing. Where the gateway stopped, its own status goes
    with it (`UpstreamError.gateway_status`): `504` for its time limit, `503` for its pool.
    """
    diagnosis = diagnosis_of_transport(exc)
    said = f" Reason: {diagnosis.reason}." if diagnosis else ""
    kind = type(exc).__name__
    waited = _allowance(exc)
    if isinstance(exc, httpx.ReadTimeout):
        return UpstreamError(
            f"AIRA stopped waiting: {name} sent nothing for {waited}, the longest this gateway "
            f"waits for it.{said}",
            diagnosis=diagnosis,
            gateway_status=504,
        )
    if isinstance(exc, httpx.WriteTimeout):
        return UpstreamError(
            f"AIRA could not finish sending the request to {name} within {waited}.{said}",
            diagnosis=diagnosis,
            gateway_status=504,
        )
    if isinstance(exc, httpx.PoolTimeout):
        return UpstreamError(
            f"AIRA had no free connection to {name} within {waited}: too many requests to it "
            f"were in flight at once.{said}",
            diagnosis=diagnosis,
            gateway_status=503,
        )
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout | httpx.ProxyError):
        return UpstreamError(f"AIRA could not reach {name} ({kind}).{said}", diagnosis=diagnosis)
    if isinstance(exc, httpx.RemoteProtocolError | httpx.ReadError):
        return UpstreamError(
            f"{name} broke off the connection ({kind}).{said}", diagnosis=diagnosis
        )
    return UpstreamError(f"{name} error: {kind}.{said}", diagnosis=diagnosis)


def _allowance(exc: httpx.HTTPError) -> str:
    """How long this gateway gave the step that ran out, as httpx recorded it on the request —
    ``"its time limit"`` where it did not."""
    try:
        limits = exc.request.extensions.get("timeout") or {}
    except RuntimeError:  # httpx raises when an exception carries no request
        return "its time limit"
    phases: dict[type[httpx.HTTPError], str] = {
        httpx.ReadTimeout: "read",
        httpx.WriteTimeout: "write",
        httpx.PoolTimeout: "pool",
        httpx.ConnectTimeout: "connect",
    }
    key = phases.get(type(exc))
    seconds = limits.get(key) if key else None
    return f"{seconds:g} s" if isinstance(seconds, int | float) else "its time limit"


def json_object(response: httpx.Response, name: str) -> dict[str, Any]:
    """The body of a 200, which every upstream answers with a JSON object."""
    try:
        data = response.json()
    except ValueError:
        raise UpstreamError(f"{name} answered with a body that is not JSON.", BAD_ANSWER) from None
    if not isinstance(data, dict):
        raise UpstreamError(f"{name} answered with JSON that is not an object.", BAD_ANSWER)
    return data


@asynccontextmanager
async def opened_stream(
    client: httpx.AsyncClient, url: str, name: str, **request: Any
) -> AsyncIterator[httpx.Response]:
    """A streamed POST whose status is judged **before** anything is read from it.

    Judged on entry, so a refusal is a status the surface can still send rather than a stream that
    stops after our own headers have gone out. A refusal's body is read first — a streamed response
    has the status and not the body, and a 400's reason is in the body — and the response is closed
    before the refusal is raised: under regional failover and the fallback chain a refused stream is
    an ordinary step, and one left open holds its connection out of the pool.
    """
    try:
        async with client.stream("POST", url, **request) as response:
            if response.status_code != httpx.codes.OK:
                # A body that cannot be read leaves the refusal standing without its reason.
                with suppress(httpx.HTTPError):
                    await response.aread()
                raise refused(response, name)
            yield response
    except httpx.HTTPError as exc:
        raise unreachable(exc, name) from exc


async def sse_payloads(response: httpx.Response, name: str) -> AsyncGenerator[dict[str, Any]]:
    """Each ``data:`` payload of a server-sent-event stream, parsed into an object.

    Other lines — `event:` names, comments, keep-alives — carry nothing a dialect reads (every
    vendor repeats the event type inside the payload), and OpenAI's ``[DONE]`` sentinel is the end
    of the stream rather than an event. A connection that breaks while the stream is read, and a
    payload that does not parse, are the upstream's failure and say so.
    """
    try:
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = json.loads(payload)
            except ValueError:
                raise UpstreamError(
                    f"{name} sent a stream event that is not JSON.", BAD_ANSWER
                ) from None
            if isinstance(parsed, dict):
                yield parsed
    except httpx.HTTPError as exc:
        raise unreachable(exc, name) from exc
