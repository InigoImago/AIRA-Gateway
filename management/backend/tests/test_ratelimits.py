"""Rate-limit CRUD + distribution (FRD-405)."""

import pytest
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_uc(admin, slug: str = "demo-uc") -> UseCase:
    _client(admin).post(BASE, {"slug": slug, "name": "Demo"}, format="json")
    return UseCase.objects.get(slug=slug)


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


def test_create_rate_limit_and_emit(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/rate-limits/",
        {"scope": "use_case", "limit_rpm": 120, "burst": 20},
        format="json",
    )

    assert resp.status_code == 201
    limit = RateLimit.objects.get(use_case__slug="demo-uc", scope="use_case")
    assert (limit.limit_rpm, limit.burst) == (120, 20)
    published = [p for t, p in captured_events if t == "ratelimit.upserted"]
    assert published[0]["use_case"] == "demo-uc"
    assert published[0]["id"] == limit.pk
    assert published[0]["limit_rpm"] == 120


def test_upsert_replaces_rather_than_duplicating(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    body = {"scope": "use_case", "limit_rpm": 60}
    _client(admin).post(f"{BASE}demo-uc/rate-limits/", body, format="json")

    _client(admin).post(f"{BASE}demo-uc/rate-limits/", {**body, "limit_rpm": 600}, format="json")

    limits = RateLimit.objects.filter(use_case__slug="demo-uc")
    assert limits.count() == 1
    assert limits.first().limit_rpm == 600


def test_member_limit_requires_a_subject() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/rate-limits/", {"scope": "member", "limit_rpm": 10}, format="json"
    )

    assert resp.status_code == 400
    assert "subject" in str(resp.json())


def test_a_use_case_scoped_limit_drops_any_subject_sent_with_it() -> None:
    """Otherwise the uniqueness constraint keys on a subject nobody meant to set, and a second
    edit silently creates a second limit instead of replacing the first."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    _client(admin).post(
        f"{BASE}demo-uc/rate-limits/",
        {"scope": "use_case", "subject": "alice", "limit_rpm": 60},
        format="json",
    )

    assert RateLimit.objects.get(use_case__slug="demo-uc").subject == ""


def test_a_per_person_limit_names_nobody(captured_events) -> None:
    """One configured rate, one bucket per caller — the answer to "everybody, but separately",
    which a use-case limit cannot give (there the first arrival can drain it) and a member limit
    can only give one person at a time.

    Like the use-case row above, a subject sent with it is dropped: it would key the uniqueness
    constraint on a name the row does not honour, so a second edit would create a second limit.
    """
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/rate-limits/",
        {"scope": "each_member", "subject": "alice", "limit_rpm": 60, "burst": 10},
        format="json",
    )

    assert resp.status_code == 201
    limit = RateLimit.objects.get(use_case__slug="demo-uc")
    assert (limit.scope, limit.subject) == ("each_member", "")
    published = [p for t, p in captured_events if t == "ratelimit.upserted"]
    assert published[-1]["scope"] == "each_member"


def test_a_limit_of_zero_is_refused() -> None:
    """Zero would mean "refuse everything", which is a use case being switched off by accident
    rather than a rate being configured."""
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).post(
        f"{BASE}demo-uc/rate-limits/", {"scope": "use_case", "limit_rpm": 0}, format="json"
    )

    assert resp.status_code == 400


def test_members_may_read_but_not_set_limits() -> None:
    admin = _user("admin1", "global-admin")
    usecase = _make_uc(admin, "demo-uc")
    member = _user("member1")
    _client(admin).post(
        f"{BASE}demo-uc/members/", {"username": "member1", "role": "user"}, format="json"
    )
    RateLimit.objects.create(use_case=usecase, scope="use_case", limit_rpm=60)

    assert _client(member).get(f"{BASE}demo-uc/rate-limits/").status_code == 200
    refused = _client(member).post(
        f"{BASE}demo-uc/rate-limits/", {"scope": "use_case", "limit_rpm": 1}, format="json"
    )
    assert refused.status_code == 403


def test_delete_removes_and_emits(captured_events) -> None:
    admin = _user("admin1", "global-admin")
    usecase = _make_uc(admin, "demo-uc")
    limit = RateLimit.objects.create(use_case=usecase, scope="use_case", limit_rpm=60)

    resp = _client(admin).delete(f"{BASE}demo-uc/rate-limits/{limit.pk}/")

    assert resp.status_code == 204
    assert not RateLimit.objects.filter(pk=limit.pk).exists()
    removed = [p for t, p in captured_events if t == "ratelimit.deleted"]
    assert removed[0]["id"] == limit.pk


def test_deleting_an_unknown_limit_is_reported_rather_than_silently_accepted() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")

    resp = _client(admin).delete(f"{BASE}demo-uc/rate-limits/999/")

    assert resp.status_code == 400


def test_a_limit_of_another_use_case_cannot_be_deleted_through_this_one() -> None:
    admin = _user("admin1", "global-admin")
    _make_uc(admin, "demo-uc")
    other = _make_uc(admin, "other-uc")
    limit = RateLimit.objects.create(use_case=other, scope="use_case", limit_rpm=60)

    resp = _client(admin).delete(f"{BASE}demo-uc/rate-limits/{limit.pk}/")

    assert resp.status_code == 400
    assert RateLimit.objects.filter(pk=limit.pk).exists()


def test_the_event_reaches_the_right_kafka_topic() -> None:
    from aira_management.apps.outbox.models import OutboxEvent
    from aira_management.apps.outbox.subscriber import record_to_outbox

    from aira_common.kafka import RATE_LIMIT_TOPIC

    record_to_outbox("ratelimit.upserted", {"id": 5, "use_case": "demo-uc", "limit_rpm": 60})

    event = OutboxEvent.objects.get()
    assert event.topic == RATE_LIMIT_TOPIC
    assert event.key == "5"  # compacted per limit, so an edit supersedes its predecessor
