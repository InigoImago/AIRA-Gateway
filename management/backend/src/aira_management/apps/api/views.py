"""Core API views (FRD-200)."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_common.access import usecases_from_group_paths
from aira_management.config.runtime import get_settings
from aira_management.rbac import MayRunTests
from aira_management.roles import ALL_ROLES


class MeView(APIView):
    """Return the authenticated user with the roles the server enforces and their use cases.

    **The roles are read from the user, not from the token (`ADR-0017`).** This view used to
    report `realm_access.roles` straight off the claim, which made it a *third* answer to "which
    roles does this caller hold" beside `sync_user_roles` and the permission classes. While all
    three read the same claim they agreed by accident; the moment roles came from group membership
    they did not, and the console — which decides what to offer from this response — was told the
    caller had no roles at all while the server happily let them through. Found live, by a Global
    Administrator being shown no "New use case" button.

    So it reports what the caller's Django groups say, which is what every permission class
    compares against. One answer, one source.
    """

    def get(self, request: Request) -> Response:
        claims: dict[str, Any] = request.auth if isinstance(request.auth, dict) else {}
        settings = get_settings()
        held = set(request.user.groups.values_list("name", flat=True))
        return Response(
            {
                "subject": claims.get("sub"),
                "username": request.user.get_username(),
                "email": getattr(request.user, "email", ""),
                "roles": [str(role) for role in ALL_ROLES if str(role) in held],
                # Slugs, not raw group paths. It returned the whole `groups` claim, which was
                # already loose and became actively wrong once that claim also carries the role
                # groups — a console asking "which use cases am I in" would have been told
                # `/aira/global-admins`.
                "use_cases": list(usecases_from_group_paths(claims.get("groups") or [])),
                # The key policy, so the console states the numbers the server enforces instead of
                # carrying its own copy. A second definition would be confidently wrong the first
                # time an installation changed the setting — and the reader would then be told a
                # refusal they cannot explain.
                "api_key_default_days": settings.api_key_default_days,
                "api_key_max_days": settings.api_key_max_days,
                # **The unit every money figure on this console is in**, for the same reason as
                # the two above and after the same failure: three screens said *"US dollars"* in
                # so many words — the model catalog, the use-case budget window and the
                # installation's — while `AIRA_CURRENCY` labelled the very same numbers `EUR` in
                # every CSV export, because that setting had exactly one reader in the whole system
                # and it was on the other plane. Somebody typed dollars into a form and received a
                # file that said euros.
                #
                # The console's argument for hard-coding it was *"every provider on this gateway
                # prices in dollars"*, which is a claim about vendors and not about an installation
                # — a reseller contract in euros makes it false, and nothing would have said so.
                "currency": settings.currency,
                # Whether to offer the pipeline-tests screen at all, answered with **the permission
                # class itself** (`ADR-0020`). It is an object-level question — "do you hold
                # `view_usecase` on anything" — so the console cannot derive it from the roles and
                # slugs above: `use_cases` here is the `/use-cases/<slug>` group convention only,
                # and somebody reaching a use case through a *grant* would be offered nothing.
                #
                # A nav entry that 403s is `FRD-206`'s defect; one that is missing for somebody who
                # may use it is the same defect inverted, and this had to be one or the other until
                # the server was asked.
                "may_test": MayRunTests().has_permission(request, None),
            }
        )
