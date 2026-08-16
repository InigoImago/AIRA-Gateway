"""`?may_call=true` answers the list, and decides no route's object (2026-08-15).

Three questions live next to each other on the use-case surface and the console needs all three:

    what may I **see**          `scope_queryset` — a `django-guardian` object permission
    what may I **administer**   `is_member` / `may_manage`
    what may I **call**         `may_call_queryset` — the *gateway's* rule, over a token's groups

The third is genuinely wider than the first, and deliberately: the `/use-cases/<slug>` Keycloak
convention (`FRD-102`) lets a token name a use case at the gateway without granting any object
permission in Management. That is what makes the parameter useful — the console has to offer the
use cases a dry run will actually be accepted for — and it is exactly why it must not decide
**which object a route resolves**.

It did. `get_queryset` answered the parameter unconditionally and DRF resolves every detail route
and every `@action(detail=True)` through `get_object()` → `get_queryset()`. The mutations are not
reachable — they ask `_may_manage`/`_is_member` independently — so it was disclosure rather than
escalation, and it covered the member list, the budgets, the rate limits, the pipeline
configuration and the API-key metadata of a use case the caller holds no `view_usecase` for.
"""

from __future__ import annotations

from typing import Any

import pytest
from aira_management.apps.usecases.models import UseCase
from aira_management.rbac import sync_user_groups, sync_user_roles
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"
SLUG = "secret-uc"

#: Every read on the detail surface. Named rather than sampled: the defect was one query parameter
#: reaching *all* of them, so a test that checked the object and not its panels would have passed
#: while the member list stayed open.
DETAIL_READS = ("", "members/", "groups/", "budgets/", "rate-limits/", "pipeline/", "api-keys/")


def _user(username: str, *roles: str, groups: tuple[str, ...] = ()) -> Any:
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    sync_user_groups(user, {"groups": list(groups)})
    return user


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def owned() -> Any:
    """One use case, created by a Global Administrator, with something on every panel."""
    admin = _user("owner", "global-admin")
    client = _client(admin)
    assert client.post(BASE, {"slug": SLUG, "name": "Secret"}, format="json").status_code == 201
    client.post(
        f"{BASE}{SLUG}/budgets/",
        {"scope": "use_case", "subject": "", "period": "day", "limit_requests": 5},
        format="json",
    )
    return admin


def test_the_convention_alone_does_not_open_a_detail_route(owned: Any) -> None:
    """The caller the defect was found with: in the Keycloak group that names the use case, holding
    no role, no grant and no membership — so the gateway would accept their traffic for it and
    Management shows them nothing."""
    del owned
    outsider = _client(_user("out", groups=(f"/use-cases/{SLUG}",)))

    for suffix in DETAIL_READS:
        plain = outsider.get(f"{BASE}{SLUG}/{suffix}")
        widened = outsider.get(f"{BASE}{SLUG}/{suffix}?may_call=true")

        assert plain.status_code == 404, suffix
        assert widened.status_code == 404, (
            f"'{suffix}' answered {widened.status_code} with the query parameter and 404 without "
            "it — a scope widened by a query string on a route that never meant it"
        )


def test_the_list_still_answers_the_gateways_question(owned: Any) -> None:
    """The feature itself, unchanged. Without the parameter the list is what this caller may
    **see** — nothing; with it, what the gateway will accept from their token."""
    del owned
    outsider = _client(_user("out", groups=(f"/use-cases/{SLUG}",)))

    visible = outsider.get(BASE).json()
    callable_ones = outsider.get(f"{BASE}?may_call=true").json()

    assert visible["results"] == []
    assert [row["slug"] for row in callable_ones["results"]] == [SLUG]


def test_the_widened_list_is_still_paged_and_searchable(owned: Any) -> None:
    """It shares the list's envelope rather than having one of its own — a second shape here is a
    second thing for the console's pager to get wrong."""
    del owned
    admin = _client(_user("many", "global-admin", groups=(f"/use-cases/{SLUG}",)))
    for index in range(3):
        UseCase.objects.create(slug=f"other-{index}", name=f"Other {index}")

    page = admin.get(f"{BASE}?may_call=true").json()
    searched = admin.get(f"{BASE}?may_call=true&q=secret").json()

    assert set(page) == {"count", "page", "page_size", "pages", "results"}
    assert page["count"] == 1, "a global admin may see everything and call only what a grant gives"
    assert [row["slug"] for row in searched["results"]] == [SLUG]


def test_a_member_sees_the_same_use_case_either_way(owned: Any) -> None:
    """The ordinary case, so the fix cannot have narrowed anything: somebody with a real grant
    reads the detail with the parameter and without it."""
    del owned
    member = _user("member", groups=(f"/use-cases/{SLUG}",))
    _client(_user("owner2", "global-admin")).post(
        f"{BASE}{SLUG}/members/", {"username": "member", "role": "user"}, format="json"
    )
    client = _client(member)

    assert client.get(f"{BASE}{SLUG}/").status_code == 200
    assert client.get(f"{BASE}{SLUG}/?may_call=true").status_code == 200
    assert client.get(f"{BASE}{SLUG}/budgets/?may_call=true").status_code == 200
