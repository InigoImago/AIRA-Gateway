"""A use case's slug and a model's name cross a system boundary, so neither may be edited.

Both are the same defect and it is worth naming as one: **an identity that crosses a boundary is
mutable on one side only.** Management owns a row; the gateway owns a different database, fed over
Kafka, keyed on the string this plane sends. A rename here is not a rename — it is an abandonment
on this side and an orphan on the other, and only this side is told.

Measured on 2026-08-27 against the running stack, before either check existed. One `PATCH` by a
use-case administrator on their own use case:

    Management                    knows only the new slug (404 for the old)
    gateway `use_cases`           two rows — the old one intact, and **no tombstone**
    gateway `api_keys`            still bound to the old slug, still active
    gateway `pipeline_configs`    still on the old slug, still enforcing
    the key issued beforehand     **still served, 200**

And one `PATCH` by a Global Administrator on a catalogued model: the gateway ended up holding both
names, the old one still `approved`, while Management answered 404 for it.

What that costs is governance rather than tidiness. Retirement cannot reach the orphan — `FRD-607`
writes the tombstone for the *new* slug, so `refuse_if_retired` never fires and the keys go on
working, which is the one thing that feature exists to prevent. Nor can a key revocation, a budget,
a rate limit or a purge. For a model it reopens the loophole `FRD-307` closed: catalogued,
approved, and permanently beyond the reach of the plane that could un-approve it.

**Refused, not ignored.** `read_only` would answer `200` with the old value, and a caller who
`PATCH`es a slug and reads `200` believes they renamed it — *a value silently transformed is worse
than one refused, because only the refusal is visible* (`FRD-124`).
"""

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.views import _grant
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

USE_CASES = "/api/v1/use-cases/"
MODELS = "/api/v1/models/"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _administrator(username: str, usecase: UseCase):
    """Somebody who administers exactly this use case and holds no organisation-wide role.

    The position that matters: the rename was reachable by the party governance exists to
    constrain, on their own use case, with no role at all.
    """
    user = _user(username)
    _grant(user, usecase, UseCaseMembership.ADMIN)
    UseCaseMembership.objects.create(use_case=usecase, user=user, role=UseCaseMembership.ADMIN)
    return get_user_model().objects.get(pk=user.pk)


# ---- a use case ---------------------------------------------------------------------------


def test_a_use_case_administrator_cannot_rename_their_use_case() -> None:
    usecase = UseCase.objects.create(slug="keep-me", name="Keep me")
    admin = _administrator("uc-admin", usecase)

    response = _client(admin).patch(f"{USE_CASES}keep-me/", {"slug": "renamed"}, format="json")

    assert response.status_code == 400, response.content
    assert "identity" in str(response.json()).lower()
    usecase.refresh_from_db()
    assert usecase.slug == "keep-me"


def test_not_even_a_global_administrator_renames_one() -> None:
    """The rule is about the **boundary**, not about who is trusted.

    A Global Administrator may do everything else here, and renaming still leaves the gateway
    holding a use case nothing can retire. Somebody senior enough to be allowed would produce
    exactly the same orphan.
    """
    UseCase.objects.create(slug="keep-me", name="Keep me")
    admin = _user("ga", "global-admin")

    response = _client(admin).patch(f"{USE_CASES}keep-me/", {"slug": "renamed"}, format="json")

    assert response.status_code == 400, response.content
    assert not UseCase.objects.filter(slug="renamed").exists()


def test_the_name_and_description_are_still_editable() -> None:
    """The point is that a *display* name is free and an *identity* is not — a check that refused
    both would have taken away the ordinary edit this endpoint is mostly used for."""
    usecase = UseCase.objects.create(slug="keep-me", name="Old")
    admin = _administrator("uc-admin", usecase)

    response = _client(admin).patch(
        f"{USE_CASES}keep-me/", {"name": "New", "description": "why"}, format="json"
    )

    assert response.status_code == 200, response.content
    usecase.refresh_from_db()
    assert (usecase.slug, usecase.name, usecase.description) == ("keep-me", "New", "why")


def test_sending_the_slug_unchanged_is_not_a_rename() -> None:
    """A `PUT` carries every field, including the one that may not change.

    Refusing an unchanged value would make the full-document update impossible — and the console
    sends one. The check is about a *change*, which is why it compares rather than forbids.
    """
    usecase = UseCase.objects.create(slug="keep-me", name="Old")
    admin = _administrator("uc-admin", usecase)

    response = _client(admin).put(
        f"{USE_CASES}keep-me/", {"slug": "keep-me", "name": "New"}, format="json"
    )

    assert response.status_code == 200, response.content


def test_creating_a_use_case_still_names_it() -> None:
    """The slug is writable exactly once, and this is that once."""
    admin = _user("ga", "global-admin")

    response = _client(admin).post(USE_CASES, {"slug": "brand-new", "name": "N"}, format="json")

    assert response.status_code == 201, response.content
    assert response.json()["slug"] == "brand-new"


# ---- a catalogued model -------------------------------------------------------------------


def test_a_model_cannot_be_renamed() -> None:
    Model.objects.create(name="vendor-a-1", provider="mock", approved=True)
    admin = _user("ga", "global-admin")

    response = _client(admin).patch(f"{MODELS}vendor-a-1/", {"name": "vendor-a-2"}, format="json")

    assert response.status_code == 400, response.content
    assert "identity" in str(response.json()).lower()
    assert Model.objects.filter(name="vendor-a-1").exists()
    assert not Model.objects.filter(name="vendor-a-2").exists()


def test_the_display_name_is_the_field_for_what_people_read() -> None:
    """Named in the refusal, and it has to work — a refusal that points at a field which does not
    do the job is a refusal somebody works around."""
    Model.objects.create(name="vendor-a-1", provider="mock", approved=True)
    admin = _user("ga", "global-admin")

    response = _client(admin).patch(
        f"{MODELS}vendor-a-1/", {"display_name": "Vendor A (fast)"}, format="json"
    )

    assert response.status_code == 200, response.content
    assert Model.objects.get(name="vendor-a-1").display_name == "Vendor A (fast)"


def test_the_upsert_by_name_still_corrects_a_price() -> None:
    """`ModelViewSet.create` upserts by name, so it hands the serializer an existing row with the
    **same** name. That is not a change, and a check that could not tell the difference would have
    broken the seed and every re-post of a price."""
    Model.objects.create(name="vendor-a-1", provider="mock", approved=True)
    admin = _user("ga", "global-admin")

    response = _client(admin).post(
        MODELS,
        {
            "name": "vendor-a-1",
            "provider": "mock",
            "input_price_per_million": "1.50",
            "output_price_per_million": "3.00",
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    assert Model.objects.count() == 1
