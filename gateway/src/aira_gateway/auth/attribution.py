"""Use-case selection and request attribution (FRD-102).

The use case is chosen by the client (header overrides path) and, for OIDC, authorized
against the caller's Keycloak group membership. Membership groups live under
``/use-cases/<slug>``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import Request

USE_CASE_GROUP_PREFIX = "/use-cases/"
USE_CASE_HEADER = "x-aira-use-case"
USE_CASE_PATH_KEY = "aira_use_case_path"


@dataclass(frozen=True, slots=True)
class Attribution:
    """What a request is attributed to: identity + selected use case."""

    subject: str
    method: str
    use_case: str | None


def usecases_from_groups(groups: Iterable[str]) -> tuple[str, ...]:
    """Extract use-case slugs from Keycloak group paths (``/use-cases/<slug>``)."""
    slugs: list[str] = []
    for group in groups:
        if group.startswith(USE_CASE_GROUP_PREFIX):
            slug = group[len(USE_CASE_GROUP_PREFIX) :].strip("/").split("/")[-1]
            if slug:
                slugs.append(slug)
    return tuple(dict.fromkeys(slugs))  # de-duplicate, preserve order


def resolve_use_case(request: Request) -> str | None:
    """Resolve the target use case: ``X-AIRA-Use-Case`` header overrides the ``/uc/<slug>`` path."""
    header = request.headers.get(USE_CASE_HEADER)
    if header and header.strip():
        return header.strip()
    path_slug = request.scope.get(USE_CASE_PATH_KEY)
    return path_slug if isinstance(path_slug, str) else None
