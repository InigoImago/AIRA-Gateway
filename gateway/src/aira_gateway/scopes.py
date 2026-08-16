"""Who a limit or a budget applies to.

Budgets (FRD-400) and rate limits (FRD-405) answer different questions — how much, and how fast —
but they scope those answers identically: to a whole use case, or to every member of it
separately. That rule was written twice, in two slightly different shapes, and each stored its key
in its own format. Adding a scope therefore meant finding and matching two places that had no
reason to be discovered together.

**A third scope, `member`, named one person and was removed on the owner's decision (2026-08-14):**
singling somebody out is not a governance decision this product wants to make easy. What remains
says everything an administrator actually needs — a shared pot, or the same allowance for
everybody — and neither substitutes for the other. Rows already carrying it are deleted by a
migration in each plane rather than left in place, because a stored scope that no longer resolves
is a rule that is enforced by nothing and visible in nothing.

The rule lives here once. The two key formats stay separate and are documented below, because
they differ for real reasons rather than by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

USE_CASE = "use_case"
#: **Each member, individually** — one configured row, one counter per person.
#:
#: The answer to "everybody, but separately": a fair share per head, without listing the heads.
#: Configured once, it applies to whoever turns up, including people who join afterwards.
#:
#: The distinction that makes it not merely a convenience: a `USE_CASE` budget is a **shared pot**
#: — the first caller to arrive can spend all of it — while this one bounds every person the same
#: way. Those are different governance decisions and neither substitutes for the other.
EACH_MEMBER = "each_member"


def person(subject: str | None, username: str | None) -> str | None:
    """Who an allowance is counted against: **one human, whichever credential they used**.

    The name where the credential carries one, the subject otherwise.

    Both credentials carry a name (`auth/service.py`, `auth/oidc.py`), and they disagree about the
    subject: an API key's subject already *is* its owner's username, while an OIDC token's is the
    directory's user id. Keying on the subject therefore gave one person two per-head budgets and
    two rate-limit buckets — a limit of ten meant twenty to anybody holding both, and the console
    had to warn about it in two places.

    The fallback matters and is not a formality: a token with no ``preferred_username`` — a service
    account, an older realm mapping — keys on its subject, which is stable and unique. Falling back
    to nothing would put every nameless caller in **one shared pot**, which is the opposite failure
    and a much worse one.

    Written here, next to the keys it feeds, rather than as a property of the two credential types:
    the rule is about what a *scope* means, and a copy on each of them is two copies to keep in
    step (`Attribution.person` and `Principal.person` both call this).

    **The assumption this rests on, stated where it is relied on.** The name comes from a token's
    `preferred_username`, so it is only an identity if the directory does not let a person change
    it. Keycloak leaves *Edit username* off by default; with it on, renaming yourself to a
    colleague moves your consumption onto their allowance — and the same claim also resolves grants
    written to a person (`auth/grants.py`) and decides who administers a use case
    (`payloads._member_key`). It is a deployment requirement rather than a check, because reading
    the realm setting needs the optional admin client and a check that silently passes wherever
    that is unconfigured is a control this project would not ship. `docs/INTEGRATIONS.md` §2 is
    where an integrator is told.
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

        ``caller`` is who is making the request. A use-case row binds everyone; an each-member row
        binds the caller as an individual, and binds nobody at all when the request carries no
        subject.

        This is the single place a scope is added: give it a branch here and both the budget and
        the rate-limit path follow, instead of one of them being forgotten.

        ``caller`` is **the person, not the credential** — see :func:`person`. A key and a sign-in
        by the same human share one allowance.

        That was a known limit for months and is written down here because the note it replaces
        said what the fix would be: *"a stable identity for a person across credentials rather than
        a scope that names one."* This is that identity. Until `FRD-606` there was nothing to build
        it from — the audit row carried only a subject, and the two credentials answer "who is
        this" in different alphabets — and once the name was recorded beside the subject for the
        *display*, using it for the *decision* was the smaller half of the same idea.

        A row's ``subject`` is **no longer a parameter**: it existed for the removed ``member``
        scope, which matched a typed name against either alphabet. Nothing reads it now, and a
        parameter nothing reads is a rule the code appears to have and does not.
        """
        if scope == USE_CASE:
            return cls(use_case)
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
