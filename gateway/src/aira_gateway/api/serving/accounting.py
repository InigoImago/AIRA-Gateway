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
from aira_gateway.api.serving.counting import Unreported, count_unreported, floor_priced
from aira_gateway.api.serving.prepare import Prepared
from aira_gateway.api.serving.refusals import REFUSALS, caller_message, refusal_outcome
from aira_gateway.audit import AuditTrail, Outcome, UsageBasis, decision_summary, tool_summary
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage
from aira_gateway.embedding import describe_vectors
from aira_gateway.persistence.recorder import record_request
from aira_gateway.pipeline.dispatch import passed_over_by
from aira_gateway.shielding import uninterrupted
from aira_gateway.state import budgets_of, pricing_of, settings_of

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
    #: What the caller was told where the request did not succeed (`FRD-628` FR-4).
    error: str | None = None
    #: Whether anything was produced. Not `usage is None`: an embedding produces vectors and
    #: reports no tokens, and settling it as "nothing" would hide batched traffic from a limit.
    produced: bool = False
    #: What this call weighs against a request-limited budget: one, or one per text in a batch.
    requests: int = 1
    #: Set by `accounting`, so `served` records tool calls on every exit alike.
    trail: AuditTrail | None = None
    #: What the settle priced it at, for a refusal's row written after this exit (`record_refusal`).
    cost_nanos: int | None = None
    #: A stream the caller left before its vendor reported usage: counted before the booking.
    unreported: Unreported | None = None
    #: How `usage` was established, where it is not the vendor's final report (`UsageBasis`).
    usage_basis: UsageBasis | None = None

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
        *,
        units: int,
        vectors: Sequence[list[float]] = (),
    ) -> None:
        """Vectors, weighed as the many requests they are (`FRD-113` FR-6).

        Priced by the input tokens the adapter reported (`FRD-403`) and recorded where it says they
        were produced (`FRD-115` FR-10). An adapter that reports neither leaves the row unpriced
        and the region to configuration — unknown, never zero. The row keeps a description of the
        vectors, never the values (`embedding.describe_vectors`), whatever the surface sent.
        """
        tokens = getattr(vectors, "input_tokens", None)
        usage: CanonicalUsage | None = None
        if tokens is not None:
            usage = CanonicalUsage(prompt_tokens=tokens, completion_tokens=0)
        self.served(model, usage, {"vectors": describe_vectors(vectors)})
        self.requests = units
        region = getattr(vectors, "served_region", "")
        if region and self.trail is not None:
            self.trail.served_region = region

    def spent(self, model: str, usage: CanonicalUsage | None) -> None:
        """The upstream answered and billed this, whatever becomes of the answer now.

        Reported before a check that may still refuse the answer — a document the model did not
        finish (`check_structured_result`): the caller is refused, the provider was paid, and a
        release would hide that spend from every budget. Settled either way; the refusal's row
        carries it.
        """
        self.model = model
        self.usage = usage
        self.produced = True

    def abandoned(
        self,
        model: str,
        usage: CanonicalUsage | None,
        payload: dict[str, Any],
        tool_calls: Sequence[str] = (),
        *,
        complete: bool = True,
    ) -> None:
        """The caller left mid-stream after the upstream had reported usage (`FRD-128` FR-2).

        What was reported was produced and reached them, so it is settled; the row still says
        `499`/`client_gone` rather than that the request was served. Reported before the stream
        finished, the figure is the vendor's running total and a floor (`UsageBasis.PARTIAL`).
        """
        self.served(model, usage, payload, tool_calls)
        self.status = CLIENT_CLOSED_REQUEST
        self.outcome = Outcome.CLIENT_GONE
        if not complete:
            self.usage_basis = UsageBasis.PARTIAL

    def left_unreported(
        self,
        model: str,
        unreported: Unreported | None,
        payload: dict[str, Any],
        tool_calls: Sequence[str] = (),
    ) -> None:
        """The caller left before the upstream reported anything it used (`FRD-128` FR-6).

        Nothing is known yet. The exit counts it at the vendor where it can, and settles that;
        where it cannot, nothing was produced as far as anybody can show, the reservation goes back
        and the row's usage stays unknown.
        """
        self.model = model
        self.payload = payload
        self.status = CLIENT_CLOSED_REQUEST
        self.outcome = Outcome.CLIENT_GONE
        self.unreported = unreported
        if tool_calls and self.trail is not None:
            self.trail.tool_calls = list(tool_calls)

    def failed(self, status: int, outcome: Outcome, error: str | None = None) -> None:
        self.status = status
        self.outcome = outcome
        #: What the caller was told, kept on the row (`FRD-628` FR-4).
        self.error = error


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
    - The exit is **one unit that runs to its end** (`shielding.uninterrupted`): settle what was
      produced or give the reservation back, then write the row. A dropped socket cancels the
      task, and it is that request's evidence which matters most. Shielding only the wait was not
      enough: anyio cancels level-triggered, control left the exit while the settle still ran, and
      `hold` released the reservation the settle was about to book — resolving it twice.
    - Nothing produced means the reservation is released, not settled, so a caller who hung up or
      an upstream outage never consumes a request limit.
    """
    state = Accounting(model=prepared.model, trail=trail)
    # For the refusal boundary, which writes the row of a refusal raised in here: what was spent
    # before it belongs on that row.
    request.state.accounted = state
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
            await uninterrupted(
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
    cost: int | None = None
    try:
        if state.unreported is not None and not state.produced:
            await _count_what_was_left(request, state)
        if not state.produced:
            # Nothing chargeable: the reservation is given back — here, inside the one exit, so it
            # is resolved exactly once whatever cancels the request, and `hold` finds nothing to do.
            await budgets_of(request).release(prepared.reservation)
        else:
            # A counted usage is priced as the floor it is (`counting`); a reported one as reported.
            priced = (
                floor_priced(state.usage)
                if state.usage is not None and state.usage_basis is UsageBasis.COUNTED
                else state.usage
            )
            cost = state.cost_nanos = await pricing_of(request).cost_nanos(model, priced)
            await budgets_of(request).settle(
                prepared.reservation,
                state.usage.total_tokens if state.usage else 0,
                cost_nanos=cost,
                requests=state.requests,
            )
    except Exception as exc:
        # **The row is written whatever the booking did.** A request that reached an upstream is
        # recorded however it ended, and a booking that fails — the store away, a total its column
        # cannot hold — must not take the row with it, nor turn an answer the provider has already
        # billed into a 500 the caller retries and pays for again. The failure is the operator's:
        # logged by type, never with values. Admission reads the same store, so the next request
        # is refused at the gate rather than served blind.
        _log.error(
            "budget_settlement_failed",
            error_type=type(exc).__name__,
            model=model,
            settled=state.produced,
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
        usage_basis=state.usage_basis,
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        tool_calls=tool_summary(trail),
        provenance=await provenance(request, model, trail.served_region),
        api=api,
        error=state.error,
    )


async def _count_what_was_left(request: Request, state: Accounting) -> None:
    """Establish what a stream the caller left had used, from the vendor's own count.

    Run inside the exit, where the caller has gone and nobody waits for it, and before the booking
    it informs. Off where the installation does not send the text a second time
    (`AIRA_COUNT_CANCELLED_STREAMS`): the usage then stays unknown.
    """
    if state.unreported is None or not settings_of(request).count_cancelled_streams:
        return
    usage = await count_unreported(state.unreported)
    if usage is not None:
        state.usage = usage
        state.produced = True
        state.usage_basis = UsageBasis.COUNTED


# == refused before dispatch ======================================================================


async def release_reservation(request: Request) -> None:
    """Give back an allowance reserved for a request that was never dispatched.

    `accounting`'s `hold` covers every refusal raised **inside** the block. This covers the window
    before it: the allowance is reserved at the end of the pre-dispatch sequence (`FRD-126`), and
    anything refused between that point and dispatch used to keep it until it expired — a streamed
    request whose chain had nothing left to offer (`FRD-302` FR-6a), an embedding whose model is
    not released, a model the catalogue cannot address. Rare one at a time, and an outage is not
    one at a time: it is every request of that use case, each holding a slice of the month.

    Idempotent, because the ordinary path has already resolved the reservation and a refusal then
    reaches this as well. A failure here never replaces the refusal the caller is owed.
    """
    reservation = getattr(request.state, "reservation", None)
    if reservation is None or reservation.resolved:
        return
    try:
        await budgets_of(request).release(reservation)
    except Exception:  # noqa: BLE001 — see above
        _log.error("reservation_not_released", exc_info=True)


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
    # Before the attribution check: an allowance is given back whether or not the refusal is the
    # kind that leaves a row.
    await release_reservation(request)
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
    # A refusal raised after the upstream answered — a document it did not finish — was paid for,
    # and `accounting` has settled it; the row says what it cost. Before that, nothing was spent.
    accounted: Accounting | None = getattr(request.state, "accounted", None)
    spent = accounted if accounted is not None and accounted.produced else None
    # A chain that ran out of candidates says whom it tried and skipped, and why (`FRD-302` FR-11):
    # on a refused request that is the whole explanation, and the response's message is not kept.
    chain = passed_over_by(exc)
    if chain is not None:
        trail.passed_over(list(chain.skipped), list(chain.failed))
    await record_request(
        request,
        operation=trail.operation,
        model=trail.served_model,
        status=status,
        usage=spent.usage if spent is not None else None,
        latency_ms=elapsed_ms(started),
        request_payload=trail.body,
        response_payload=None,
        cost_nanos=spent.cost_nanos if spent is not None else None,
        usage_basis=spent.usage_basis if spent is not None else None,
        outcome=refusal_outcome(exc),
        requested_model=trail.requested_model,
        model_selection=trail.selection,
        pipeline_decisions=decision_summary(trail.decisions),
        # What the caller was told (`FRD-628` FR-4): under strict data mode it is often all that
        # explains the refusal to the people who run the use case.
        error=caller_message(exc),
        # What the request offered, even though it was refused (`FRD-131` FR-7).
        tool_calls=tool_summary(trail),
        provenance=await provenance(request, trail.served_model, trail.served_region),
        api=trail.api,
    )
