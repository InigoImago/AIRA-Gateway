"""Retiring a use case ends each of its children on that child's own compaction key (`FRD-607`).

The topics are compacted: the last record of each key is what a rebuilt read-model is made of. A
use case's key, member, group grant, budget, limit, rule and pipeline each live on a key of their
own, so the retirement — on the use case's key — protected them only while it was the last record
of that key. A purge replaces it, and a rebuild or an offset reset afterwards found the children's
last records still granting, and attached them to a use case created later under the same slug.
So retirement now ends each child where it lives, and a purge asks the same again for a use case
retired before it did.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.budgets.models import Budget
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant, UseCaseMembership
from aira_management.apps.usecases.views.lifecycle import purge, retire
from django.contrib.auth import get_user_model
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture
def captured() -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        seen.append((event_type, payload))

    events.subscribe(spy)
    yield seen
    events.unsubscribe(spy)


def _governed() -> UseCase:
    """A use case with one of every child an event carries."""
    owner = get_user_model().objects.create(username="mallory")
    usecase = UseCase.objects.create(slug="chatbot", name="Chatbot")
    UseCaseMembership.objects.create(use_case=usecase, user=owner, role="admin")
    UseCaseGroupGrant.objects.create(use_case=usecase, group_path="/abteilungen/alt", role="admin")
    ApiKey.objects.create(
        use_case=usecase, owner=owner, prefix="a1b2c3d4", key_hash="0" * 64,
        expires_at=timezone.now() + timedelta(days=150),
    )  # fmt: skip
    ApiKey.objects.create(
        use_case=usecase, owner=owner, prefix="e5f6a7b8", key_hash="1" * 64, is_active=False,
        revoked_at=timezone.now(), expires_at=timezone.now() + timedelta(days=150),
    )  # fmt: skip
    Budget.objects.create(use_case=usecase, scope="use_case", limit_tokens=10)
    RateLimit.objects.create(use_case=usecase, scope="use_case", limit_rpm=5)
    AnomalyRule.objects.create(use_case=usecase, name="r", kind="error_rate", threshold=50)
    PipelineConfig.objects.create(use_case=usecase, steps=[])
    return usecase


def _every_child_ended(usecase: UseCase) -> list[tuple[str, dict]]:
    """The removal of each child of ``_governed()``, sorted by event type."""
    budget = Budget.objects.get(use_case=usecase)
    limit = RateLimit.objects.get(use_case=usecase)
    rule = AnomalyRule.objects.get(use_case=usecase)
    return [
        ("anomaly_rule.deleted", {"id": rule.pk, "use_case": "chatbot"}),
        ("api_key.revoked", {"prefix": "a1b2c3d4", "use_case": "chatbot", "status": "revoked"}),
        ("budget.deleted", {"id": budget.pk, "use_case": "chatbot"}),
        ("membership.removed", {"slug": "chatbot", "username": "mallory"}),
        ("pipeline.deleted", {"use_case": "chatbot"}),
        ("ratelimit.deleted", {"id": limit.pk, "use_case": "chatbot"}),
        ("use_case_group.revoked", {"slug": "chatbot", "group": "/abteilungen/alt"}),
    ]


def test_retiring_ends_every_child_on_its_own_key(captured: list[tuple[str, dict]]) -> None:
    usecase = _governed()
    expected = _every_child_ended(usecase)

    assert retire(usecase, by="someone") is True

    assert captured[0][0] == "usecase.deleted", "the use case first, as it stood"
    ended = sorted(captured[1:], key=lambda event: event[0])
    assert ended == expected, "a key revoked earlier ended then, and is not ended twice"


def test_the_record_says_the_keys_ended_and_keeps_who_could_reach_it() -> None:
    """Revoked here too, with the moment, because a key that stopped working is a fact an
    investigation asks about. The rest stays: who could reach the use case and how it was governed
    is the record a retirement exists to keep (`FRD-607`), and it grants nothing."""
    usecase = _governed()

    retire(usecase, by="someone")

    key = ApiKey.objects.get(prefix="a1b2c3d4")
    assert key.is_active is False and key.revoked_at is not None
    assert UseCaseMembership.objects.filter(use_case=usecase).exists()
    assert UseCaseGroupGrant.objects.filter(use_case=usecase).exists()
    assert Budget.objects.filter(use_case=usecase).exists()
    assert PipelineConfig.objects.filter(use_case=usecase).exists()


def test_retiring_twice_ends_nothing_twice(captured: list[tuple[str, dict]]) -> None:
    usecase = _governed()
    retire(usecase, by="first")
    captured.clear()

    assert retire(UseCase.objects.get(pk=usecase.pk), by="second") is False
    assert captured == []


def test_a_purge_ends_the_children_a_retirement_from_before_left_granting(
    captured: list[tuple[str, dict]],
) -> None:
    """A use case retired before retirement ended its children: only the slug's record says so,
    and the purge is the last moment Management has the rows that name the other keys."""
    usecase = _governed()
    expected = _every_child_ended(usecase)
    UseCase.objects.filter(pk=usecase.pk).update(deleted_at=timezone.now(), deleted_by="earlier")

    purge(UseCase.objects.get(pk=usecase.pk))

    assert captured[-1] == ("usecase.purged", {"slug": "chatbot"}), "the purge last"
    ended = sorted(captured[:-1], key=lambda event: event[0])
    assert ended == expected
