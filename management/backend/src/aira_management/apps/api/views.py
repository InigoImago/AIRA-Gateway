"""Core API views (`FRD-200`)."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_common.access import usecases_from_group_paths
from aira_management.config.runtime import get_settings
from aira_management.rbac import MayRunTests, held_roles_of, permissions_of, role_definitions
from aira_management.roles import ALL_ROLES


class MeView(APIView):
    """The authenticated user, the roles the server enforces, and what the console must not guess.

    **Roles are read from the user's Django groups, not from the token** (`ADR-0017`): that is
    what every permission class compares against, so the console is told the same answer the
    server acts on. Each further field is a server-side fact the console would otherwise carry its
    own copy of.
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
                "roles": [str(role) for role in ALL_ROLES if str(role) in held]
                + [role.slug for role in held_roles_of(request.user) if not role.builtin],
                # Every role's name, so no screen restates one (`FRD-614` FR-10).
                "role_labels": {role.slug: role.label for role in role_definitions()},
                # What those roles may do (`FRD-614`): the console asks these, never the roles.
                "permissions": sorted(str(p) for p in permissions_of(request.user)),
                # Slugs, not raw group paths: the `groups` claim also carries the role groups.
                "use_cases": list(usecases_from_group_paths(claims.get("groups") or [])),
                # The key policy the server enforces, so the console states the same numbers.
                "api_key_default_days": settings.api_key_default_days,
                "api_key_max_days": settings.api_key_max_days,
                # The unit of every money figure on the console — the installation's
                # `AIRA_CURRENCY`, never an assumption about what providers price in.
                "currency": settings.currency,
                # Whether to offer the pipeline-tests screen, answered by the permission class
                # itself (`ADR-0020`): it is an object-level question the roles and slugs above
                # cannot answer (`FRD-206`).
                "may_test": MayRunTests().has_permission(request, None),
            }
        )
