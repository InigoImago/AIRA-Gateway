"""Core API views (FRD-200)."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class MeView(APIView):
    """Return the authenticated user with their realm roles and use-case groups."""

    def get(self, request: Request) -> Response:
        claims: dict[str, Any] = request.auth if isinstance(request.auth, dict) else {}
        realm_access = claims.get("realm_access") or {}
        return Response(
            {
                "subject": claims.get("sub"),
                "username": request.user.get_username(),
                "email": getattr(request.user, "email", ""),
                "roles": realm_access.get("roles", []),
                "use_cases": claims.get("groups", []),
            }
        )
