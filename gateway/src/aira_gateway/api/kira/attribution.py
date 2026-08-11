"""Attribution for a surface that has no notion of a use case (FRD-107 §5.3).

Every AIRA control is scoped to a use case. The predecessor has no such concept: a caller is a user
in a group, and that is all. So a KIRA request arrives with no selector — and without one it cannot
be budgeted, limited, priced or reported.

The resolution, in order:

1. **Exactly one use case in the caller's memberships** → that one. `FRD-102` already derives them
   from ``/use-cases/<slug>`` groups, so for the common case — an application belonging to one use
   case — attribution is automatic and the migrating client changes nothing.
2. **An explicit ``X-AIRA-Use-Case`` header** → that one, if they are a member. Authorised by
   ``use_case_refusal``, which both surfaces share: a selector chooses among what a caller already
   has and never adds to it.
3. **Several memberships and no header** → **403 naming the candidates.**

The third is the one worth defending. A guess would attribute somebody's traffic to the wrong
budget; a fallback to an "unattributed" bucket would be a hole in every control at once, and it
would be the path of least resistance for every caller. Naming the candidates makes the fix a
one-line header change rather than a support conversation.
"""

from __future__ import annotations

from fastapi import Request

from aira_gateway.api.kira import errors
from aira_gateway.auth.attribution import USE_CASE_HEADER, Attribution, is_valid_use_case
from aira_gateway.auth.dependencies import must_name_a_use_case, use_case_refusal
from aira_gateway.auth.principal import Principal


def resolve(request: Request, principal: Principal) -> Attribution:
    header = (request.headers.get(USE_CASE_HEADER) or "").strip()
    memberships = principal.use_cases

    if header:
        if not is_valid_use_case(header):
            raise errors.KiraError(400, errors.VALIDATION_ERROR, "Invalid use case identifier.")
        # The same rule the Gemini surface applies, from the same function — because this used to
        # be a second copy of it and the copy was wrong. `if memberships and …` made an *empty*
        # membership list mean "anything goes": a caller belonging to no use case could name
        # somebody else's and have the tokens billed to it. A selector never *grants* access.
        refusal = use_case_refusal(principal, header)
        if refusal is not None:
            raise errors.KiraError(403, errors.STANDARD_USER_PERMISSION_REQUIRED, refusal)
        selected: str | None = header
    elif len(memberships) == 1:
        selected = memberships[0]
    elif not memberships:
        selected = None
    else:
        raise errors.KiraError(
            403,
            errors.STANDARD_USER_PERMISSION_REQUIRED,
            "Your identity belongs to several use cases, so this request cannot be attributed to "
            f"one. Send the '{USE_CASE_HEADER}' header naming one of: {sorted(memberships)}.",
        )

    if selected is None and must_name_a_use_case(request, principal):
        raise errors.KiraError(
            403,
            errors.STANDARD_USER_PERMISSION_REQUIRED,
            "This request cannot be attributed to a use case, and an unattributed request would "
            f"bypass every budget and limit. Send the '{USE_CASE_HEADER}' header.",
        )

    attribution = Attribution(
        subject=principal.subject,
        method=principal.method,
        use_case=selected,
        credential=principal.credential,
    )
    request.state.attribution = attribution
    return attribution
