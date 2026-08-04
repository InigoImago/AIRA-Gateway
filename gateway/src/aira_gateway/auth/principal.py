"""The authenticated caller, independent of how they authenticated."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """Resolved identity of a request. ``method`` is ``api_key`` | ``oidc`` | ``demo``.

    ``use_cases`` are the use-case slugs the principal may access (OIDC: derived from
    Keycloak groups; api_key/demo: empty until Management-side binding lands, FRD-205).
    """

    subject: str
    method: str
    label: str | None = None
    use_cases: tuple[str, ...] = ()
