"""Who a limit or a budget applies to.

Budgets (FRD-400) and rate limits (FRD-405) answer different questions — how much, and how fast —
but they scope those answers identically: to a whole use case, or to one named member of it. That
rule was written twice, in two slightly different shapes, and each stored its key in its own
format. Adding a third scope (per API key, say) therefore meant finding and matching two places
that had no reason to be discovered together.

The rule lives here once. The two key formats stay separate and are documented below, because
they differ for real reasons rather than by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

USE_CASE = "use_case"
MEMBER = "member"
#: **Each member, individually** — one configured row, one counter per person.
#:
#: `MEMBER` names somebody: it is the answer to "this person in particular". `EACH_MEMBER` is the
#: answer to "everybody, but separately", which is what an administrator wants far more often —
#: a fair share per head, without listing the heads. Configured once, it applies to whoever turns
#: up, including people who joined the use case afterwards.
#:
#: The distinction that makes it not merely a convenience: a `USE_CASE` budget is a **shared pot**
#: — the first caller to arrive can spend all of it — while this one bounds every person the same
#: way. Those are different governance decisions and neither substitutes for the other.
EACH_MEMBER = "each_member"


@dataclass(frozen=True, slots=True)
class Scope:
    """A resolved scope: a use case, and optionally the one member it narrows to."""

    use_case: str
    member: str | None = None

    @classmethod
    def applying(
        cls, *, scope: str, use_case: str, subject: str, caller: str | None
    ) -> Scope | None:
        """The scope a configured row describes — or ``None`` if it does not bind this caller.

        ``subject`` is the member named in the configuration; ``caller`` is who is making the
        request. A use-case row binds everyone; a member row binds only its own subject, and
        binds nobody at all when the request carries no subject.

        This is the single place a new scope is added: give it a branch here and both the budget
        and the rate-limit path follow, instead of one of them being forgotten.
        """
        if scope == USE_CASE:
            return cls(use_case)
        if scope == MEMBER and caller and subject == caller:
            return cls(use_case, caller)
        if scope == EACH_MEMBER and caller:
            # The row names nobody; the **caller** is the subject. So one configured row produces a
            # counter per person, under exactly the key a row naming that person would have used —
            # which is why an administrator can narrow one individual later without the shared
            # history moving to a different key.
            return cls(use_case, caller)
        return None

    @property
    def usage_key(self) -> str:
        """The key budget consumption is accounted under.

        This shape is **not free to change**: it is stored in the ``budget_usage`` table and is
        how an existing period's counters are found again. Altering it would not lose the rows,
        it would silently stop finding them — every budget would read as unspent.
        """
        if self.member is None:
            return f"uc:{self.use_case}"
        return f"member:{self.use_case}:{self.member}"

    @property
    def bucket_key(self) -> str:
        """The key a rate-limit bucket lives under.

        The use case sits in a hash tag so that every bucket a single request must pass hashes to
        the same Redis Cluster slot — the all-or-nothing decision is one multi-key script, and
        Redis Cluster refuses a script whose keys live on different nodes. These keys are
        ephemeral, so unlike ``usage_key`` this shape may change freely.
        """
        tag = f"rl:{{{self.use_case}}}"
        return f"{tag}:uc" if self.member is None else f"{tag}:member:{self.member}"

    @property
    def label(self) -> str:
        """How the scope is named to a caller in a refusal."""
        return "use case" if self.member is None else "member"
