"""What a request asks about its caller is answered once per request — and forgotten the moment the
request itself changes access (`aira_management.memo`).

The memo is what took a page of the use-case list from about four hundred queries to a dozen; this
file is about the half that makes it safe: a membership, a group grant, an object permission, a role
or a user's groups written during a request must not leave an earlier answer standing.
"""

from __future__ import annotations

import pytest
from aira_management.apps.roles.models import StoredRole
from aira_management.apps.usecases.access import holds_a_grant, may_call, may_manage
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant, UseCaseMembership
from aira_management.apps.usecases.views import _grant
from aira_management.memo import remembered, remembering
from aira_management.rbac import permissions_of, sync_user_groups
from django.contrib.auth import get_user_model

from aira_common.permissions import Permission

pytestmark = pytest.mark.django_db


@pytest.fixture
def usecase() -> UseCase:
    return UseCase.objects.create(slug="uc", name="UC")


@pytest.fixture
def ada():
    return get_user_model().objects.create(username="ada")


def test_an_answer_is_computed_once_inside_a_request_and_every_time_outside() -> None:
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return len(calls)

    with remembering():
        assert remembered("k", compute) == remembered("k", compute) == 1
    assert remembered("k", compute) == 2
    assert remembered("k", compute) == 3


def test_a_membership_written_in_the_request_is_seen_by_the_next_question(usecase, ada) -> None:
    with remembering():
        assert not holds_a_grant(ada, usecase)
        assert not may_call(ada, usecase)

        UseCaseMembership.objects.create(use_case=usecase, user=ada, role=UseCaseMembership.USER)

        assert holds_a_grant(ada, usecase)
        assert may_call(ada, usecase)


def test_an_object_permission_granted_in_the_request_is_seen(usecase, ada) -> None:
    with remembering():
        assert not may_manage(ada, usecase)

        _grant(ada, usecase, UseCaseMembership.ADMIN)

        assert may_manage(ada, usecase)


def test_a_group_grant_and_a_change_of_groups_are_seen(usecase, ada) -> None:
    with remembering():
        assert not holds_a_grant(ada, usecase)
        UseCaseGroupGrant.objects.create(use_case=usecase, group_path="/team")
        sync_user_groups(ada, {"groups": ["/team"]})

        assert holds_a_grant(ada, usecase)

        sync_user_groups(ada, {"groups": []})
        assert not holds_a_grant(ada, usecase)


def test_a_role_changed_in_the_request_is_seen(ada) -> None:
    StoredRole.objects.create(slug="readers", label="Readers", group_path="/readers")
    sync_user_groups(ada, {"groups": ["/readers"]})
    with remembering():
        assert Permission.REPORT_READ_ALL not in permissions_of(ada)

        role = StoredRole.objects.get(slug="readers")
        role.permissions = [str(Permission.REPORT_READ_ALL)]
        role.save()

        assert Permission.REPORT_READ_ALL in permissions_of(ada)
