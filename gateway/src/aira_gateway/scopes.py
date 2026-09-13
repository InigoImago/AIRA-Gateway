"""Who a limit or a budget applies to (`FRD-400`, `FRD-405`).

Budgets (how much) and rate limits (how fast) scope identically, so the rule lives here once. Their
key formats differ for real reasons and are documented on `Scope`. There is deliberately no scope
naming one person: singling somebody out is not a decision this product makes easy.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A **shared pot** for the whole use case: the first caller to arrive can spend all of it.
USE_CASE = "use_case"
#: The installation's own spend, which belongs to no use case (`FRD-610`): console model checks
#: before a release, unbound break-glass keys, demo traffic. Not a global cap — it binds only
#: requests that name no use case, so every request has a bucket.
INSTALLATION = "installation"
#: **Each member, individually**: one configured row, one counter per person, including people who
#: join later. Bounds every person the same way, where `USE_CASE` is a shared pot.
EACH_MEMBER = "each_member"


def person(subject: str | None, username: str | None) -> str | None:
    """Who an allowance is counted against: **one human, whichever credential they used**.

    The name where the credential carries one, the subject otherwise. An API key's subject already
    is its owner's username while an OIDC token's is a directory id, so keying on the subject gave
    one person two allowances. The fallback keeps a nameless caller (a service account) on its own
    stable key rather than in one shared pot. `Attribution.person` and `Principal.person` call this.

    **Rests on the directory not letting a person change `preferred_username`** (Keycloak's *Edit
    username*, off by default): renaming onto a colleague would move consumption onto their
    allowance and match their grants (`auth/grants.py`, `payloads._member_key`). A deployment
    requirement stated in `docs/INTEGRATIONS.md` §2, not a check — reading the realm setting needs
    the optional admin client, and a check that silently passes without it is no control.
    """
    return username or subject


@dataclass(frozen=True, slots=True)
class Scope:
    """A resolved scope: a use case, and optionally the one member it narrows to."""

    use_case: str
    member: str | None = None

    @classmethod
    def applying(cls, *, scope: str, use_case: str, caller: str | None) -> Scope | None:
        """The scope a configured row describes — or ``None`` if it does not bind this caller.

        ``caller`` is **the person** making the request (:func:`person`), so a key and a sign-in by
        the same human share one allowance. An each-member row binds nobody when there is no
        caller. This is the single place a scope is added: the budget and the rate-limit path both
        follow.
        """
        if scope == INSTALLATION:
            # Binds only requests that name no use case; Management refuses a row combining both.
            return cls("") if not use_case else None
        if scope == USE_CASE:
            return cls(use_case)
        if scope == EACH_MEMBER and caller:
            # One counter per person, under the key a row naming that person would have used.
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
            # Its own prefix, not `uc:` with an empty name: `_delete_usecase` sweeps counters by
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
