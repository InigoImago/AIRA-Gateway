"""Which counter a budget's consumption is kept under, and how a refusal names the budget."""

from __future__ import annotations

from datetime import UTC, datetime

from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.db.models import BudgetRead
from aira_gateway.scopes import INSTALLATION, USE_CASE, Scope

#: The refusal for each dimension a budget can cap, by the ledger's name for it.
_BREACH_MESSAGES = {
    "cost": "Cost budget exhausted for {scope} ({period}).",
    "requests": "Request budget exhausted for {scope} ({period}).",
    "tokens": "Token budget exhausted for {scope} ({period}).",
}


def period_key(period: str, now: datetime) -> str:
    """The counter key for this moment: the **UTC** calendar day or month.

    Converted here rather than assumed of the caller, so the boundary does not depend on every
    call site passing a UTC moment. The console's Period control tells readers it is UTC.
    """
    moment = now.astimezone(UTC)
    return moment.strftime("%Y-%m-%d") if period == "day" else moment.strftime("%Y-%m")


def scope_key(budget: BudgetRead, caller: str | None = None) -> str:
    """The key this budget's consumption is accounted under.

    Applicability is decided before this is asked (`store.applicable`). An `each_member` budget
    names nobody, so **the caller is the key**: one configured row, one counter per head.
    """
    scope = Scope.applying(
        scope=budget.scope,
        use_case=budget.use_case,
        caller=caller,
    )
    assert scope is not None, (
        f"budget {budget.id} ({budget.scope}) does not bind caller {caller!r} — it should never "
        "have reached here, since applicable() resolves the same question"
    )
    return scope.usage_key


def exceeded(budget: BudgetRead, dimension: str) -> BudgetExceeded:
    """The refusal for ``budget``, breached on ``dimension`` (`cost`, `requests` or `tokens`)."""
    return BudgetExceeded(
        _BREACH_MESSAGES[dimension].format(scope=_scope_label(budget), period=budget.period)
    )


def _scope_label(budget: BudgetRead) -> str:
    """How the exhausted budget is named to a caller who did not write the configuration.

    `installation` is named rather than falling through to "member": a refusal naming the wrong
    owner sends somebody to edit a budget that was never involved.
    """
    if budget.scope == INSTALLATION:
        return "installation"
    return "use case" if budget.scope == USE_CASE else "member"
