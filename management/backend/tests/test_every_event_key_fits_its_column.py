"""Every record key an event can have fits the outbox column it is written to (`FRD-204`).

A key is the entity's whole natural key — compaction keeps the last record per key, so a key
narrower than its entity would merge two of them — and the widest is a group grant's: a use case's
slug, `|` and a group path. PostgreSQL refuses a value wider than its column inside the change's
transaction, so a key that does not fit is a change nobody can make: granting a group on a deep
path, revoking it, and retiring the use case that holds it all answered `500`.

SQLite, which this suite runs on, stores any length, so the width compared here is the one the
migrations declared, read back from the schema they built — the column PostgreSQL enforces.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from aira_management.apps.api.authentication import USERNAME_MAX_LENGTH
from aira_management.apps.outbox.models import OutboxEvent
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant
from aira_management.rbac import sync_user_roles
from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"


def _declared_width(table: str, column: str) -> int:
    """The width the schema declares for ``column``, as both backends' introspection reports it."""
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table)
    width = next(field.display_size for field in description if field.name == column)
    assert isinstance(width, int), (table, column, width)
    return width


def test_the_outbox_holds_the_widest_key_an_event_can_have() -> None:
    """Held to the fields a key is made of: widening one of them fails here, not in production."""
    slug = UseCase._meta.get_field("slug").max_length
    path = UseCaseGroupGrant._meta.get_field("group_path").max_length
    assert slug is not None and path is not None

    widest = slug + len("|") + max(path, USERNAME_MAX_LENGTH)

    assert widest <= _declared_width(OutboxEvent._meta.db_table, "key")


def test_granting_the_deepest_path_and_everything_that_ends_it_write_keys_that_fit() -> None:
    """The longest slug and the longest path the API accepts, through every change that keys an
    event on both: the grant, its revocation, a second grant, and the retirement that revokes it."""
    admin = get_user_model().objects.create(username="root")
    sync_user_roles(admin, role_claims("global-admin"))
    client = APIClient()
    client.force_authenticate(user=admin)
    slug = "uc-" + "x" * 61
    path = "/" + "g" * 254
    assert client.post(BASE, {"slug": slug, "name": "Deep"}, format="json").status_code == 201

    granted = client.post(f"{BASE}{slug}/groups/", {"group_path": path}, format="json")
    assert granted.status_code == 201, granted.content
    revoked = client.delete(f"{BASE}{slug}/groups/revoke/?group_path={quote(path)}")
    assert revoked.status_code == 200, revoked.content
    again = client.post(f"{BASE}{slug}/groups/", {"group_path": path}, format="json")
    assert again.status_code == 201, again.content
    assert client.delete(f"{BASE}{slug}/").status_code == 204

    grant_keys = list(
        OutboxEvent.objects.filter(event_type__startswith="use_case_group.").values_list(
            "key", flat=True
        )
    )
    assert grant_keys == [f"{slug}|{path}"] * 4, "grant, revocation, grant, retirement"
    width = _declared_width(OutboxEvent._meta.db_table, "key")
    assert max(len(key) for key in OutboxEvent.objects.values_list("key", flat=True)) <= width
