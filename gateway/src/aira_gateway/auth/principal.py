"""The authenticated caller, independent of how they authenticated."""

from __future__ import annotations

from dataclasses import dataclass

from aira_common.roles import has_oversight, is_governance, may_act_on_incidents
from aira_gateway.scopes import person as person_key


@dataclass(frozen=True, slots=True)
class Principal:
    """Resolved identity of a request. ``method`` is ``api_key`` | ``oidc`` | ``demo``.

    ``use_cases`` are the use-case slugs the principal may access (OIDC: from Keycloak groups and
    grants; a bound API key: its one use case, `FRD-205`). ``roles`` come from group membership
    (`ADR-0017`) and answer what membership cannot — whether this caller oversees the whole
    installation. Oversight is read-only: acting inside a use case stays with membership.
    """

    subject: str
    method: str
    #: The human-readable name, where the credential carries one (OIDC ``preferred_username``).
    #: **Never the identity** — `subject` is, because a name can be reassigned. It exists because
    #: an API key's subject is a username and an OIDC token's a directory id; `None` where the
    #: credential names nobody.
    username: str | None = None
    #: The *credential's* identity — an API key's prefix or an OIDC client id — answering "which
    #: system called" (`FRD-122` FR-5). Never contains part of a secret.
    credential: str | None = None
    label: str | None = None
    #: Which Keycloak realm minted this token (`FRD-118`); `None` for an API key and demo mode.
    issuer: str | None = None
    use_cases: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    #: The Keycloak group paths the token carried, verbatim (`FRD-209`). Resolving them needs the
    #: read-model, which the synchronous validator does not have, so it happens one layer out.
    groups: tuple[str, ...] = ()
    #: ``(use_case, role)`` for every grant the read-model resolved (`FRD-209`). The role matters: a
    #: *group* grant writes no member row, so `payloads.grant_role_in` reads it from here. Empty
    #: where no resolver ran — an additional source, not a replacement.
    grants: tuple[tuple[str, str], ...] = ()

    @property
    def person(self) -> str | None:
        """Who allowances are counted against — one human, whichever credential they used.

        The same rule as `Attribution.person`: a route has an `Attribution`, a read-only endpoint
        only the `Principal`, and an allowance must not depend on which one is in scope.
        """
        return person_key(self.subject, self.username)

    @property
    def is_governance(self) -> bool:
        """Whether this caller oversees every use case rather than a set of them."""
        return is_governance(self.roles)

    @property
    def is_oversight(self) -> bool:
        """Whether this caller may **see** every use case.

        Wider than :attr:`is_governance` by exactly IT Security (the `FRD-206` split): seeing every
        use case and seeing every *figure* are two questions.
        """
        return has_oversight(self.roles)

    @property
    def may_act_on_incidents(self) -> bool:
        """Whether this caller may **stop** traffic (`FRD-503` FR-6).

        Narrower than :attr:`is_oversight`: IT Steuerung sees every figure and writes nothing
        (PRD §154), so it may not use the kill switch.
        """
        return may_act_on_incidents(self.roles)
