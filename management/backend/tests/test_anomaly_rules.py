"""Anomaly rules: authoring, validation, authorisation and distribution (FRD-500)."""

from __future__ import annotations

import pytest
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.usecases import events
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from aira_common.anomalies import RuleAction, RuleKind, RuleTarget

from .conftest import role_claims

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"
GLOBAL = "/api/v1/anomaly-rules/"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _use_case(admin, slug: str = "uc") -> str:
    _client(admin).post(BASE, {"slug": slug, "name": slug.title()}, format="json")
    return slug


@pytest.fixture
def captured_events():
    captured: list[tuple[str, dict]] = []

    def spy(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    events.subscribe(spy)
    yield captured
    events.unsubscribe(spy)


def _rule(**over):
    body = {
        "name": "too many refusals",
        "kind": RuleKind.REFUSAL_RATE.value,
        "window_minutes": 15,
        "threshold": 40,
        "min_sample": 20,
    }
    body.update(over)
    return body


# ---- authoring on a use case ----------------------------------------------------------------


def test_a_rule_is_authored_listed_and_distributed(captured_events) -> None:
    admin = _user("rule-admin", "global-admin")
    slug = _use_case(admin, "rules-uc")

    created = _client(admin).post(f"{BASE}{slug}/anomaly-rules/", _rule(), format="json")
    assert created.status_code == 201, created.data
    assert created.json()["use_case"] == slug
    assert created.json()["is_global"] is False

    listed = _client(admin).get(f"{BASE}{slug}/anomaly-rules/").json()
    assert [r["name"] for r in listed] == ["too many refusals"]

    # Everything the engine needs travels with the event: the gateway never calls back.
    published = [payload for kind, payload in captured_events if kind == "anomaly_rule.upserted"]
    assert published[-1]["use_case"] == slug
    assert published[-1]["kind"] == RuleKind.REFUSAL_RATE.value
    assert published[-1]["threshold"] == 40
    assert published[-1]["window_minutes"] == 15


def test_a_new_rule_only_alerts_until_somebody_says_otherwise() -> None:
    """A detection system whose first setting is `block` blocks the wrong thing once and is then
    switched off forever (`FRD-500` §4.3)."""
    admin = _user("default-admin", "global-admin")
    slug = _use_case(admin, "default-uc")

    created = _client(admin).post(f"{BASE}{slug}/anomaly-rules/", _rule(), format="json")

    assert created.json()["action"] == RuleAction.ALERT.value
    assert created.json()["action_minutes"] is None


def test_a_rule_is_replaced_by_name_rather_than_duplicated() -> None:
    admin = _user("upsert-admin", "global-admin")
    slug = _use_case(admin, "upsert-uc")
    client = _client(admin)

    client.post(f"{BASE}{slug}/anomaly-rules/", _rule(threshold=40), format="json")
    client.post(f"{BASE}{slug}/anomaly-rules/", _rule(threshold=55), format="json")

    rules = AnomalyRule.objects.filter(use_case__slug=slug)
    assert rules.count() == 1
    assert rules.first().threshold == 55


def test_a_rule_can_be_deleted_and_the_gateway_is_told(captured_events) -> None:
    admin = _user("del-admin", "global-admin")
    slug = _use_case(admin, "del-uc")
    rule_id = (
        _client(admin).post(f"{BASE}{slug}/anomaly-rules/", _rule(), format="json").json()["id"]
    )

    response = _client(admin).delete(f"{BASE}{slug}/anomaly-rules/{rule_id}/")

    assert response.status_code == 204
    assert not AnomalyRule.objects.filter(pk=rule_id).exists()
    assert ("anomaly_rule.deleted", {"id": rule_id, "use_case": slug}) in captured_events


def test_retiring_a_use_case_keeps_its_rules_as_record_and_they_reach_nothing() -> None:
    """**The cascade moved planes** (`FRD-607`).

    This asserted Django's `CASCADE` had removed the rules, on the argument `FRD-205` made about
    API keys: config that outlives the thing it configured is config a recreated slug silently
    inherits. That argument still holds and is now answered twice over:

    - the slug is **never recreated** — a retired use case keeps it, so there is nothing to
      inherit; and
    - the *gateway* still deletes its copy of every rule on `usecase.deleted`, which is where a
      rule would have to exist to do anything at all.

    What is left here is the record: which anomalies this use case was watched for. An
    investigation into a use case somebody retired asks exactly that, and destroying it was the
    capability this feature exists to remove.
    """
    admin = _user("cascade-admin", "global-admin")
    slug = _use_case(admin, "cascade-uc")
    _client(admin).post(f"{BASE}{slug}/anomaly-rules/", _rule(), format="json")

    _client(admin).delete(f"{BASE}{slug}/")

    assert AnomalyRule.objects.filter(use_case__slug=slug).exists()
    # And it is unreachable: every route resolves through the live queryset.
    assert _client(admin).get(f"{BASE}{slug}/anomaly-rules/").status_code == 404


# ---- validation, where the rule is written ---------------------------------------------------


def test_a_share_above_one_hundred_percent_is_refused() -> None:
    admin = _user("share-admin", "global-admin")
    slug = _use_case(admin, "share-uc")

    response = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/", _rule(threshold=140), format="json"
    )

    assert response.status_code == 400
    assert "threshold" in str(response.data)


def test_a_spike_at_or_below_the_previous_window_is_refused() -> None:
    """A ratio of 100 % fires on traffic that did not grow at all — every window, forever. The
    alert that never stops is the one people mute."""
    admin = _user("spike-admin", "global-admin")
    slug = _use_case(admin, "spike-uc")

    response = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/",
        _rule(name="spike", kind=RuleKind.SPEND_SPIKE.value, threshold=100),
        format="json",
    )

    assert response.status_code == 400
    assert "above" in str(response.data["error"]["details"]["threshold"]).lower()


def test_an_action_that_takes_something_away_must_say_for_how_long() -> None:
    """An automatic block with no expiry is an outage with a good reason (`ADR-0014` §2)."""
    admin = _user("expiry-admin", "global-admin")
    slug = _use_case(admin, "expiry-uc")

    refused = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/",
        _rule(action=RuleAction.BLOCK.value),
        format="json",
    )
    assert refused.status_code == 400
    assert "action_minutes" in refused.data["error"]["details"]

    accepted = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/",
        _rule(action=RuleAction.BLOCK.value, action_minutes=60, target=RuleTarget.CREDENTIAL.value),
        format="json",
    )
    assert accepted.status_code == 201
    assert accepted.json()["action_minutes"] == 60


def test_a_rate_rule_needs_a_sample_floor() -> None:
    """Without one, a single refused request out of one is 100 %."""
    admin = _user("sample-admin", "global-admin")
    slug = _use_case(admin, "sample-uc")

    response = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/", _rule(min_sample=0), format="json"
    )

    assert response.status_code == 400
    assert "min_sample" in str(response.data)


def test_an_event_kind_carries_no_sample_floor() -> None:
    """A credential used from a new address is one observation, not a proportion — requiring
    twenty of them would be requiring twenty leaks."""
    admin = _user("event-admin", "global-admin")
    slug = _use_case(admin, "event-uc")

    created = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/",
        _rule(name="new source", kind=RuleKind.NEW_SOURCE_IP.value, threshold=1, min_sample=20),
        format="json",
    )

    assert created.status_code == 201
    assert created.json()["min_sample"] == 0


def test_an_alert_carries_no_expiry_even_if_one_is_offered() -> None:
    """An expiry on an alert would read as though the alert stopped applying."""
    admin = _user("alert-admin", "global-admin")
    slug = _use_case(admin, "alert-uc")

    created = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/", _rule(action_minutes=30), format="json"
    )

    assert created.json()["action_minutes"] is None


# ---- authorisation ---------------------------------------------------------------------------


def test_a_member_cannot_author_a_rule_but_can_read_them() -> None:
    admin = _user("read-admin", "global-admin")
    slug = _use_case(admin, "read-uc")
    _client(admin).post(f"{BASE}{slug}/anomaly-rules/", _rule(), format="json")

    member = _user("read-member")
    _client(admin).post(
        f"{BASE}{slug}/members/", {"username": "read-member", "role": "user"}, format="json"
    )

    assert _client(member).get(f"{BASE}{slug}/anomaly-rules/").status_code == 200
    assert (
        _client(member)
        .post(f"{BASE}{slug}/anomaly-rules/", _rule(name="mine"), format="json")
        .status_code
        == 403
    )


def test_only_oversight_may_author_a_rule_that_acts_everywhere(captured_events) -> None:
    """A global rule's effects land on use cases its author may not be able to see, so authoring
    one is IT Security's job description (PRD §154) — and the API says so, not the UI."""
    # Somebody whose authority is a use case, not the installation — which since `ADR-0017` means
    # somebody holding no organisation-wide role at all. A Global Administrator here would be
    # refused by nothing, because they *are* oversight.
    uc_admin = _user("global-ucadmin")
    refused = _client(uc_admin).post(GLOBAL, _rule(name="everywhere"), format="json")
    assert refused.status_code == 403

    itsec = _user("global-itsec", "it-security")
    accepted = _client(itsec).post(GLOBAL, _rule(name="everywhere"), format="json")
    assert accepted.status_code == 201
    assert accepted.json()["is_global"] is True
    assert accepted.json()["use_case"] is None

    # `None` on the wire, not "": a use case named "" would match nothing while looking like it
    # matched everything.
    published = [p for kind, p in captured_events if kind == "anomaly_rule.upserted"]
    assert published[-1]["use_case"] is None


def test_a_global_rule_is_visible_to_everybody_it_could_act_on() -> None:
    """A rule that can block your traffic is a rule you are entitled to know about."""
    itsec = _user("visible-itsec", "it-security")
    _client(itsec).post(GLOBAL, _rule(name="everywhere"), format="json")

    plain = _user("visible-user")
    listed = _client(plain).get(GLOBAL).json()

    assert [r["name"] for r in listed] == ["everywhere"]


def test_a_use_case_rule_is_not_listed_among_the_global_ones_for_a_stranger() -> None:
    admin = _user("hidden-admin", "global-admin")
    slug = _use_case(admin, "hidden-uc")
    _client(admin).post(f"{BASE}{slug}/anomaly-rules/", _rule(name="theirs"), format="json")

    stranger = _user("hidden-stranger")

    assert _client(stranger).get(GLOBAL).json() == []
    assert [r["name"] for r in _client(admin).get(GLOBAL).json()] == ["theirs"]


def test_somebody_without_oversight_cannot_change_a_global_rule_through_the_global_list() -> None:
    """Editing follows the scope, not the endpoint."""
    itsec = _user("edit-itsec", "it-security")
    rule_id = _client(itsec).post(GLOBAL, _rule(name="everywhere"), format="json").json()["id"]

    uc_admin = _user("edit-ucadmin")

    assert _client(uc_admin).delete(f"{GLOBAL}{rule_id}/").status_code == 403
    assert AnomalyRule.objects.filter(pk=rule_id).exists()


def test_the_global_list_is_not_a_way_around_the_use_case_boundary() -> None:
    """A use-case rule reachable through the global list is still that use case's to change."""
    owner = _user("boundary-owner", "global-admin")
    slug = _use_case(owner, "boundary-uc")
    rule_id = (
        _client(owner)
        .post(f"{BASE}{slug}/anomaly-rules/", _rule(name="theirs"), format="json")
        .json()["id"]
    )

    itsec = _user("boundary-itsec", "it-security")
    # IT Security can *see* it — that is oversight — and still may not change it.
    assert [r["name"] for r in _client(itsec).get(GLOBAL).json()] == ["theirs"]
    assert _client(itsec).delete(f"{GLOBAL}{rule_id}/").status_code == 403


def test_the_str_of_a_rule_says_where_it_applies() -> None:
    admin = _user("str-admin", "global-admin")
    slug = _use_case(admin, "str-uc")
    _client(admin).post(f"{BASE}{slug}/anomaly-rules/", _rule(), format="json")
    itsec = _user("str-itsec", "it-security")
    _client(itsec).post(GLOBAL, _rule(name="everywhere"), format="json")

    scoped = AnomalyRule.objects.get(name="too many refusals")
    everywhere = AnomalyRule.objects.get(name="everywhere")

    assert slug in str(scoped)
    assert "global" in str(everywhere)
    assert everywhere.is_global and not scoped.is_global


# ---- the second number a kind may need (FRD-501 §4.4) ----------------------------------------


def test_a_kind_that_measures_against_a_size_must_be_given_one() -> None:
    """Found by building the engine, not by reviewing the schema: `payload_size` is "the share of
    requests above a byte threshold", and the rule carried one threshold — the share. Stage A's
    model, API, 18 tests and six mutations were all green, because nothing had yet tried to
    *evaluate* a rule."""
    admin = _user("param-admin", "global-admin")
    slug = _use_case(admin, "param-uc")

    refused = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/",
        _rule(name="bulk", kind=RuleKind.PAYLOAD_SIZE.value, threshold=20),
        format="json",
    )
    assert refused.status_code == 400
    assert "parameter" in refused.data["error"]["details"]

    accepted = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/",
        _rule(name="bulk", kind=RuleKind.PAYLOAD_SIZE.value, threshold=20, parameter=500_000),
        format="json",
    )
    assert accepted.status_code == 201
    assert accepted.json()["parameter"] == 500_000


def test_a_kind_that_takes_no_second_number_refuses_one() -> None:
    """Refused rather than ignored. A number a rule accepts and never reads is a setting somebody
    will tune, and then wonder why nothing changes (`FRD-124`)."""
    admin = _user("noparam-admin", "global-admin")
    slug = _use_case(admin, "noparam-uc")

    response = _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/", _rule(parameter=500_000), format="json"
    )

    assert response.status_code == 400
    assert "parameter" in response.data["error"]["details"]


def test_the_byte_figure_travels_to_the_gateway(captured_events) -> None:
    admin = _user("param-event-admin", "global-admin")
    slug = _use_case(admin, "param-event-uc")

    _client(admin).post(
        f"{BASE}{slug}/anomaly-rules/",
        _rule(name="bulk", kind=RuleKind.PAYLOAD_SIZE.value, threshold=20, parameter=500_000),
        format="json",
    )

    published = [p for kind, p in captured_events if kind == "anomaly_rule.upserted"]
    assert published[-1]["parameter"] == 500_000


# == a partial edit is about the rule that exists, not about a default ============================
#
# `validate` read `attrs.get("kind", REFUSAL_RATE)` and `attrs.get("action", ALERT)`, so on a
# `PATCH` — which carries only what changed — every check below it answered about a rule nobody
# has. The endpoint is a `ModelViewSet`, the console's own client method is a `PATCH`, and its
# docstring is explicit that this is the intended shape: *"a rule has thirteen fields and most
# edits touch one of them"*.


def _global_throttle(client, **over) -> int:
    body = {
        "name": "throttle probers",
        "kind": RuleKind.REFUSAL_RATE.value,
        "window_minutes": 15,
        "threshold": 40,
        "min_sample": 20,
        "action": RuleAction.THROTTLE.value,
        "target": RuleTarget.SUBJECT.value,
        "action_minutes": 60,
        "throttle_rpm": 5,
    }
    body.update(over)
    response = client.post(GLOBAL, body, format="json")
    assert response.status_code == 201, response.data
    return int(response.data["id"])


def test_a_partial_edit_keeps_the_expiry_of_an_automatic_action() -> None:
    """**The expensive one.** `action` defaulted to `alert` on a `PATCH`, and an alert keeps no
    expiry — so the branch that clears `action_minutes` ran against a `throttle` rule.

    The row then says `throttle` with no expiry, the event ships that to the gateway, and
    `service._act` refuses to carry it out (`not rule.action_minutes` → `detected_not_enforced`).
    Which is the right refusal and the wrong situation: the console goes on displaying the rule as
    throttling, so an incident control was switched off by a rename. Measured at 2026-08-26 —
    `action_minutes` came back `None` from a `PATCH` whose body was a name.
    """
    sec = _user("sec", "it-security")
    rule_id = _global_throttle(_client(sec))

    response = _client(sec).patch(f"{GLOBAL}{rule_id}/", {"name": "renamed"}, format="json")

    assert response.status_code == 200, response.data
    rule = AnomalyRule.objects.get(pk=rule_id)
    assert (rule.name, rule.action) == ("renamed", RuleAction.THROTTLE.value)
    assert rule.action_minutes == 60, "the rename removed the expiry from an automatic throttle"
    assert rule.throttle_rpm == 5, "and the rate it throttles to"


def test_a_partial_edit_is_not_refused_over_a_field_it_did_not_send() -> None:
    """The symptom that made every other one invisible: with `kind` defaulted to a rate kind, the
    sample floor was demanded of a body that carried no sample — so **every** partial edit was
    refused, whatever it changed, with a message about a field the caller never mentioned.

    Asserted for an event kind, where the stored rule legitimately has no sample at all.
    """
    sec = _user("sec", "it-security")
    created = _client(sec).post(
        GLOBAL,
        _rule(name="new address", kind=RuleKind.NEW_SOURCE_IP.value, threshold=1, min_sample=0),
        format="json",
    )
    assert created.status_code == 201, created.data

    response = _client(sec).patch(
        f"{GLOBAL}{created.data['id']}/", {"enabled": False}, format="json"
    )

    assert response.status_code == 200, response.data
    assert AnomalyRule.objects.get(pk=created.data["id"]).enabled is False


def test_a_partial_edit_checks_the_threshold_against_the_kind_the_rule_actually_has() -> None:
    """A ratio is a multiple of the previous window, so a threshold at or below 100 fires on
    traffic that did not grow — every window, forever. With `kind` defaulted to a rate kind, that
    check asked the wrong question and the rate check answered instead."""
    sec = _user("sec", "it-security")
    created = _client(sec).post(
        GLOBAL,
        _rule(
            name="spend spike",
            kind=RuleKind.SPEND_SPIKE.value,
            threshold=200,
            window_minutes=60,
        ),
        format="json",
    )
    assert created.status_code == 201, created.data

    response = _client(sec).patch(
        f"{GLOBAL}{created.data['id']}/", {"threshold": 50}, format="json"
    )

    assert response.status_code == 400
    # **Named**, not merely refused. Under the defect this endpoint refused *every* partial edit
    # over `min_sample`, so "it was a 400" is a verdict this test would have reached without the
    # threshold ever being looked at — the shape where a guard keeps passing for the wrong reason.
    assert "threshold" in str(response.data), response.data
    assert AnomalyRule.objects.get(pk=created.data["id"]).threshold == 200


def test_changing_an_action_away_from_throttle_leaves_no_rate_behind() -> None:
    """The counterpart, and the reason the clearing branches are not simply deleted: a rate on an
    `alert` is a number the row carries, the event ships and nothing reads."""
    sec = _user("sec", "it-security")
    rule_id = _global_throttle(_client(sec))

    response = _client(sec).patch(
        f"{GLOBAL}{rule_id}/", {"action": RuleAction.ALERT.value}, format="json"
    )

    assert response.status_code == 200, response.data
    rule = AnomalyRule.objects.get(pk=rule_id)
    assert (rule.action, rule.action_minutes, rule.throttle_rpm) == (
        RuleAction.ALERT.value,
        None,
        None,
    )
