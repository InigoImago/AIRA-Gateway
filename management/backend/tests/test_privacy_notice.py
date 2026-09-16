"""The privacy notice as the console receives it, and its acknowledgement (`FRD-625`)."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import pytest
from aira_management.apps.privacy import notice
from aira_management.apps.privacy.edition import EDITION, SOURCE_DIGEST, source_digest
from aira_management.apps.privacy.models import NoticeAcknowledgement
from aira_management.apps.privacy.texts import LANGUAGES
from aira_management.apps.roles.models import StoredRole
from aira_management.config.app_settings import ManagementSettings
from aira_management.config.runtime import get_settings
from aira_management.rbac import sync_user_groups, sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from aira_common.permissions import Permission, RoleDefinition

from .conftest import GROUP_FOR

pytestmark = pytest.mark.django_db

NOTICE = "/api/v1/privacy-notice"
ACK = "/api/v1/privacy-notice/acknowledgements"
CONTROLLER = "Example GmbH, Example Street 1, 12345 Example, privacy@example.com"


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The installation's privacy settings, changeable per test through ``configured[...]``."""
    values: dict[str, Any] = {
        "privacy_controller": CONTROLLER,
        "privacy_notice_mode": "monthly",
    }

    def settings() -> ManagementSettings:
        return ManagementSettings(**values)

    monkeypatch.setattr("aira_management.apps.privacy.views.get_settings", settings)
    return values


def _user(name: str, *roles: str) -> Any:
    user = get_user_model().objects.create(username=name)
    claims = {"groups": [GROUP_FOR[role] for role in roles]}
    sync_user_roles(user, claims)
    sync_user_groups(user, claims)
    return user


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _text(value: Any) -> str:
    """Every text a rendered notice carries, joined — its words, not its punctuation as a dict."""
    if isinstance(value, dict):
        return "\n".join(_text(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    return str(value)


def _installation(**changes: Any) -> notice.Installation:
    base = notice.Installation.of(ManagementSettings(privacy_controller=CONTROLLER), ())
    return dataclasses.replace(base, **changes)


# == the edition ===================================================================================


def test_the_edition_moves_whenever_the_notice_s_sources_change() -> None:
    """A changed register or text with an unchanged date is a notice claiming a "Stand" it does not
    have — and one nobody is asked to read again."""
    actual = source_digest()
    assert actual == SOURCE_DIGEST, (
        "The privacy notice's register or texts changed. Describe the change in every language, "
        f"then set EDITION in apps/privacy/edition.py to today's date and SOURCE_DIGEST = "
        f'"{actual}".'
    )
    dt.date.fromisoformat(EDITION)


# == rendering =====================================================================================


def test_the_configured_controller_and_retention_are_what_the_notice_prints() -> None:
    installation = _installation(
        dpo_contact="dpo@example.com",
        works_agreement="BV KI-Nutzung 2026",
        log_retention_days=400,
        default_retention_days=14,
        api_key_default_days=30,
        api_key_max_days=90,
    )
    rendered = _text(notice.render("en", installation))

    assert CONTROLLER in rendered
    assert "dpo@example.com" in rendered
    assert "BV KI-Nutzung 2026" in rendered
    assert "Request records in this installation: 400 days." in rendered
    assert "for requests without a use case 14 days" in rendered
    assert "after 30 days, and after 90 days at the latest" in rendered
    assert "{" not in rendered


def test_an_installation_that_keeps_records_forever_says_so() -> None:
    rendered = str(notice.render("de", _installation(log_retention_days=0)))
    assert "ohne zeitliche Begrenzung" in rendered


def test_an_unconfigured_controller_prints_a_gap_rather_than_a_blank() -> None:
    rendered = str(notice.render("en", _installation(controller="")))
    assert "AIRA_PRIVACY_CONTROLLER" in rendered


def test_content_storage_switched_off_is_stated() -> None:
    assert "stores no request or response content" in str(
        notice.render("en", _installation(store_payloads=False))
    )
    assert "allows content to be stored" in str(notice.render("en", _installation()))


def _access_items(rendered: dict[str, Any]) -> list[str]:
    return next(s for s in rendered["sections"] if s["key"] == "access")["items"]


def test_each_role_is_listed_with_what_it_may_do_to_other_people_s_data() -> None:
    roles = (
        RoleDefinition("readers", "Readers", (), frozenset({Permission.PAYLOAD_READ_ANY})),
        RoleDefinition("pricing", "Pricing", (), frozenset({Permission.CATALOG_WRITE})),
    )
    installation = notice.Installation.of(ManagementSettings(), roles)

    items = _access_items(notice.render("en", installation))

    assert items[0] == (
        "Readers: read request and response content in every use case, with every read recorded"
    )
    # A role whose permissions reach nobody's data says so rather than disappearing.
    assert items[1] == "Pricing: no access to other people's data beyond their own use cases"


def test_every_activity_carries_all_five_statements_in_every_language() -> None:
    for code in LANGUAGES:
        section = next(
            s for s in notice.render(code, _installation())["sections"] if s["key"] == "activities"
        )
        for activity in section["activities"]:
            keys = [field["key"] for field in activity["fields"]]
            assert keys == ["data", "purpose", "legal_basis", "retention", "recipients"]
            assert all(field["text"] and field["label"] for field in activity["fields"])


@pytest.mark.parametrize(
    "change",
    [
        {"controller": "Other AG"},
        {"log_retention_days": 30},
        {"store_payloads": False},
        {"roles": (notice.RoleAccess("New role", ("payload.read_any",)),)},
    ],
)
def test_what_the_notice_states_is_part_of_its_version(change: dict[str, Any]) -> None:
    """A reader acknowledged a notice naming a controller, a retention and who may read their
    content. Changing any of them is a different notice, so everybody is asked again."""
    assert notice.version(_installation(**change)) != notice.version(_installation())


def test_the_version_does_not_depend_on_the_language_being_read() -> None:
    assert notice.version(_installation()).startswith(f"{EDITION}.")
    assert notice.version(_installation()) == notice.version(_installation())


# == the API =======================================================================================


def test_reading_the_notice_requires_signing_in() -> None:
    assert APIClient().get(NOTICE).status_code == 401
    assert APIClient().post(ACK, {}, format="json").status_code == 401


def test_the_browser_language_decides_when_none_is_asked_for() -> None:
    client = _client(_user("anna"))

    german = client.get(NOTICE, HTTP_ACCEPT_LANGUAGE="de-DE,de;q=0.9").json()
    english = client.get(NOTICE, HTTP_ACCEPT_LANGUAGE="en-US").json()

    assert german["language"] == "de" and german["title"] == "Datenschutzhinweise"
    assert english["language"] == "en" and english["title"] == "Privacy notice"
    assert {entry["code"] for entry in german["languages"]} == set(LANGUAGES)


def test_an_explicit_language_wins_over_the_browser() -> None:
    body = _client(_user("anna")).get(f"{NOTICE}?language=en", HTTP_ACCEPT_LANGUAGE="de").json()
    assert body["language"] == "en"


def test_a_language_the_installation_has_no_text_for_is_refused_by_name() -> None:
    response = _client(_user("anna")).get(f"{NOTICE}?language=tlh")
    assert response.status_code == 400
    assert "de" in str(response.json()["error"]["details"])


def test_the_installation_default_answers_a_browser_that_asks_for_nothing_known(
    configured: dict[str, Any],
) -> None:
    configured["privacy_default_language"] = "en"
    body = _client(_user("anna")).get(NOTICE, HTTP_ACCEPT_LANGUAGE="fr-FR").json()
    assert body["language"] == "en"


def test_the_first_reader_is_asked_and_after_acknowledging_is_not() -> None:
    client = _client(_user("anna"))
    first = client.get(NOTICE).json()
    assert first["due"] == "first"
    assert first["acknowledged_at"] is None
    assert first["mode"] == "monthly"

    acknowledged = client.post(ACK, {"version": first["version"], "language": "de"}, format="json")

    assert acknowledged.status_code == 201, acknowledged.content
    again = client.get(NOTICE).json()
    assert again["due"] is None
    assert again["acknowledged_at"] is not None


def test_one_person_s_acknowledgement_is_nobody_else_s() -> None:
    anna, ben = _client(_user("anna")), _client(_user("ben"))
    version = anna.get(NOTICE).json()["version"]
    anna.post(ACK, {"version": version, "language": "de"}, format="json")

    assert ben.get(NOTICE).json()["due"] == "first"


def test_monthly_mode_asks_again_once_the_month_has_turned() -> None:
    user = _user("anna")
    client = _client(user)
    version = client.get(NOTICE).json()["version"]
    client.post(ACK, {"version": version, "language": "de"}, format="json")

    NoticeAcknowledgement.objects.filter(user=user).update(
        last_at=dt.datetime.now(dt.UTC).replace(day=1, hour=0, minute=0) - dt.timedelta(minutes=1)
    )

    assert client.get(NOTICE).json()["due"] == "month"


def test_once_mode_does_not_ask_again_next_month(configured: dict[str, Any]) -> None:
    configured["privacy_notice_mode"] = "once"
    user = _user("anna")
    client = _client(user)
    client.post(
        ACK, {"version": client.get(NOTICE).json()["version"], "language": "en"}, format="json"
    )
    NoticeAcknowledgement.objects.filter(user=user).update(
        last_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=90)
    )

    assert client.get(NOTICE).json()["due"] is None


def test_always_mode_asks_every_time(configured: dict[str, Any]) -> None:
    """For whoever reviews the notice: the window without waiting a month."""
    configured["privacy_notice_mode"] = "always"
    client = _client(_user("anna"))
    client.post(
        ACK, {"version": client.get(NOTICE).json()["version"], "language": "de"}, format="json"
    )

    assert client.get(NOTICE).json()["due"] == "always"


def test_a_changed_notice_is_due_again_for_somebody_who_acknowledged_the_old_one(
    configured: dict[str, Any],
) -> None:
    client = _client(_user("anna"))
    client.post(
        ACK, {"version": client.get(NOTICE).json()["version"], "language": "de"}, format="json"
    )

    configured["privacy_controller"] = "Successor AG, New Street 2, 54321 Example"

    assert client.get(NOTICE).json()["due"] == "changed"


def test_a_new_role_that_may_read_content_is_a_changed_notice() -> None:
    client = _client(_user("anna"))
    client.post(
        ACK, {"version": client.get(NOTICE).json()["version"], "language": "de"}, format="json"
    )

    StoredRole.objects.create(
        slug="auditors",
        label="Auditors",
        group_path="/finance/audit",
        permissions=["payload.read_any"],
    )

    body = client.get(NOTICE).json()
    assert body["due"] == "changed"
    access = next(s for s in body["sections"] if s["key"] == "access")
    assert any(item.startswith("Auditors: ") for item in access["items"])


def test_acknowledging_a_version_that_is_not_the_current_one_is_refused() -> None:
    """Otherwise the record says somebody read a text they were never shown."""
    response = _client(_user("anna")).post(
        ACK, {"version": "2020-01-01.0000000000000000", "language": "de"}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert NoticeAcknowledgement.objects.count() == 0


@pytest.mark.parametrize(
    "body",
    [{"language": "de"}, {"version": "", "language": "de"}, {"version": "x", "language": "tlh"}],
)
def test_an_acknowledgement_names_its_version_and_a_known_language(body: dict[str, Any]) -> None:
    response = _client(_user("anna")).post(ACK, body, format="json")
    assert response.status_code == 400
    assert NoticeAcknowledgement.objects.count() == 0


def test_a_body_that_is_not_an_object_is_the_caller_s_mistake() -> None:
    response = _client(_user("anna")).post(ACK, ["de"], format="json")
    assert response.status_code == 400


def test_acknowledging_again_moves_the_latest_time_and_keeps_the_first() -> None:
    user = _user("anna")
    client = _client(user)
    version = client.get(NOTICE).json()["version"]
    first = client.post(ACK, {"version": version, "language": "de"}, format="json").json()

    second = client.post(ACK, {"version": version, "language": "en"}, format="json")

    assert second.status_code == 200
    assert second.json()["first_at"] == first["first_at"]
    assert second.json()["last_at"] >= first["last_at"]
    assert second.json()["language"] == "en"
    assert NoticeAcknowledgement.objects.filter(user=user).count() == 1


def test_two_windows_acknowledging_at_once_leave_one_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second insert loses the race on the unique constraint and updates the winner's row."""
    from django.db import IntegrityError

    user = _user("anna")
    client = _client(user)
    version = client.get(NOTICE).json()["version"]
    client.post(ACK, {"version": version, "language": "de"}, format="json")

    def lost_the_race(**_: Any) -> Any:
        raise IntegrityError("uq_privacy_ack_user_version")

    monkeypatch.setattr(NoticeAcknowledgement.objects, "get_or_create", lost_the_race)
    response = client.post(ACK, {"version": version, "language": "en"}, format="json")

    assert response.status_code == 200
    assert NoticeAcknowledgement.objects.get(user=user).language == "en"


def test_the_acknowledgement_goes_with_the_account() -> None:
    user = _user("anna")
    client = _client(user)
    client.post(
        ACK, {"version": client.get(NOTICE).json()["version"], "language": "de"}, format="json"
    )

    user.delete()

    assert NoticeAcknowledgement.objects.count() == 0


def test_the_view_reads_the_process_settings_by_default() -> None:
    """The fixture replaces the settings; this is the one place that proves the real ones load."""
    assert get_settings().privacy_notice_mode in {"once", "monthly", "always"}


# == settings ======================================================================================


@pytest.mark.parametrize("mode", ["once", "monthly", "always", " ALWAYS "])
def test_every_documented_mode_is_accepted(mode: str) -> None:
    assert ManagementSettings(privacy_notice_mode=mode).privacy_notice_mode == mode.strip().lower()


def test_a_misspelled_mode_refuses_the_process() -> None:
    with pytest.raises(ValueError, match="AIRA_PRIVACY_NOTICE_MODE"):
        ManagementSettings(privacy_notice_mode="monthy")


def test_a_default_language_without_a_text_refuses_the_process() -> None:
    with pytest.raises(ValueError, match="AIRA_PRIVACY_DEFAULT_LANGUAGE"):
        ManagementSettings(privacy_default_language="fr")
