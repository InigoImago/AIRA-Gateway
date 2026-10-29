"""What has been consumed against a budget: for the console's view, and for the caller's own client.

Two endpoints, one question asked from two sides:

- ``/v1beta/usage/{use_case}`` — the console's budget view (`FRD-402`). Read-only, but
  per-use-case operational data, so the caller must be entitled to the use case (`ADR-0007`).
  Entitled to *read* is wider than to *act*: an oversight role reads the per-use-case figure it
  already sees in full on the reporting screen. It also carries ``enforced``, because a figure
  beside a limit that stops nothing is the shape of a working control (`FRD-610` §3.3).
- ``/v1beta/me/budget`` — **the caller's own allowance** (`FRD-139`), for a coding assistant or any
  other client that wants to show what is left before the refusal arrives. No use case in the
  path, no permission beyond being authenticated: it answers about the credential that asked.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from aira_common.clock import week_ends, week_starts
from aira_common.permissions import Permission
from aira_gateway.auth.attribution import Attribution
from aira_gateway.auth.dependencies import (
    authorize_use_case,
    require_attribution,
    require_principal,
    require_valid_use_case,
)
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets import keys
from aira_gateway.budgets.service import BudgetService
from aira_gateway.reporting.service import ReportingService
from aira_gateway.state import budgets_of, clock_of, settings_of

router = APIRouter(tags=["usage"])


@router.get("/v1beta/usage/{use_case}")
async def usage(
    use_case: str, request: Request, principal: Principal = Depends(require_principal)
) -> JSONResponse:
    require_valid_use_case(use_case)
    # `report.read_all` reads; everybody else has to be a member (the `FRD-601` split). A
    # permission, not membership: IT Security is a member of nothing (`ADR-0007`) and must see it.
    if not principal.allows(Permission.REPORT_READ_ALL):
        authorize_use_case(principal, use_case)
    service: BudgetService = budgets_of(request)
    # A per-person budget has one figure per person. The reader is shown their own, keyed by person
    # as the gateway enforces it (`aira_gateway.scopes.person`) — never a named colleague's.
    figures = await service.usage(use_case, subject=principal.person)
    # **Whether any of this stops anything** (`FRD-610` §3.3). A budget with enforcement off keeps
    # its figures, its warnings and its bars, and reads like a working control — and the person
    # editing it is the likeliest to assume it works. Said by the plane that *does* the enforcing:
    # Management has its own copy of this setting and no authority over it.
    return JSONResponse(
        {
            "use_case": use_case,
            "usage": figures,
            "enforced": settings_of(request).enforce_budgets,
        }
    )


@router.get("/v1beta/me/budget")
async def my_budget(
    request: Request,
    principal: Principal = Depends(require_principal),
    attribution: Attribution = Depends(require_attribution),
) -> JSONResponse:
    """What this caller is allowed and what is left of it (`FRD-139`).

    **Why a second endpoint rather than a parameter.** The console asks *about a use case*; a
    client asks *about itself*, and it does not know the slug — an API key is bound to one and the
    agent holding it was configured with a URL and a key, nothing else. Everything here is
    resolved from the credential that called.

    **The use case comes from `require_attribution`**, the same resolver the served verbs use, and
    that is not a detail: the first version read `request.state.attribution` directly and always
    found nothing, because that dependency is mounted on the Gemini router and this endpoint is not
    under it. Every answer said `use_case: null` and carried no allowance — and the hermetic tests
    passed, because they installed a middleware that wrote the attribution themselves. The live
    stack found it in one call. A `GET` *"spends nothing"* (`must_name_a_use_case`), so a caller
    who names none is still answered rather than refused.

    What it answers, in the order somebody reads it:

    - ``allowances`` — one entry per budget that binds **this** caller: the shared pot of their use
      case, and their own counter where the use case gives each member one — their own budget in
      place of the one for everybody where an administrator set one for them (`FRD-407`). Each
      carries the limit, what is used, what is left and when the period rolls over. A colleague's
      counter is never among them, and neither is a budget that gives way to another for them.
    - ``this_period`` — what this caller has spent in the current month **from the audit trail**,
      whether or not any budget exists. A use case with no budget still answers the question the
      feature was asked for ("what have I used"), and a client can show a figure without inventing
      a limit to put beside it.
    - ``today`` and ``this_week`` — the same record over the current day and the week since
      Monday (`FRD-144`): a developer reading a sidebar asks how today went before they ask about
      the month, and a client cannot work either out from a monthly total.
    - ``currency`` — the unit the amounts are in, because the caller cannot work it out and a
      client that guesses puts a wrong unit beside a right number.
    - ``time_zone`` — the clock every period and reset here is on (`AIRA_TIME_ZONE`, `FRD-629`),
      and every moment is written with that clock's offset.
    - ``enforced`` — whether budgets refuse at all in this installation
      (``AIRA_ENFORCE_BUDGETS``). With enforcement off a limit is a figure and not a stop, and a
      client that drew a bar implying otherwise would be wrong in the direction that matters
      (`FRD-610` §3.3).

    Two figures, two sources, deliberately labelled apart: ``allowances`` are the **counters
    enforcement reads** (reset per period, the number that refuses), ``this_period`` is the
    **record** (what the audit trail says the caller spent). They answer different questions and
    can differ — a reservation released, a row retained after a counter reset — and collapsing
    them into one number would make the difference impossible to see.
    """
    use_case = attribution.use_case or None
    person = principal.person

    service: BudgetService = budgets_of(request)
    # The same method the console reads, so the arithmetic of "what is left" exists once
    # (`BudgetService.usage`). `subject=person` is what makes a per-person row this caller's, and
    # `binds` is enforcement's own answer to which rows those are.
    figures = await service.usage(use_case or "", subject=person) if use_case else []
    allowances = [row for row in figures if row["binds"]]

    zone = clock_of(request)
    windows = _windows(datetime.now(UTC), zone)
    reporting: ReportingService = request.app.state.reporting
    # Scoped to this caller's own traffic in their own use case: `person` narrows every figure
    # (`FRD-626` FR-15), so nothing here can carry a colleague's figures whatever the use case's
    # visibility says (`FRD-622` FR-1c).
    today, week, month = await reporting.totals(
        (use_case,) if use_case else (), windows, person=person or ""
    )

    return JSONResponse(
        {
            "use_case": use_case,
            "person": person,
            "enforced": settings_of(request).enforce_budgets,
            # The unit every amount below is in (`AIRA_CURRENCY`). A client cannot derive it and
            # must not assume one: a figure labelled in the wrong currency is read as a fact, and
            # the one client this was built for prints costs with a `$` of its own
            # (`FRD-132` §11.7). No conversion happens anywhere — display only (`FRD-403`).
            "currency": settings_of(request).currency,
            # The clock every period and reset below is on (`AIRA_TIME_ZONE`, `FRD-629`), so a
            # client says "resets 1 Nov" on the date the installation means.
            "time_zone": str(zone),
            "allowances": allowances,
            **{
                name: {
                    "from": start.astimezone(zone).isoformat(),
                    "to": end.astimezone(zone).isoformat(),
                    **figures,
                }
                for name, (start, end), figures in zip(
                    ("today", "this_week", "this_period"),
                    windows,
                    (today, week, month),
                    strict=True,
                )
            },
        }
    )


def _windows(now: datetime, zone: tzinfo = UTC) -> tuple[tuple[datetime, datetime], ...]:
    """Today, this week and this month — calendar windows, half-open, on the installation's
    clock (``zone``, `FRD-629`).

    Not the budget's periods: a budget counts by day or month, and these blocks are the record
    rather than the counter. On the same clock as every counter and reset this endpoint reports, so
    a "today" cannot disagree with the reset beside it. The week starts on Monday (ISO 8601), and
    may begin in the previous month. Where a day and a month begin and end is `budgets.keys`' to
    say, once, and where a week does `aira_common.clock`'s.
    """
    day = keys.period_starts("day", now, zone), keys.period_ends("day", now, zone)
    return (
        day,
        (week_starts(now, zone), week_ends(now, zone)),
        (keys.period_starts("month", now, zone), keys.period_ends("month", now, zone)),
    )
