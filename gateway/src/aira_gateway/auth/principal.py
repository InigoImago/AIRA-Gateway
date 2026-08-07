"""The authenticated caller, independent of how they authenticated."""

from __future__ import annotations

from dataclasses import dataclass

from aira_common.roles import has_oversight, is_governance


@dataclass(frozen=True, slots=True)
class Principal:
    """Resolved identity of a request. ``method`` is ``api_key`` | ``oidc`` | ``demo``.

    ``use_cases`` are the use-case slugs the principal may access (OIDC: derived from
    Keycloak groups; api_key/demo: empty until Management-side binding lands, FRD-205).

    ``roles`` are the realm roles the token carried (ADR-0009). They answer a question
    membership cannot: whether this caller oversees the whole installation. Oversight is
    read-only — a role grants a view across use cases, never the right to act inside one, which
    stays with membership. Only OIDC principals have them; an API key is issued for a use case,
    not for a person with a standing in the organisation.
    """

    subject: str
    method: str
    #: The *credential's* identity — an API key's prefix, or an OIDC client id. Distinct from
    #: ``subject``, which is whose credential it is. This is the one the audit trail needs to
    #: answer "which system called" (FRD-122 FR-5), and it never contains part of a secret.
    credential: str | None = None
    label: str | None = None
    use_cases: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()

    @property
    def is_governance(self) -> bool:
        """Whether this caller oversees every use case rather than a set of them."""
        return is_governance(self.roles)

    @property
    def is_oversight(self) -> bool:
        """Whether this caller may act across use cases in an incident (`FRD-503` FR-6).

        Wider than :attr:`is_governance` by exactly IT Security, which is the role whose job this
        is — the same split `FRD-206` had to make in the control plane, for the same reason: who
        may *see* every use case and who may see every *figure* are two questions.
        """
        return has_oversight(self.roles)
