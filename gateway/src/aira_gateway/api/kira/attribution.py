"""Attribution for a surface that has no notion of a use case (FRD-107 §5.3).

Every AIRA control is scoped to a use case. The contract has no field for one: a caller is a user
in a group, and that is all. So a KIRA request arrives with no selector — and without one it cannot
be budgeted, limited, priced or reported.

The resolution, in order:

1. **Exactly one use case in the caller's memberships** → that one. `FRD-102` already derives them
   from ``/use-cases/<slug>`` groups, so for the common case — an application belonging to one use
   case — attribution is automatic and the migrating client changes nothing.
2. **An explicit selector** → that one, if they are a member. Authorised by ``use_case_refusal``,
   which both surfaces share: a selector chooses among what a caller already has and never adds
   to it.
3. **Several memberships and no selector** → **403 naming the candidates.**

The third is the one worth defending. A guess would attribute somebody's traffic to the wrong
budget; a fallback to an "unattributed" bucket would be a hole in every control at once, and it
would be the path of least resistance for every caller. Naming the candidates makes the fix a
one-line change rather than a support conversation.

**A selector is a header *or* a path prefix, and this surface read only the first.**
``UseCasePathMiddleware`` is mounted before every route and puts ``/uc/<slug>`` into the scope for
both surfaces; ``resolve_use_case`` reads header-then-path and the Gemini routes have always used
it. This module rewrote the header half by hand and never looked at the path, so the prefix worked
on one surface and was invisible on the other. Measured on 2026-08-13 with one token holding two
memberships: ``/uc/kundenservice/v1beta/…`` served, ``/uc/kundenservice/kira/api/external/…``
refused — and refused with *"send the header"*, so the prefix was not rejected, it was never seen.

It is the surface where the prefix matters most. A migrating client is often something whose base
URL is configurable and whose headers are not, which is exactly the case the prefix exists for.
One rule, two surfaces, one of them written out again — the shape this repository keeps finding,
and the reason `resolve_use_case` is now called rather than re-implemented.
"""

from __future__ import annotations

from fastapi import Request

from aira_gateway.api.kira import errors
from aira_gateway.auth.attribution import (
    USE_CASE_HEADER_NAME,
    USE_CASE_PATH_FORM,
    Attribution,
    is_valid_use_case,
    resolve_use_case,
)
from aira_gateway.auth.dependencies import must_name_a_use_case, use_case_refusal
from aira_gateway.auth.principal import Principal

#: How a caller names a use case, in the words the refusals use. Written once because it appears in
#: two messages, and a caller who cannot set a header must not be told twice to set one.
_HOW_TO_SELECT = f"the '{USE_CASE_HEADER_NAME}' header, or the '{USE_CASE_PATH_FORM}' path prefix"


def resolve(request: Request, principal: Principal) -> Attribution:
    # Header **or** path, from the function both surfaces share, which is what makes the two agree
    # rather than merely resemble each other. It also settles precedence in one place: the header
    # wins where both are present.
    selector = (resolve_use_case(request) or "").strip()
    memberships = principal.use_cases

    if selector:
        if not is_valid_use_case(selector):
            raise errors.KiraError(400, errors.VALIDATION_ERROR, "Invalid use case identifier.")
        # The same rule the Gemini surface applies, from the same function — because this used to
        # be a second copy of it and the copy was wrong. `if memberships and …` made an *empty*
        # membership list mean "anything goes": a caller belonging to no use case could name
        # somebody else's and have the tokens billed to it. A selector never *grants* access.
        refusal = use_case_refusal(principal, selector)
        if refusal is not None:
            raise errors.KiraError(403, errors.STANDARD_USER_PERMISSION_REQUIRED, refusal)
        selected: str | None = selector
    elif len(memberships) == 1:
        selected = memberships[0]
    elif not memberships:
        selected = None
    else:
        raise errors.KiraError(
            403,
            errors.STANDARD_USER_PERMISSION_REQUIRED,
            "Your identity belongs to several use cases, so this request cannot be attributed to "
            f"one. Name one of {sorted(memberships)} with {_HOW_TO_SELECT}.",
        )

    if selected is None and must_name_a_use_case(request, principal):
        raise errors.KiraError(
            403,
            errors.STANDARD_USER_PERMISSION_REQUIRED,
            "This request cannot be attributed to a use case, and an unattributed request would "
            f"bypass every budget and limit. Name one with {_HOW_TO_SELECT}.",
        )

    attribution = Attribution(
        subject=principal.subject,
        method=principal.method,
        username=principal.username,
        use_case=selected,
        credential=principal.credential,
        issuer=principal.issuer,
    )
    request.state.attribution = attribution
    return attribution
