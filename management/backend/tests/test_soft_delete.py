"""Retiring a use case, and the second decision that removes it (`FRD-607`).

The requirement, from the owner, is a **threat** rather than a preference:

    *"we want soft delete, and only later a full delete after a deliberate decision. Prompts
    should still be deleted after the defined time — but not in a way that lets somebody use a
    use case for the wrong purposes, compromise it, and delete the use case."*

Two obligations that pull against each other, and every test here belongs to one of them:

**The record must survive the person.** Deleting used to be a hard delete available to the
**use-case administrator** — which is to say, available to exactly the party an investigation
would be about. The traffic survived in the gateway's audit trail on purpose (`FRD-404` §4.1) and
survived *context-free*: what the use case was for, which models it had released, whether it
stored prompts and who its members were all lived in Management and went with the row.

**The prompts must still go.** A tombstone that keeps personal data forever answers the audit
question by breaking the GDPR one. They expire on the use case's **own** period, which is the
promise that was made — not on whatever the installation default happens to be.
"""

from datetime import timedelta

import pytest
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.usecases import events
from aira_management.apps.usecases.access import may_admin, may_manage
from aira_management.apps.usecases.models import (
    PURGE_AFTER_DAYS,
    UseCase,
    UseCaseMembership,
)
from aira_management.apps.usecases.views import _grant
from aira_management.apps.usecases.views.lifecycle import retire
from aira_management.apps.usecases.views.payloads import _snapshot
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from guardian.models import GroupObjectPermission, UserObjectPermission
from rest_framework.test import APIClient

from aira_common.anomalies import RuleKind

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


def _administrator(username: str, usecase: UseCase):
    """An administrator of **one** use case, holding no organisation-wide role.

    The party the threat model is about, and the reason this file exists.
    """
    user = _user(username)
    _grant(user, usecase, UseCaseMembership.ADMIN)
    UseCaseMembership.objects.create(use_case=usecase, user=user, role=UseCaseMembership.ADMIN)
    return get_user_model().objects.get(pk=user.pk)


def _made(slug: str = "uc") -> tuple[UseCase, object]:
    """A use case and its own administrator — created by a Global Administrator (`ADR-0017`)."""
    root = _user(f"{slug}-root", "global-admin")
    _client(root).post(BASE, {"slug": slug, "name": "UC"}, format="json")
    usecase = UseCase.objects.get(slug=slug)
    return usecase, _administrator(f"{slug}-admin", usecase)


def _retire(usecase: UseCase, *, days_ago: int = 0) -> UseCase:
    """Retire directly and age the tombstone, for the tests about the waiting period.

    Reaching back to the model rather than travelling in time: the wait is the *policy* under test
    in exactly two places below, and mocking a clock in the other twenty would test the mock.
    """
    usecase.deleted_at = timezone.now() - timedelta(days=days_ago)
    usecase.deleted_by = "someone"
    usecase.save(update_fields=["deleted_at", "deleted_by"])
    return usecase


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


# == the threat itself ===========================================================================


def test_the_administrator_of_a_use_case_can_retire_it_and_cannot_erase_it(captured_events) -> None:
    """**The whole feature in one test.** The party who might want the record gone can stop the
    use case and cannot destroy what it was."""
    usecase, admin = _made()

    assert _client(admin).delete(f"{BASE}uc/").status_code == 204

    retired = UseCase.objects.get(slug="uc")
    assert retired.deleted_at is not None
    assert retired.deleted_by == "uc-admin"
    # The same event as before, so access ends exactly as it always did.
    (event,) = [payload for kind, payload in captured_events if kind == "usecase.deleted"]
    assert event["slug"] == "uc"

    # And the use case itself, as it stood. The topic is compacted on the slug, which this event
    # shares with `usecase.upserted`, so on a rebuilt read-model it is the only record left of
    # the use case: the gateway writes the tombstone from it, retention promise included.
    assert event == _snapshot(retired)
    assert event["retention_days"] == retired.retention_days

    # And the second act is not theirs.
    assert _client(admin).delete(f"{BASE}uc/purge/").status_code == 403
    assert UseCase.objects.filter(slug="uc").exists()


def test_governance_oversees_the_tombstones_and_does_not_remove_them() -> None:
    """`IT Steuerung` is read-only by design (`ADR-0007`): it sees every use case and acts in
    none. A purge is an act, and the most irreversible one in the product."""
    usecase, _ = _made()
    _retire(usecase, days_ago=PURGE_AFTER_DAYS + 1)
    governance = _user("gov", "it-steuerung")

    listed = _client(governance).get(f"{BASE}retired/")
    assert listed.status_code == 200
    assert [row["slug"] for row in listed.json()] == ["uc"]

    assert _client(governance).delete(f"{BASE}uc/purge/").status_code == 403
    assert UseCase.objects.filter(slug="uc").exists()


def test_a_use_case_member_sees_no_tombstones_at_all() -> None:
    """The retired list is a governance record, not a directory. A member of one use case learning
    which others existed and were retired is a disclosure with no purpose."""
    usecase, _ = _made()
    _retire(usecase)
    member = _user("nobody")

    assert _client(member).get(f"{BASE}retired/").status_code == 403


def test_a_global_administrator_purges_only_after_the_waiting_period(captured_events) -> None:
    """A decision that can be taken in the same minute as the deletion is not a second decision."""
    usecase, _ = _made()
    root = _user("root", "global-admin")

    _retire(usecase, days_ago=PURGE_AFTER_DAYS - 1)
    too_soon = _client(root).delete(f"{BASE}uc/purge/")
    assert too_soon.status_code == 400
    # The sentence itself, not a repr of the list it travelled in.
    assert too_soon.json()["error"]["message"] == (
        f"This use case was retired {PURGE_AFTER_DAYS - 1} day(s) ago and may be purged after "
        f"{PURGE_AFTER_DAYS}. Try again in 1 day(s)."
    )
    assert UseCase.objects.filter(slug="uc").exists()

    _retire(usecase, days_ago=PURGE_AFTER_DAYS)
    assert _client(root).delete(f"{BASE}uc/purge/").status_code == 204
    assert not UseCase.objects.filter(slug="uc").exists()
    # A **second** event: the first ended access and kept the record, this one says the record is
    # gone. The gateway keeps its row until it hears this one.
    assert ("usecase.purged", {"slug": "uc"}) in captured_events


def test_a_live_use_case_cannot_be_purged() -> None:
    """Otherwise the one-step erase this feature removes is back, behind a longer URL."""
    _made()
    root = _user("root", "global-admin")

    assert _client(root).delete(f"{BASE}uc/purge/").status_code == 404
    assert UseCase.objects.filter(slug="uc", deleted_at__isnull=True).exists()


def test_purging_something_that_never_existed_is_indistinguishable_from_a_live_one() -> None:
    """Both answer 404. Telling them apart would say whether a slug exists to somebody who cannot
    see it — the disclosure `get_queryset` was narrowed for in 2026-08-15."""
    _made()
    root = _user("root", "global-admin")

    assert _client(root).delete(f"{BASE}uc/purge/").status_code == 404
    assert _client(root).delete(f"{BASE}never-existed/purge/").status_code == 404


# == the record must survive ======================================================================


def test_the_tombstone_keeps_what_makes_the_traffic_evidence() -> None:
    """The gateway's audit rows name a use case by **slug** and nothing else.

    What that slug meant — its purpose, how it processed data, whether it stored prompts and for
    how long — lives here, and used to be destroyed by the party the record would be about. Every
    field on the retired view is one an investigation asks for.
    """
    usecase, admin = _made()
    _client(admin).patch(
        f"{BASE}uc/",
        {
            "description": "Answers customer mail",
            "processing_notes": "Prompts may contain names and order numbers.",
            "store_payloads": True,
            "retention_days": 45,
        },
        format="json",
    )

    _client(admin).delete(f"{BASE}uc/")

    row = _client(_user("root", "global-admin")).get(f"{BASE}retired/").json()[0]
    assert row["description"] == "Answers customer mail"
    assert row["processing_notes"] == "Prompts may contain names and order numbers."
    assert row["store_payloads"] is True
    assert row["retention_days"] == 45
    assert row["deleted_by"] == "uc-admin"


def test_the_retired_view_says_when_the_second_decision_becomes_available() -> None:
    """A date, not a rule. A reader should not have to hold `PURGE_AFTER_DAYS` in their head and
    do arithmetic on a timestamp to find out whether a button will work."""
    usecase, _ = _made()
    _retire(usecase, days_ago=10)

    row = _client(_user("root", "global-admin")).get(f"{BASE}retired/").json()[0]
    expected = usecase.deleted_at + timedelta(days=PURGE_AFTER_DAYS)
    assert row["purgeable_on"].startswith(expected.date().isoformat())


# == edge cases ===================================================================================


def test_retiring_twice_emits_once_and_does_not_overwrite_who_did_it(captured_events) -> None:
    """At-least-once delivery and a double-click are the same shape.

    A second event would re-run the gateway's cascade against rows already gone — harmless — and
    overwrite `deleted_by` with whoever asked second, which is not. The **first** person to retire
    a use case is the fact an investigation wants.
    """
    usecase, admin = _made()
    root = _user("root", "global-admin")

    assert _client(admin).delete(f"{BASE}uc/").status_code == 204
    first = UseCase.objects.get(slug="uc").deleted_at

    captured_events.clear()
    assert _client(root).delete(f"{BASE}uc/").status_code == 404

    again = UseCase.objects.get(slug="uc")
    assert again.deleted_at == first
    assert again.deleted_by == "uc-admin"
    assert not [event for event in captured_events if event[0] == "usecase.deleted"]


def test_the_slug_stays_taken() -> None:
    """**A re-created `kundenservice` inheriting the audit history of the retired one is the same
    evidence problem with extra steps.** The refusal is a clean 400, not a 500 from the unique
    index — somebody retiring and re-creating a use case is an ordinary mistake to make.
    """
    usecase, admin = _made()
    _client(admin).delete(f"{BASE}uc/")

    again = _client(_user("root2", "global-admin")).post(
        BASE, {"slug": "uc", "name": "Reused"}, format="json"
    )
    assert again.status_code == 400
    assert "slug" in str(again.json()).lower()


def test_a_retired_use_case_is_gone_from_every_way_of_reaching_one() -> None:
    """A soft delete some queries honour and others do not is worse than none: it makes a retired
    use case appear on the screens nobody audited and vanish from the ones they did."""
    usecase, admin = _made()
    _client(admin).delete(f"{BASE}uc/")
    root = _client(_user("root", "global-admin"))

    assert [row["slug"] for row in root.get(BASE).json()["results"]] == []
    # `?q=`, the parameter the list reads — a search that never ran would pass this vacuously.
    assert [row["slug"] for row in root.get(f"{BASE}?q=uc").json()["results"]] == []
    assert [row["slug"] for row in root.get(f"{BASE}?may_call=true").json()["results"]] == []
    assert root.get(f"{BASE}uc/").status_code == 404
    # And every nested route, which all resolve through the same queryset.
    for route in ("members", "budgets", "rate-limits", "api-keys", "anomaly-rules"):
        assert root.get(f"{BASE}uc/{route}/").status_code == 404, route


def test_purging_is_refused_for_a_use_case_retired_a_moment_ago() -> None:
    """The boundary, at the resolution somebody would actually hit it: retire and immediately try.

    Written after asking what the *cheapest* attack is. It is not waiting thirty days — it is
    hoping the check compares dates rather than instants and that "today minus thirty days" is
    satisfied by a tombstone made this morning.
    """
    usecase, admin = _made()
    _client(admin).delete(f"{BASE}uc/")
    root = _client(_user("root", "global-admin"))

    assert root.delete(f"{BASE}uc/purge/").status_code == 400

    _retire(usecase, days_ago=PURGE_AFTER_DAYS)
    assert root.delete(f"{BASE}uc/purge/").status_code == 204


def test_a_retired_use_case_is_gone_from_the_installation_wide_rule_list_too() -> None:
    """`/api/v1/anomaly-rules/` reaches every visible use case's rules without the use-case
    viewset, so it has to ask the same live filter. Its administrator — the party `FRD-607` is
    about — could otherwise still read, change and delete a retired use case's rules, and each
    change would announce a rule for a slug the gateway has retired."""
    usecase, admin = _made()
    client = _client(admin)
    created = client.post(
        f"{BASE}uc/anomaly-rules/",
        {
            "name": "refusals",
            "kind": RuleKind.REFUSAL_RATE.value,
            "window_minutes": 15,
            "threshold": 40,
            "min_sample": 20,
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    rule_id = created.json()["id"]
    assert client.delete(f"{BASE}uc/").status_code == 204

    assert client.get("/api/v1/anomaly-rules/").json() == []
    patched = client.patch(f"/api/v1/anomaly-rules/{rule_id}/", {"threshold": 10}, format="json")
    assert patched.status_code == 404
    assert client.delete(f"/api/v1/anomaly-rules/{rule_id}/").status_code == 404
    assert AnomalyRule.objects.get(pk=rule_id).threshold == 40, "the evidence is left as it was"


def test_no_predicate_answers_yes_about_a_retired_use_case() -> None:
    """Its guardian rows stay behind as evidence, so the predicates ask the tombstone too — for a
    route that reaches a use case without the viewset's live filter. Nobody, not even a Global
    Administrator, administers or manages what has been retired."""
    usecase, admin = _made()
    root = _user("root", "global-admin")
    assert may_admin(admin, usecase) and may_manage(admin, usecase), "the comparison"

    _retire(usecase)

    assert not may_admin(admin, usecase) and not may_manage(admin, usecase)
    assert not may_admin(root, usecase) and not may_manage(root, usecase)


def test_retiring_through_two_stale_copies_keeps_the_first_author_and_announces_once(
    captured_events,
) -> None:
    """A double click, or two people at once: both requests loaded the live row before either
    saved. The database decides who was first — a read-then-save would let the second overwrite
    who retired it and send `usecase.deleted` twice."""
    usecase, _admin = _made()
    first = UseCase.objects.get(pk=usecase.pk)
    second = UseCase.objects.get(pk=usecase.pk)
    captured_events.clear()

    assert retire(first, by="first") is True
    assert retire(second, by="second") is False

    assert UseCase.objects.get(pk=usecase.pk).deleted_by == "first"
    assert [kind for kind, _payload in captured_events].count("usecase.deleted") == 1


def test_a_purge_removes_the_object_permissions_that_named_the_use_case() -> None:
    """Guardian's rows point at an object by content type and key, not by a foreign key, so
    deleting the use case would leave them: who had access to something that no longer exists —
    the personal data a purge promises to remove — and a grant a re-used key would inherit."""
    usecase, _admin = _made()
    member = _user("member")
    _grant(member, usecase, UseCaseMembership.USER)
    _retire(usecase, days_ago=PURGE_AFTER_DAYS)
    content_type = ContentType.objects.get_for_model(UseCase)
    pk = str(usecase.pk)
    assert UserObjectPermission.objects.filter(content_type=content_type, object_pk=pk).exists()

    assert _client(_user("root", "global-admin")).delete(f"{BASE}uc/purge/").status_code == 204

    for model in (UserObjectPermission, GroupObjectPermission):
        assert not model.objects.filter(content_type=content_type, object_pk=pk).exists()
