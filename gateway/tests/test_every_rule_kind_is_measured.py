"""A rule the console can create is a rule the engine measures.

`evaluate_rule` ends with a branch that returns no findings for a kind it does not implement, and
that branch is deliberate: a newer Management may publish a kind this gateway has never heard of,
and *measuring nothing* is the only safe answer — passing would report a rule as having found
nothing, which is a statement about traffic rather than about this version.

The cost of that decision is that a **same-version** gap looks identical. A kind added to the
closed vocabulary and to Management's choices, and not to the evaluator, is a rule an administrator
creates, sees listed as enabled, and which measures nothing for ever — the badge-wearing absent
control `FRD-125` named, one layer further out.

Nothing was comparing the two. Written after the console was found offering a kind that does not
exist (`token_spike`) and omitting one that does (`blocked_prompt_rate`) — the same drift in the
other copy of the same vocabulary.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aira_common.anomalies import RATIO_KINDS, RuleKind
from aira_gateway.anomalies.evaluator import _COUNTED_OUTCOMES, evaluate_rule
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import AnomalyRuleRead


def test_every_kind_in_the_vocabulary_has_a_branch() -> None:
    """Read off the tables the dispatch uses, so it cannot agree with itself: `_COUNTED_OUTCOMES`
    and `RATIO_KINDS` are the same objects `evaluate_rule` branches on."""
    dispatched = (
        set(_COUNTED_OUTCOMES) | set(RATIO_KINDS) | {RuleKind.PAYLOAD_SIZE, RuleKind.NEW_SOURCE_IP}
    )

    assert dispatched == set(RuleKind), (
        f"kinds the vocabulary defines and the engine does not measure: "
        f"{sorted(k.value for k in set(RuleKind) - dispatched)}\n"
        f"kinds the engine measures and the vocabulary does not define: "
        f"{sorted(getattr(k, 'value', k) for k in dispatched - set(RuleKind))}\n\n"
        "A kind with no branch falls to the forward-compatibility return, which measures nothing "
        "and looks exactly like a rule that found nothing."
    )


@pytest.mark.parametrize("kind", sorted(RuleKind, key=lambda k: k.value))
async def test_every_kind_can_actually_be_evaluated(kind: RuleKind) -> None:
    """And it runs. The table above proves a branch exists; this proves the branch works against a
    real schema — a query naming a column that is not there fails here and nowhere else, because
    the detector swallows a bad tick on purpose so one broken rule cannot stop the others."""
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = build_sessionmaker(engine)
    rule = AnomalyRuleRead(
        id=1,
        use_case="uc",
        name=f"{kind.value} probe",
        kind=kind.value,
        window_minutes=15,
        threshold=50,
        parameter=1000,
        min_sample=1,
        action="alert",
        target="subject",
        enabled=True,
    )

    async with sessions() as session:
        assert isinstance(session, AsyncSession)
        findings = await evaluate_rule(session, rule, datetime.now(UTC))

    # No traffic, so no finding — the point is that asking did not raise.
    assert findings == []
    await engine.dispose()
