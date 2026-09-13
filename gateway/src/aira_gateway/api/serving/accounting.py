"""Recording a request, however it ended.

After dispatch, :func:`accounting` holds the reservation and one exit settles and records it, so
served, failed and abandoned requests leave the same evidence (`FRD-128`). A request that reached
an upstream is recorded even when the caller hung up mid-answer (`499`, `client_gone`).

Before dispatch, a refusal propagates to the surface's exception boundary, which renders it in its
own envelope and calls :func:`record_refusal` — one function, so both surfaces write the same row.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from aira_common.logging import get_logger
from aira_gateway.api.serving.context import attribution_of, provenance
from aira_gateway.api.serving.prepare import Prepared
from aira_gateway.api.serving.refusals import REFUSALS, refusal_outcome
from aira_gateway.audit import AuditTrail, Outcome, decision_summary, tool_summary
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage
from aira_gateway.persistence.recorder import record_request
from aira_gateway.state import budgets_of, pricing_of

_log = get_logger("aira_gateway")

#: The status recorded when the caller left before anything was produced. Nobody is sent it; it
#: tells the audit that case apart from a served one (Google's `CANCELLED` maps to 499 as well).
CLIENT_CLOSED_REQUEST = 499


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# == after dispatch ===============================================================================


@dataclass(slots=True)
class Accounting:
    """What a request produced, reported into the sequence that accounts for it."""

    response: CanonicalResponse | None = None
    payload: dict[str, Any] | None = None
    usage: CanonicalUsage | None = None
    model: str = ""
    #: Overwritten by whatever actually happened; the default says the caller left.
    status: int = CLIENT_CLOSED_REQUEST
    outcome: Outcome | None = None
    #: Whether anything was produced. Not `usage is None`: an embedding produces vectors and
    #: reports no tokens, and settling it as "nothing" would hide batched traffic from a limit.
    produced: bool = False
    #: What this call weighs against a request-limited budget: one, or one per text in a batch.
    requests: int = 1
    #: Set by `accounting`, so `served` records tool calls on every exit alike.
    trail: AuditTrail | None = None

    def served(
        self,
        model: str,
        usage: CanonicalUsage | None,
        payload: dict[str, Any],
        tool_calls: Sequence[str] = (),
    ) -> None:
        self.model = model
        self.usage = usage
        self.payload = payload
        self.status = 200
        self.outcome = Outcome.SERVED
        self.produced = True
        if tool_calls and self.trail is not None:
            self.trail.tool_calls = list(tool_calls)

    def embedded(
        self,
        model: str,
        payload: dict[str, Any],
        *,
        units: int,
        vectors: Sequence[list[float]] = (),
    ) -> None:
        """Vectors, weighed as the many requests they are (`FRD-113` FR-6).

        Priced by the input tokens the adapter reported (`FRD-403`) and recorded where it says they
        were produced (`FRD-115` FR-10). An adapter that reports neither leaves the row unpriced
        and the region to configuration — unknown, never zero.
        """
        tokens = getattr(vectors, "input_tokens", None)
        usage: CanonicalUsage | None = None
        if tokens is not None:
            usage = CanonicalUsage(prompt_tokens=tokens, completion_tokens=0)
        self.served(model, usage, payload)
        self.requests = units
        region = getattr(vectors, "served_region", "")
        if region and self.trail is not None:
            self.trail.served_region = region

    def abandoned(
        self,
        model: str,
        usage: CanonicalUsage | None,
        payload: dict[str, Any],
        tool_calls: Sequence[str] = (),
    ) -> None:
        """The caller left mid-stream after the upstream had reported usage (`FRD-128` FR-2).

        What was reported was produced and reached them, so it is settled; the row still says
        `499`/`client_gone` rather than that the request was served.
        """
        self.served(model, usage, payload, tool_calls)
        self.status = CLIENT_CLOSED_REQUEST
        self.outcome = Outcome.CLIENT_GONE

    def failed(self, status: int, outcome: Outcome) -> None:
        self.status = status
        self.outcome = outcome


@asynccontextmanager
async def accounting(
    request: Request,
    trail: AuditTrail,
    prepared: Prepared,
    *,
    api: str,
    operation: str,
    started: float,
) -> AsyncIterator[Accounting]:
    """Hold the reservation, and account for the request **however it ends**.

    - The stored body is read off the trail, which holds the pipeline's rewrite (`FRD-309`).
    - A refusal propagating to the surface is recorded there, by :func:`record_refusal`; this exit
      only settles, so no request gets two rows. Any other exception is this gateway failing, and
      nothing else would record it: it leaves a `500`/`internal_error` row here.
    - The settle-and-record is **shielded**: a dropped socket cancels the task, and an unshielded
      `await` in `finally` would lose exactly the row this exists to write.
    - Nothing produced means the reservation is released, not settled, so a caller who hung up or
      an upstream outage never consumes a request limit.
    """
    state = Accounting(model=prepared.model, trail=trail)
    record = True
    # Inside `hold`, not around it: outside, `hold` would release the reservation on the way out
    # and the settle would book it a second time.
    async with budgets_of(request).hold(prepared.reservation):
        try:
            yield state
        except asyncio.CancelledError, GeneratorExit:
            # The caller left: nobody else will write a row, so this exit must.
            raise
        except REFUSALS:
            record = False
            raise
        except BaseException:
            state.failed(500, Outcome.INTERNAL_ERROR)
            raise
        finally:
            await asyncio.shield(
                _settle_and_record(
                    request,
                    trail,
                    prepared,
                    state,
                    api=api,
                    operation=operation,
                    started=started,
                    record=record,
                )
            )


async def _settle_and_record(
    request: Request,
    trail: AuditTrail,
    prepared: Prepared,
    state: Accounting,
    *,
    api: str,
    operation: str,
    started: float,
    record: bool = True,
) -> None:
    model = state.model or prepared.model
    cost = await pricing_of(request).cost_nanos(model, state.usage)
    if not state.produced:
        # Nothing chargeable: nothing is settled, and `hold` has already given the unresolved
        # reservation back — releasing here too would count the give-back twice.
        cost = None
    else:
        await budgets_of(request).settle(
            prepared.reservation,
            state.usage.total_tokens if state.usage else 0,
            cost_nanos=cost,
            requests=state.requests,
        )
    if not record:
        return
    await record_request(
        request,
        operation=operation,
        model=trail.served_model or model,
        status=state.status,
        usage=state.usage,
        latency_ms=elapsed_ms(started),
        request_payload=trail.body,
        response_payload=state.payload,
        cost_nanos=cost,
        outcome=state.outcome or Outcome.CLIENT_GONE,
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        tool_calls=tool_summary(trail),
        provenance=await provenance(request, model, trail.served_region),
        api=api,
    )


# == refused before dispatch ======================================================================


async def record_refusal(
    request: Request, trail: AuditTrail, exc: Exception, *, status: int, started: float
) -> None:
    """Write the audit row for a request that was refused (`FRD-122`).

    The reason lives in ``outcome``, which is groupable; the message stays in the response and the
    log. A request refused before authentication resolved an attribution is not recorded: a 401 is
    an authentication event, and a row per unauthenticated request would make the audit table a
    denial-of-service target.

    A failed write never turns a correct refusal into a 500 — that would invite the retry storm a
    limit exists to prevent (`FRD-122` FR-7) — and it is never silent either.
    """
    if attribution_of(request) is None:
        return
    try:
        await _write_refusal(request, trail, exc, status=status, started=started)
    except Exception:  # noqa: BLE001 — see above
        _log.error(
            "audit_refusal_not_recorded",
            operation=trail.operation,
            model=trail.served_model,
            status=status,
            outcome=str(refusal_outcome(exc)),
            exc_info=True,
        )


async def _write_refusal(
    request: Request, trail: AuditTrail, exc: Exception, *, status: int, started: float
) -> None:
    await record_request(
        request,
        operation=trail.operation,
        model=trail.served_model,
        status=status,
        usage=None,
        latency_ms=elapsed_ms(started),
        request_payload=trail.body,
        response_payload=None,
        outcome=refusal_outcome(exc),
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        # What the request offered, even though it was refused (`FRD-131` FR-7).
        tool_calls=tool_summary(trail),
        provenance=await provenance(request, trail.served_model, trail.served_region),
        api=trail.api,
    )
