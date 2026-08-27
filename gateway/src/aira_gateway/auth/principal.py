"""The authenticated caller, independent of how they authenticated."""

from __future__ import annotations

from dataclasses import dataclass

from aira_common.roles import has_oversight, is_governance, may_act_on_incidents
from aira_gateway.scopes import person as person_key


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
    #: The caller's human-readable name, when the credential carries one — OIDC's
    #: ``preferred_username``. **Never the identity**: `subject` is, and stays, what every audit
    #: row and every counter is keyed on, because a name can be reassigned and a subject cannot.
    #:
    #: It exists because the two credentials answer "who is this" in two different alphabets: an
    #: API key's subject is its owner's *username*, an OIDC token's is the directory's *user id*.
    #: A `member`-scoped budget or limit is written by an administrator typing a name, so without
    #: this it matched API-key traffic and silently matched nothing at all for the same person's
    #: browser or service-account traffic. Measured: four calls against a limit of one, all
    #: served. `None` where the credential names nobody, which is not the same as "".
    username: str | None = None
    #: The *credential's* identity — an API key's prefix, or an OIDC client id. Distinct from
    #: ``subject``, which is whose credential it is. This is the one the audit trail needs to
    #: answer "which system called" (FRD-122 FR-5), and it never contains part of a secret.
    credential: str | None = None
    label: str | None = None
    #: Which Keycloak realm minted this token (`FRD-118`). `None` for an API key and for demo
    #: mode. One issuer is the ordinary case and this is then the same value on every row; it earns
    #: its place during a migration, when "which realm was this decided on" has two answers and the
    #: audit trail is where somebody looks for them.
    issuer: str | None = None
    use_cases: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    #: The Keycloak group paths the token carried, verbatim (`FRD-209`).
    #:
    #: Kept rather than resolved on the spot because resolving needs a read-model lookup, and the
    #: validator is synchronous and has no database. The paths are the raw fact; which use cases
    #: they reach is a decision made one layer out, where the grants are.
    groups: tuple[str, ...] = ()
    #: ``(use_case, role)`` for every grant the read-model resolved for this caller (`FRD-209`).
    #:
    #: **The role, not only the slug.** `GroupGrantResolver.use_cases` answers with a
    #: `{slug: role}` mapping — its own test asserts that the granted role is carried through —
    #: and `_with_group_grants` used only the keys. So an administrator whose grant is on a
    #: *group*, which is the route `FR-6` exists for and the one the owner asked for by name, was
    #: read by the gateway as a plain member: `payloads.grant_role_in` re-derived the role from
    #: `use_case_members` alone, where a group grant writes no row.
    #:
    #: Measured on 2026-08-26: a `/ai/kundenservice` grant of `admin` on `uc-a` resolved to
    #: `"user"`. In a use case that restricts members to their own requests that administrator is
    #: then refused their colleagues' prompts and shown a narrowed trace list — while Management,
    #: which asks guardian, correctly treats them as an administrator. Two planes, one question,
    #: two answers.
    #:
    #: Empty where no resolver ran (an API key, or a token that names nothing to look up), which
    #: is why the member-row lookup stays: this is an *additional* source, not a replacement.
    grants: tuple[tuple[str, str], ...] = ()

    @property
    def person(self) -> str | None:
        """Who allowances are counted against — one human, whichever credential they used.

        The same rule the attribution carries, because the two are read in different places: a
        route has an `Attribution`, a read-only endpoint has only the `Principal`, and a person's
        allowance must not depend on which of the two happened to be in scope.
        """
        return person_key(self.subject, self.username)

    @property
    def is_governance(self) -> bool:
        """Whether this caller oversees every use case rather than a set of them."""
        return is_governance(self.roles)

    @property
    def is_oversight(self) -> bool:
        """Whether this caller may **see** every use case.

        Wider than :attr:`is_governance` by exactly IT Security — the split `FRD-206` had to make
        in the control plane, for the same reason: who may see every use case and who may see every
        *figure* are two questions.
        """
        return has_oversight(self.roles)

    @property
    def may_act_on_incidents(self) -> bool:
        """Whether this caller may **stop** traffic (`FRD-503` FR-6).

        A third question, and it took a live round to notice it was being answered with the second
        one. `is_oversight` includes IT Steuerung, which PRD §154 gives every figure and no write
        anywhere — so the gateway's kill switch was letting a read-only role stop traffic while
        Management refused it a global rule. Two planes, one question, two answers.
        """
        return may_act_on_incidents(self.roles)
