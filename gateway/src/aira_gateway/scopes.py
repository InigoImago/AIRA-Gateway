"""Who a limit or a budget applies to (`FRD-400`, `FRD-405`, `FRD-407`).

Budgets (how much) and rate limits (how fast) scope identically, so the rule lives here once. Their
key formats differ for real reasons and are documented on `Scope`. Which rows bind a caller when
several could — the use case's pot over everybody, a person's own budget in place of the one for
everybody — is :func:`binding`, the hierarchy `ADR-0044` decides.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import Protocol

#: A **shared pot** for the whole use case: the first caller to arrive can spend all of it.
USE_CASE = "use_case"
#: The installation's own spend, which belongs to no use case (`FRD-610`): console model checks
#: before a release, unbound break-glass keys, demo traffic. Not a global cap — it binds only
#: requests that name no use case, so every request has a bucket.
INSTALLATION = "installation"
#: **Each member, individually**: one configured row, one counter per person, including people who
#: join later. Bounds every person the same way, where `USE_CASE` is a shared pot.
EACH_MEMBER = "each_member"
#: **One named person's own** (`FRD-407`): it takes the place of the `EACH_MEMBER` row of the same
#: period for that person alone, so it raises an allowance as readily as it lowers one. The row
#: names the person by the key their allowance is counted under (:func:`person`).
MEMBER = "member"
#: The scopes counted per person — one counter per person, whichever of them binds.
PER_PERSON = frozenset({EACH_MEMBER, MEMBER})


def person(subject: str | None, username: str | None) -> str | None:
    """Who an allowance is counted against: **one human, whichever credential they used**.

    The name where the credential carries one, the subject otherwise. An API key's subject already
    is its owner's username while an OIDC token's is a directory id, so keying on the subject gave
    one person two allowances. The fallback keeps a nameless caller (a service account) on its own
    stable key rather than in one shared pot. `Attribution.person` and `Principal.person` call this.

    **Rests on the directory not letting a person change `preferred_username`** (Keycloak's *Edit
    username*, off by default): renaming onto a colleague would move consumption onto their
    allowance and match their grants (`auth/grants.py`, `payloads._member_key`) and their own
    budget. A deployment requirement stated in `docs/INTEGRATIONS.md` §2, not a check — reading the
    realm setting needs the optional admin client, and a check that silently passes without it is
    no control.
    """
    return username or subject


@dataclass(frozen=True, slots=True)
class Scope:
    """A resolved scope: a use case, and optionally the one member it narrows to."""

    use_case: str
    member: str | None = None

    @classmethod
    def applying(
        cls, *, scope: str, use_case: str, caller: str | None, named: str = ""
    ) -> Scope | None:
        """The scope a configured row describes — or ``None`` if it does not bind this caller.

        ``caller`` is **the person** making the request (:func:`person`), so a key and a sign-in by
        the same human share one allowance; ``named`` is the person a `MEMBER` row names. A
        per-person row binds nobody when there is no caller. This is the single place a scope is
        added: the budget and the rate-limit path both follow. A row binding the caller here may
        still give way to another under :func:`binding`.
        """
        if scope == INSTALLATION:
            # Binds only requests that name no use case; Management refuses a row combining both.
            return cls("") if not use_case else None
        if scope == USE_CASE:
            return cls(use_case)
        if scope == EACH_MEMBER and caller:
            # One counter per person, under the key a row naming that person uses too.
            return cls(use_case, caller)
        if scope == MEMBER and caller and named == caller:
            # The person it names, on the counter they already have: replacing their per-person
            # budget by this one hands out no fresh allowance.
            return cls(use_case, caller)
        return None

    @property
    def usage_key(self) -> str:
        """The key budget consumption is accounted under.

        **Not free to change**: it is stored in ``budget_usage`` and is how an existing period's
        counters are found again. Altering it would silently stop finding them — every budget
        would read as unspent.
        """
        if not self.use_case and self.member is None:
            # Its own prefix, not `uc:` with an empty name: `_retire_usecase` sweeps counters by
            # `uc:{slug}` prefix, which with an empty slug is every counter there is.
            return "installation:"
        if self.member is None:
            return f"uc:{self.use_case}"
        return f"member:{self.use_case}:{self.member}"

    @property
    def bucket_key(self) -> str:
        """The key a rate-limit bucket lives under.

        The use case sits in a hash tag so every bucket one request must pass hashes to the same
        Redis Cluster slot — the all-or-nothing decision is one multi-key script, and Redis Cluster
        refuses a script whose keys live on different nodes. Ephemeral, so free to change.
        """
        if not self.use_case and self.member is None:
            # A literal tag: `rl:{}` is read by Redis Cluster as *no* tag and would scatter these
            # keys across slots.
            return "rl:{installation}:all"
        tag = f"rl:{{{self.use_case}}}"
        return f"{tag}:uc" if self.member is None else f"{tag}:member:{self.member}"

    @property
    def label(self) -> str:
        """How the scope is named to a caller in a refusal."""
        if not self.use_case and self.member is None:
            return "installation"
        return "use case" if self.member is None else "member"


class ScopedRow(Protocol):
    """What :func:`binding` reads of a configured budget or rate limit."""

    @property
    def scope(self) -> str: ...
    @property
    def use_case(self) -> str: ...
    @property
    def subject(self) -> str: ...
    @property
    def enabled(self) -> bool: ...


def binding[R: ScopedRow](
    rows: Iterable[R], *, caller: str | None, slot: Callable[[R], Hashable]
) -> list[R]:
    """The rows of one use case that bind ``caller``, the hierarchy applied (`ADR-0044`).

    - A row that is not per person binds as `Scope.applying` says: the use case's pot bounds
      everybody, whatever else applies to them.
    - Of the per-person rows that apply to the caller, **one per slot** — a budget's period, the one
      slot of a rate limit: the caller's own where it is on, else the one for everybody where that
      is on, else whichever is off, which refuses nothing and is still counted. The two share one
      counter, so binding both would book a request twice; and a person's own budget switched off
      hands them back to the one for everybody, never to no limit at all.

    Returned in the order given, lifted rows included: a caller decides with `enabled` whether a
    row refuses, and books every row it gets (`budgets.store.applicable`).
    """
    applying = [
        row
        for row in rows
        if Scope.applying(scope=row.scope, use_case=row.use_case, caller=caller, named=row.subject)
        is not None
    ]
    chosen: dict[Hashable, R] = {}
    for row in applying:
        if row.scope not in PER_PERSON:
            continue
        held = chosen.get(slot(row))
        if held is None or _precedence(row) > _precedence(held):
            chosen[slot(row)] = row
    return [row for row in applying if row.scope not in PER_PERSON or chosen[slot(row)] is row]


def _precedence(row: ScopedRow) -> tuple[bool, bool]:
    """On before off, then a person's own before the one for everybody."""
    return (bool(row.enabled), row.scope == MEMBER)
