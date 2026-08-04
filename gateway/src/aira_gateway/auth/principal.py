"""The authenticated caller, independent of how they authenticated."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """Resolved identity of a request. ``method`` is ``api_key`` | ``oidc`` | ``demo``."""

    subject: str
    method: str
    label: str | None = None
