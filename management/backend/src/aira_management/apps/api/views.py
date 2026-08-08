"""Core API views (FRD-200)."""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from aira_management.config.runtime import get_settings


class MeView(APIView):
    """Return the authenticated user with their realm roles and use-case groups."""

    def get(self, request: Request) -> Response:
        claims: dict[str, Any] = request.auth if isinstance(request.auth, dict) else {}
        realm_access = claims.get("realm_access") or {}
        settings = get_settings()
        return Response(
            {
                "subject": claims.get("sub"),
                "username": request.user.get_username(),
                "email": getattr(request.user, "email", ""),
                "roles": realm_access.get("roles", []),
                "use_cases": claims.get("groups", []),
                # The key policy, so the console states the numbers the server enforces instead of
                # carrying its own copy. A second definition would be confidently wrong the first
                # time an installation changed the setting — and the reader would then be told a
                # refusal they cannot explain.
                "api_key_default_days": settings.api_key_default_days,
                "api_key_max_days": settings.api_key_max_days,
            }
        )
