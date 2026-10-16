"""Which counter a budget's consumption is kept under, and how a refusal names the budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from aira_common.money import format_display
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.db.models import BudgetRead
from aira_gateway.scopes import INSTALLATION, USE_CASE, Scope

#: The refusal for each dimension a budget can cap, by the ledger's name for it.
_BREACH_MESSAGES = {
    "cost": "Cost budget exhausted for {scope} ({period}).",
    "requests": "Request budget exhausted for {scope} ({period}).",
    "tokens": "Token budget exhausted for {scope} ({period}).",
}


#: The refusal when this request's own estimate is more than the whole limit: no reset helps it.
_ALONE_MESSAGES = {
    "cost": (
        "Cost budget for {scope} ({period}) cannot take this request: it alone is estimated at "
        "{asked}, more than the whole limit of {limit}. Asking for fewer output tokens can fit."
    ),
    "requests": (
        "Request budget for {scope} ({period}) cannot take this request: its limit is {limit}."
    ),
    "tokens": (
        "Token budget for {scope} ({period}) cannot take this request: it alone is estimated at "
        "{asked} tokens, more than the whole limit of {limit}. Asking for fewer output tokens "
        "can fit."
    ),
}

#: Where each dimension's limit and this request's amount are read from.
_LIMIT_FIELDS = {"cost": "limit_cost_nanos", "requests": "limit_requests", "tokens": "limit_tokens"}
_AMOUNT_FIELDS = {"cost": "cost_nanos", "requests": "requests", "tokens": "tokens"}


def period_key(period: str, now: datetime) -> str:
    """The counter key for this moment: the **UTC** calendar day or month.

    Converted here rather than assumed of the caller, so the boundary does not depend on every
    call site passing a UTC moment. The console's Period control tells readers it is UTC.
    """
    moment = now.astimezone(UTC)
    return moment.strftime("%Y-%m-%d") if period == "day" else moment.strftime("%Y-%m")


def period_starts(period: str, now: datetime) -> datetime:
    """The first moment of the counter `period_key` names — **UTC**, the other end of
    `period_ends`, decided by the same arithmetic so the two cannot disagree about a boundary."""
    moment = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return moment if period == "day" else moment.replace(day=1)


def period_ends(period: str, now: datetime) -> datetime:
    """When the counter `period_key` names is replaced by the next one — **UTC**, half-open.

    Beside `period_key` because it is the same arithmetic read the other way, and a second place
    that decided where a month ends would eventually disagree with the place that decides where it
    begins. The answer is the *first moment of the next period*, which is the moment the counter
    resets: a caller asking "when does my allowance come back" is told that, not 23:59:59.
    """
    moment = now.astimezone(UTC)
    if period == "day":
        return (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return moment.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


class Unbound(LookupError):
    """A budget asked for the counter of a caller it does not bind — or with a scope this build
    does not know, which binds nobody."""


def scope_key(budget: BudgetRead, caller: str | None = None) -> str:
    """The key this budget's consumption is accounted under.

    Enforcement asks only about budgets `store.applicable` found binding; a reader asking about
    every budget of a use case resolves the scope first (`BudgetService.usage`). An `each_member`
    budget names nobody, so **the caller is the key**: one configured row, one counter per head.
    An explicit check rather than an `assert`, which `python -O` removes.
    """
    scope = Scope.applying(
        scope=budget.scope,
        use_case=budget.use_case,
        caller=caller,
    )
    if scope is None:
        raise Unbound(f"budget {budget.id} ({budget.scope}) does not bind caller {caller!r}")
    return scope.usage_key


def exceeded(
    budget: BudgetRead, dimension: str, now: datetime, asked: Any | None = None
) -> BudgetExceeded:
    """The refusal for ``budget``, breached on ``dimension`` (`cost`, `requests` or `tokens`).

    It says when the period resets (`FRD-628` FR-6), and — where ``asked``, this request's own
    estimate, is more than the whole limit — that it is this request that cannot fit, not a budget
    that is spent: a caller told "exhausted" waits for a reset that will not help them.
    """
    scope, period = _scope_label(budget), budget.period
    resets_at = period_ends(budget.period, now).isoformat()
    limit = getattr(budget, _LIMIT_FIELDS[dimension])
    alone = getattr(asked, _AMOUNT_FIELDS[dimension], 0) if asked is not None else 0
    if limit is not None and alone > limit:
        message = _ALONE_MESSAGES[dimension].format(
            scope=scope,
            period=period,
            asked=_amount(dimension, alone),
            limit=_amount(dimension, limit),
        )
    else:
        message = _BREACH_MESSAGES[dimension].format(scope=scope, period=period)
    return BudgetExceeded(f"{message} It resets at {resets_at}.", resets_at=resets_at)


def _amount(dimension: str, value: int) -> str:
    return format_display(value) if dimension == "cost" else str(value)


def _scope_label(budget: BudgetRead) -> str:
    """How the exhausted budget is named to a caller who did not write the configuration.

    `installation` is named rather than falling through to "member": a refusal naming the wrong
    owner sends somebody to edit a budget that was never involved.
    """
    if budget.scope == INSTALLATION:
        return "installation"
    return "use case" if budget.scope == USE_CASE else "member"
