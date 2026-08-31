"""One human, two credentials, one identity — asked of every reader of it (`FRD-613`).

**The sweep, not the field.** This project has now found the same defect five times: a person's
identity is written in two alphabets — an OIDC token's `sub` is a directory id, an API key's is its
owner's *username* — and every place that compares one of them against a stored value is a place
that can pick the wrong one. `LESSONS.md` §1 records the shape and the instruction that follows
from it: *"Correct the definition, then grep for the comparison."* Each time, one reader was
corrected and the next was found weeks later by somebody who could not see their own requests.

So this file does not test `person()`. It enumerates **every reader** — the budget key, the rate
limit bucket, the kill switch, the detector, the trace list, the payload gate, the membership
lookup and the findings list — and asks each the same question with the same two credentials. A
reader added tomorrow that reads `subject` alone is meant to fail here, which is why
:func:`test_every_comparison_against_a_stored_identity_is_named` exists at the bottom: it reads the
source rather than the behaviour, so a *ninth* reader cannot be added silently.

The fixtures give the two credentials the shape production has, and that is deliberate: the
previous round's tests passed for months because their principals carried no `preferred_username`,
so `person` fell back to the subject and **a fixture that makes two things equal cannot tell them
apart**.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.anomalies import RuleAction, RuleKind, RuleTarget
from aira_gateway.anomalies.evaluator import evaluate_rule
from aira_gateway.anomalies.suspensions import Suspended, SuspensionService
from aira_gateway.auth.attribution import Attribution
from aira_gateway.auth.principal import Principal
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import (
    AccessSuspension,
    AnomalyRuleRead,
    RequestLog,
    UseCaseMemberRead,
    UseCaseRead,
)
from aira_gateway.payloads import (
    grant_role_in,
    is_own_request,
    own_requests,
    restricted_use_cases,
)
from aira_gateway.scopes import EACH_MEMBER, INSTALLATION, USE_CASE, Scope, person

#: The same human. Keycloak calls her `ada`; the directory's id for her is a UUID; the API key an
#: administrator issued for her is recorded against the name, because that is the only thing
#: Management can put on the wire.
DIRECTORY_ID = "9f1c3e2a-0000-4000-8000-000000000001"
NAME = "ada"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

SIGNED_IN = Principal(subject=DIRECTORY_ID, method="oidc", username=NAME, credential="console")
WITH_A_KEY = Principal(subject=NAME, method="api_key", username=NAME, credential="ab12cd34")
#: A service account: a real token that carries no `preferred_username`. The fallback is not a
#: formality — without it every nameless caller would share one pot.
NAMELESS = Principal(subject="service-account-batch", method="oidc")

BOTH = pytest.mark.parametrize("principal", [SIGNED_IN, WITH_A_KEY], ids=["oidc", "api_key"])


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


def _row(
    *,
    subject: str,
    username: str | None,
    row_id: str = "r",
    use_case: str | None = "uc-a",
    outcome: str = "served",
    status: int = 200,
    at: datetime | None = None,
) -> RequestLog:
    return RequestLog(
        id=row_id,
        subject=subject,
        username=username,
        auth_method="oidc",
        use_case=use_case,
        api="gemini",
        operation="generateContent",
        model="mock-1",
        status=status,
        outcome=outcome,
        created_at=at or NOW,
    )


# ═══ 1. the definition ══════════════════════════════════════════════════════════════════════════


def test_the_person_is_the_name_when_the_credential_carries_one() -> None:
    assert person(DIRECTORY_ID, NAME) == NAME


def test_the_person_falls_back_to_the_subject_when_nobody_is_named() -> None:
    """Not a formality. Falling back to nothing would put every nameless caller in one shared pot,
    which is the opposite failure and much the worse of the two."""
    assert person("service-account-batch", None) == "service-account-batch"


def test_an_empty_name_is_not_a_name() -> None:
    assert person(DIRECTORY_ID, "") == DIRECTORY_ID


def test_a_caller_with_neither_is_nobody() -> None:
    assert person(None, None) is None


@BOTH
def test_both_credentials_answer_with_one_person(principal: Principal) -> None:
    assert principal.person == NAME


@BOTH
def test_the_attribution_answers_the_same_as_the_principal(principal: Principal) -> None:
    """Two carriers of one rule. A route has an `Attribution`, a read-only endpoint has only the
    `Principal`, and an allowance must not depend on which one happened to be in scope."""
    attribution = Attribution(
        subject=principal.subject,
        method=principal.method,
        use_case="uc-a",
        username=principal.username,
    )
    assert attribution.person == principal.person


def test_a_nameless_principal_keys_on_its_subject() -> None:
    assert NAMELESS.person == "service-account-batch"


# ═══ 2. what the allowance is counted under ═════════════════════════════════════════════════════


@BOTH
def test_one_person_has_one_budget_counter(principal: Principal) -> None:
    scope = Scope.applying(scope=EACH_MEMBER, use_case="uc-a", caller=principal.person)
    assert scope is not None
    assert scope.usage_key == f"member:uc-a:{NAME}"


@BOTH
def test_one_person_has_one_rate_limit_bucket(principal: Principal) -> None:
    scope = Scope.applying(scope=EACH_MEMBER, use_case="uc-a", caller=principal.person)
    assert scope is not None
    assert scope.bucket_key == f"rl:{{uc-a}}:member:{NAME}"


def test_the_two_credentials_would_have_had_two_counters_on_the_subject() -> None:
    """The defect this closed, stated as the thing that must stay untrue.

    Keying on the subject gave one person two per-head budgets and two buckets: a limit of ten
    meant twenty to anybody holding both credentials.
    """
    on_subject = {
        Scope.applying(scope=EACH_MEMBER, use_case="uc-a", caller=p.subject).usage_key  # type: ignore[union-attr]
        for p in (SIGNED_IN, WITH_A_KEY)
    }
    on_person = {
        Scope.applying(scope=EACH_MEMBER, use_case="uc-a", caller=p.person).usage_key  # type: ignore[union-attr]
        for p in (SIGNED_IN, WITH_A_KEY)
    }
    assert len(on_subject) == 2
    assert len(on_person) == 1


def test_a_use_case_budget_binds_everybody_regardless_of_who_asks() -> None:
    assert Scope.applying(scope=USE_CASE, use_case="uc-a", caller=None) == Scope("uc-a")


def test_a_per_head_budget_binds_nobody_when_the_request_names_nobody() -> None:
    assert Scope.applying(scope=EACH_MEMBER, use_case="uc-a", caller=None) is None


def test_the_installation_budget_takes_exactly_what_no_use_case_owns() -> None:
    assert Scope.applying(scope=INSTALLATION, use_case="", caller=NAME) == Scope("")
    assert Scope.applying(scope=INSTALLATION, use_case="uc-a", caller=NAME) is None


def test_the_installation_counter_is_not_a_use_case_with_an_empty_name() -> None:
    """`uc:` would be swept by `_delete_usecase`'s `uc:{slug}` prefix with an empty slug — which is
    every counter there is."""
    assert Scope("").usage_key == "installation:"
    assert Scope("").bucket_key == "rl:{installation}:all"


def test_an_unknown_scope_binds_nobody() -> None:
    assert Scope.applying(scope="per_department", use_case="uc-a", caller=NAME) is None


# ═══ 3. the kill switch ═════════════════════════════════════════════════════════════════════════


async def _suspend(sessions: async_sessionmaker[AsyncSession], **kwargs: object) -> None:
    async with sessions() as session:
        session.add(AccessSuspension(action=RuleAction.BLOCK.value, author="user:root", **kwargs))
        await session.commit()


@pytest.mark.asyncio
@BOTH
async def test_stopping_a_person_by_name_stops_them_whichever_credential_they_use(
    sessions: async_sessionmaker[AsyncSession], principal: Principal
) -> None:
    """The name is the one string both credentials answer to, and this is what it buys.

    Measured before this held: `target_value: "ada"` blocked her key and served her browser — a
    kill switch that stopped whichever half of somebody the operator happened to copy the subject
    from.
    """
    await _suspend(sessions, target=RuleTarget.SUBJECT.value, target_value=NAME)
    service = SuspensionService(sessions)
    with pytest.raises(Suspended):
        await service.check("uc-a", principal.subject, principal.credential, principal.person)


@pytest.mark.asyncio
async def test_a_directory_id_stops_the_token_and_says_nothing_about_a_key() -> None:
    """The asymmetry, asserted rather than left to be discovered.

    An API key never carries a directory id — Management cannot put one on the wire, because the
    key is issued against a *username* — so a suspension typed as a `sub` is a statement about
    tokens from that realm and nothing else. It is not a gap this side can close: nothing here can
    know that a directory id and a username are the same human without asking the directory, which
    is the one thing the request path may not do (`FRD-204`).

    So it is a **documented instruction**: to stop a person, name them the way the trace list's
    `username` column does. `docs/INTEGRATIONS.md` §2 says so, and this test is why that sentence
    has to stay there.
    """
    assert WITH_A_KEY.subject != DIRECTORY_ID
    assert WITH_A_KEY.person == SIGNED_IN.person


@pytest.mark.asyncio
async def test_stopping_a_credential_does_not_stop_the_person_holding_it(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The separation that makes three targets worth having. *"Block this leaked key"* must not
    stop its owner, or an incident response is a punishment."""
    await _suspend(sessions, target=RuleTarget.CREDENTIAL.value, target_value=WITH_A_KEY.credential)
    service = SuspensionService(sessions)
    with pytest.raises(Suspended):
        await service.check("uc-a", WITH_A_KEY.subject, WITH_A_KEY.credential, WITH_A_KEY.person)
    service.invalidate()
    assert (
        await service.check("uc-a", SIGNED_IN.subject, SIGNED_IN.credential, SIGNED_IN.person) == []
    )


@pytest.mark.asyncio
async def test_a_suspension_naming_a_person_does_not_stop_a_colleague(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _suspend(sessions, target=RuleTarget.SUBJECT.value, target_value=NAME)
    service = SuspensionService(sessions)
    assert await service.check("uc-a", "bob", "ef56", "bob") == []


@pytest.mark.asyncio
async def test_a_suspension_scoped_to_one_use_case_leaves_the_others_alone(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _suspend(sessions, use_case="uc-a", target=RuleTarget.SUBJECT.value, target_value=NAME)
    service = SuspensionService(sessions)
    assert await service.check("uc-b", DIRECTORY_ID, "console", NAME) == []


@pytest.mark.asyncio
async def test_a_nameless_caller_is_still_stoppable_by_their_subject(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _suspend(sessions, target=RuleTarget.SUBJECT.value, target_value="service-account-batch")
    service = SuspensionService(sessions)
    with pytest.raises(Suspended):
        await service.check("uc-a", NAMELESS.subject, None, NAMELESS.person)


@pytest.mark.asyncio
async def test_an_expired_suspension_stops_refusing_the_moment_it_runs_out(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        session.add(
            AccessSuspension(
                target=RuleTarget.SUBJECT.value,
                target_value=NAME,
                action=RuleAction.BLOCK.value,
                author="user:root",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()
    assert await SuspensionService(sessions).check("uc-a", DIRECTORY_ID, None, NAME) == []


# ═══ 4. the detector ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_rule_about_a_person_counts_both_of_their_credentials(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """One person, sixty refusals, thirty through each credential.

    Grouped by `subject` this is two callers of thirty and a rule set at fifty never fires — the
    detector's blind spot is exactly the size of somebody's second credential.
    """
    async with sessions() as session:
        for index in range(30):
            session.add(
                _row(
                    subject=DIRECTORY_ID,
                    username=NAME,
                    row_id=f"oidc-{index}",
                    outcome="invalid_request",
                    status=400,
                )
            )
            session.add(
                _row(
                    subject=NAME,
                    username=NAME,
                    row_id=f"key-{index}",
                    outcome="invalid_request",
                    status=400,
                )
            )
        session.add(
            AnomalyRuleRead(
                id=1,
                name="refusals",
                use_case=None,
                kind=RuleKind.REFUSAL_RATE.value,
                target=RuleTarget.SUBJECT.value,
                threshold=50,
                window_minutes=60,
                # **Above what either credential produces on its own**, which is what makes this a
                # test of the grouping rather than of the threshold.
                min_sample=50,
                action=RuleAction.BLOCK.value,
                enabled=True,
            )
        )
        await session.commit()
        rule = (await session.execute(select(AnomalyRuleRead))).scalar_one()
        findings = await evaluate_rule(session, rule, NOW + timedelta(minutes=1))

    assert [f.target_value for f in findings] == [NAME]
    # Sixty requests in one bucket. Grouped by `subject` this was two callers of thirty, each
    # below `min_sample`, and the rule found nothing at all.
    assert findings[0].sample == 60


@pytest.mark.asyncio
async def test_a_finding_about_a_nameless_caller_is_filed_under_its_subject(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The same fallback the allowance makes. A row written before names were recorded has none,
    and it must not join everybody else's bucket."""
    async with sessions() as session:
        for index in range(20):
            session.add(
                _row(
                    subject="service-account-batch",
                    username=None,
                    row_id=f"old-{index}",
                    outcome="invalid_request",
                    status=400,
                )
            )
        session.add(
            AnomalyRuleRead(
                id=1,
                name="refusals",
                use_case=None,
                kind=RuleKind.REFUSAL_RATE.value,
                target=RuleTarget.SUBJECT.value,
                threshold=50,
                window_minutes=60,
                min_sample=10,
                action=RuleAction.BLOCK.value,
                enabled=True,
            )
        )
        await session.commit()
        rule = (await session.execute(select(AnomalyRuleRead))).scalar_one()
        findings = await evaluate_rule(session, rule, NOW + timedelta(minutes=1))

    assert [f.target_value for f in findings] == ["service-account-batch"]


@pytest.mark.asyncio
async def test_an_empty_name_is_read_as_no_name_by_the_detector_too(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`nullif`, and this is the case it is for: SQL reads `''` as a value and `person` reads it
    as absence. No writer produces one today, and the two spellings of one rule have to agree
    anyway."""
    async with sessions() as session:
        for index in range(20):
            session.add(
                _row(
                    subject="service-account-batch",
                    username="",
                    row_id=f"blank-{index}",
                    outcome="invalid_request",
                    status=400,
                )
            )
        session.add(
            AnomalyRuleRead(
                id=1,
                name="refusals",
                use_case=None,
                kind=RuleKind.REFUSAL_RATE.value,
                target=RuleTarget.SUBJECT.value,
                threshold=50,
                window_minutes=60,
                min_sample=10,
                action=RuleAction.BLOCK.value,
                enabled=True,
            )
        )
        await session.commit()
        rule = (await session.execute(select(AnomalyRuleRead))).scalar_one()
        findings = await evaluate_rule(session, rule, NOW + timedelta(minutes=1))

    assert [f.target_value for f in findings] == ["service-account-batch"]


@pytest.mark.asyncio
async def test_a_credential_rule_still_measures_the_credential(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Widening `subject` must not widen its neighbour: a key is a thing in its own right, and
    "which key is doing this" is the question a leak is investigated with."""
    async with sessions() as session:
        for index in range(20):
            row = _row(
                subject=NAME,
                username=NAME,
                row_id=f"k-{index}",
                outcome="invalid_request",
                status=400,
            )
            row.credential = "ab12cd34"
            session.add(row)
        session.add(
            AnomalyRuleRead(
                id=1,
                name="refusals",
                use_case=None,
                kind=RuleKind.REFUSAL_RATE.value,
                target=RuleTarget.CREDENTIAL.value,
                threshold=50,
                window_minutes=60,
                min_sample=10,
                action=RuleAction.BLOCK.value,
                enabled=True,
            )
        )
        await session.commit()
        rule = (await session.execute(select(AnomalyRuleRead))).scalar_one()
        findings = await evaluate_rule(session, rule, NOW + timedelta(minutes=1))

    assert [f.target_value for f in findings] == ["ab12cd34"]


# ═══ 5. what a person may see ═══════════════════════════════════════════════════════════════════


@BOTH
@pytest.mark.parametrize(
    ("row_subject", "row_username"),
    [(DIRECTORY_ID, NAME), (NAME, NAME), (NAME, None)],
    ids=["oidc-row", "key-row", "legacy-key-row"],
)
def test_a_person_recognises_their_own_request_whichever_way_it_was_made(
    principal: Principal, row_subject: str, row_username: str | None
) -> None:
    """Three kinds of row a real installation holds, against both ways of reading them.

    The console is always OIDC and the traffic is usually a key, so the ordinary case compares a
    directory id against a username — and a member of a use case that shows each member their own
    requests was refused **their own** prompt with their own rows in the table.
    """
    assert is_own_request(principal, _row(subject=row_subject, username=row_username))


def test_a_row_from_before_names_were_recorded_is_recognised_by_its_own_credential() -> None:
    """The fourth kind, and the limit of what can be known about it.

    A row carrying a directory id and no name is recognisable to the token that made it and to
    nothing else: there is no name on the row to meet the key's alphabet in. Kept visible to the
    credential that can prove it rather than hidden from everybody, which is the direction that
    loses no history.
    """
    legacy = _row(subject=DIRECTORY_ID, username=None)
    assert is_own_request(SIGNED_IN, legacy)
    assert not is_own_request(WITH_A_KEY, legacy)


@BOTH
def test_a_colleagues_request_stays_somebody_elses(principal: Principal) -> None:
    assert not is_own_request(principal, _row(subject="bob", username="bob"))


def test_a_name_is_never_matched_against_another_persons_subject() -> None:
    """The narrowing this widening must not have done. A row whose *subject* happens to equal
    somebody else's *name* is still not theirs — which is only safe because the directory does not
    let people rename themselves onto a colleague (`docs/INTEGRATIONS.md` §2)."""
    bob_key_row = _row(subject="bob", username=None)
    assert not is_own_request(SIGNED_IN, bob_key_row)


@pytest.mark.asyncio
@BOTH
async def test_the_query_and_the_predicate_agree_about_who_somebody_is(
    sessions: async_sessionmaker[AsyncSession], principal: Principal
) -> None:
    """One rule, two forms. A predicate and a query that disagree is how a payload comes to be
    refused on a screen that lists the row."""
    rows = [
        _row(subject=DIRECTORY_ID, username=NAME, row_id="a"),
        _row(subject=NAME, username=NAME, row_id="b"),
        _row(subject=DIRECTORY_ID, username=None, row_id="c"),
        _row(subject="bob", username="bob", row_id="d"),
        _row(subject="bob", username=None, row_id="e"),
    ]
    async with sessions() as session:
        for row in rows:
            session.add(row)
        await session.commit()
        selected = {
            str(found)
            for found in (
                await session.execute(select(RequestLog.id).where(own_requests(principal)))
            ).scalars()
        }
    assert selected == {row.id for row in rows if is_own_request(principal, row)}


# ═══ 6. what a person may do inside a use case ══════════════════════════════════════════════════


async def _use_case(
    sessions: async_sessionmaker[AsyncSession], *, restricted: bool = False, role: str = "admin"
) -> None:
    async with sessions() as session:
        session.add(UseCaseRead(slug="uc-a", name="A", restrict_members_to_own_requests=restricted))
        session.add(UseCaseMemberRead(use_case_slug="uc-a", subject=NAME, role=role))
        await session.commit()


@pytest.mark.asyncio
@BOTH
async def test_a_membership_row_is_read_in_the_alphabet_it_is_written_in(
    sessions: async_sessionmaker[AsyncSession], principal: Principal
) -> None:
    """`use_case_members.subject` holds a **username** — Management emits one and the consumer
    writes it. Read against `principal.subject` it never matched an OIDC caller, so no console
    user was ever recognised as an administrator of their own use case."""
    await _use_case(sessions)
    holder = replace(principal, use_cases=("uc-a",))
    async with sessions() as session:
        assert await grant_role_in(session, holder, "uc-a") == "admin"


@pytest.mark.asyncio
async def test_a_use_case_the_caller_does_not_hold_answers_nothing(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _use_case(sessions)
    async with sessions() as session:
        assert await grant_role_in(session, SIGNED_IN, "uc-a") is None


@pytest.mark.asyncio
async def test_a_group_grant_carries_its_role_without_a_membership_row(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A grant on a *group* writes no row in `use_case_members` — that is what makes it a group
    grant — so reading that table alone answered `user` for a deliberate administrator."""
    async with sessions() as session:
        session.add(UseCaseRead(slug="uc-a", name="A"))
        await session.commit()
    granted = Principal(
        subject=DIRECTORY_ID,
        method="oidc",
        username=NAME,
        use_cases=("uc-a",),
        grants=(("uc-a", "admin"),),
    )
    async with sessions() as session:
        assert await grant_role_in(session, granted, "uc-a") == "admin"


@pytest.mark.asyncio
async def test_the_stronger_of_two_routes_wins(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Being granted twice over is being granted, and which row was read first is not a thing an
    access decision may depend on."""
    await _use_case(sessions, role="user")
    granted = Principal(
        subject=DIRECTORY_ID,
        method="oidc",
        username=NAME,
        use_cases=("uc-a",),
        grants=(("uc-a", "admin"),),
    )
    async with sessions() as session:
        assert await grant_role_in(session, granted, "uc-a") == "admin"


@pytest.mark.asyncio
@BOTH
async def test_an_administrator_is_never_restricted_inside_their_own_use_case(
    sessions: async_sessionmaker[AsyncSession], principal: Principal
) -> None:
    await _use_case(sessions, restricted=True, role="admin")
    holder = Principal(
        subject=principal.subject,
        method=principal.method,
        username=principal.username,
        use_cases=("uc-a",),
    )
    async with sessions() as session:
        assert await restricted_use_cases(session, holder) == []


@pytest.mark.asyncio
@BOTH
async def test_a_member_is_restricted_where_the_use_case_says_so(
    sessions: async_sessionmaker[AsyncSession], principal: Principal
) -> None:
    await _use_case(sessions, restricted=True, role="user")
    holder = Principal(
        subject=principal.subject,
        method=principal.method,
        username=principal.username,
        use_cases=("uc-a",),
    )
    async with sessions() as session:
        assert await restricted_use_cases(session, holder) == ["uc-a"]


@pytest.mark.asyncio
async def test_a_group_granted_administrator_sees_the_whole_list(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The wider blast radius of the same omission: this decides the whole trace **list**, so a
    group-granted administrator saw the figures about their own use case narrowed to their own
    traffic."""
    async with sessions() as session:
        session.add(UseCaseRead(slug="uc-a", name="A", restrict_members_to_own_requests=True))
        await session.commit()
    granted = Principal(
        subject=DIRECTORY_ID,
        method="oidc",
        username=NAME,
        use_cases=("uc-a",),
        grants=(("uc-a", "admin"),),
    )
    async with sessions() as session:
        assert await restricted_use_cases(session, granted) == []


@pytest.mark.asyncio
async def test_an_incident_role_is_never_restricted(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _use_case(sessions, restricted=True, role="user")
    security = Principal(subject="sec", method="oidc", roles=("it-security",), use_cases=("uc-a",))
    async with sessions() as session:
        assert await restricted_use_cases(session, security) == []


# ═══ 7. the wire ════════════════════════════════════════════════════════════════════════════════
#
# Every property above is asked of a *component*. `LESSONS.md` §1 opens with why that is not
# enough: **two correct halves and no wire**, seven instances and counting. `SuspensionService`
# knowing about the person is one half; `guard_before_work` handing it over is the other, and it is
# an argument at a call site — the shape the same file calls *"a default argument is a silent
# one"*, because there is nothing missing to notice.


class _Suspensions:
    """Records what the gate asked it, and refuses the person by name."""

    def __init__(self) -> None:
        self.asked: tuple[object, ...] = ()

    async def check(
        self,
        use_case: object,
        subject: object,
        credential: object,
        person: object = None,
    ) -> list[object]:
        self.asked = (use_case, subject, credential, person)
        if person == NAME:
            raise Suspended("stopped", retry_after="60", reason="test")
        return []


class _NoLimits:
    async def check(self, *args: object, **kwargs: object) -> None:
        return None


class _NoBudgets:
    async def refuse_if_exhausted(self, *args: object, **kwargs: object) -> None:
        return None


class _GateRequest:
    """The two attributes `guard_before_work` reads. A real `Request` would need a whole app."""

    def __init__(self, attribution: Attribution, suspensions: _Suspensions) -> None:
        class _State:
            pass

        self.state = _State()
        self.state.attribution = attribution
        self.method = "POST"

        class _AppState:
            pass

        app_state = _AppState()
        app_state.suspensions = suspensions
        app_state.rate_limits = _NoLimits()
        app_state.budgets = _NoBudgets()
        app_state.settings = type("S", (), {"require_use_case": True})()
        self.app = type("App", (), {"state": app_state})()


@pytest.mark.asyncio
async def test_the_gate_hands_the_kill_switch_the_person_as_well_as_the_subject() -> None:
    """A caller signed in with a token, stopped by a suspension naming them.

    Asked through `guard_before_work` rather than through the service, because the service already
    knew: the argument at this call site is the whole of what was missing, and a test that
    constructs the call itself is a test of its own fixture.
    """
    from aira_gateway.api.serving import guard_before_work

    suspensions = _Suspensions()
    request = _GateRequest(
        Attribution(subject=DIRECTORY_ID, method="oidc", use_case="uc-a", username=NAME),
        suspensions,
    )

    with pytest.raises(Suspended):
        await guard_before_work(request)  # type: ignore[arg-type]

    assert suspensions.asked == ("uc-a", DIRECTORY_ID, None, NAME)
