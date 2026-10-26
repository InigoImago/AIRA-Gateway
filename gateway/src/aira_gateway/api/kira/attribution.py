"""Attribution for a surface that has no notion of a use case (`FRD-107` §5.3, `FRD-149`).

Every AIRA control is scoped to a use case, and the KIRA contract has no field for one. So a model
call names it exactly as on the Gemini surface — the header or the `/uc/<slug>` path prefix, read
by the shared `resolve_use_case` (the header wins) — and `use_case_refusal` decides whether the
caller may act there. A selector chooses among what a caller already has and never adds to it.

**A model call that names none is refused, whoever makes it**: a person in one use case, a person
in several, a key bound to one (`must_name_a_use_case`). What a request is billed to is in the
request, so the same client means the same thing whether its caller reaches one use case or, from
tomorrow, two. Only the unbound break-glass key and demo mode go unattributed (`ADR-0015`).

The path prefix matters most here: a migrating client often has a configurable base URL and fixed
headers.
"""

from __future__ import annotations

from fastapi import Request

from aira_gateway.api.kira import errors
from aira_gateway.auth.attribution import (
    UNATTRIBUTED,
    Attribution,
    attribute,
    is_valid_use_case,
    mark_refused,
    resolve_use_case,
)
from aira_gateway.auth.dependencies import (
    implied_use_case,
    missing_use_case,
    must_name_a_use_case,
    use_case_refusal,
)
from aira_gateway.auth.principal import Principal


def _mark_unattributed(principal: Principal, status: int) -> None:
    """A known caller whose request no use case could be attributed to, on its span (`FRD-616`):
    it writes no audit row, so the span is what a SIEM receives."""
    mark_refused(
        UNATTRIBUTED,
        status,
        subject=principal.subject,
        method=principal.method,
        credential=principal.credential,
    )


def resolve(request: Request, principal: Principal) -> Attribution:
    """Attribute this request to a use case, or raise the KIRA-shaped refusal."""
    selector = (resolve_use_case(request) or "").strip()

    if selector:
        if not is_valid_use_case(selector):
            _mark_unattributed(principal, 400)
            raise errors.KiraError(400, errors.VALIDATION_ERROR, "Invalid use case identifier.")
        # The rule the Gemini surface applies, from the same function: an empty membership list
        # refuses every selector rather than allowing any.
        refusal = use_case_refusal(principal, selector)
        if refusal is not None:
            raise errors.KiraError(403, errors.STANDARD_USER_PERMISSION_REQUIRED, refusal)
        selected: str | None = selector
    else:
        selected = implied_use_case(request, principal)

    if selected is None and must_name_a_use_case(request, principal):
        # A permission refusal in this vocabulary, which has no code for a missing request field
        # outside the body; the Gemini surface says the same sentence as its own 400.
        _mark_unattributed(principal, 403)
        raise errors.KiraError(
            403, errors.STANDARD_USER_PERMISSION_REQUIRED, missing_use_case(principal)
        )

    return attribute(request, Attribution.of(principal, selected))
