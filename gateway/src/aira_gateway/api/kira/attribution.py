"""Attribution for a surface that has no notion of a use case (`FRD-107` §5.3).

Every AIRA control is scoped to a use case, and the KIRA contract has no field for one. Resolution,
in order:

1. **An explicit selector** — the header or the `/uc/<slug>` path prefix, read by the shared
   `resolve_use_case` (the header wins) — if `use_case_refusal` allows it. A selector chooses among
   what a caller already has and never adds to it.
2. **Exactly one membership** → that one, so a migrating client changes nothing.
3. **Several memberships** → 403 naming the candidates. A guess would bill the wrong budget, and an
   "unattributed" fallback would be a hole in every control at once.
4. **No membership** → unattributed, unless `must_name_a_use_case` requires one.

The path prefix matters most here: a migrating client often has a configurable base URL and fixed
headers.
"""

from __future__ import annotations

from fastapi import Request

from aira_gateway.api.kira import errors
from aira_gateway.auth.attribution import (
    UNATTRIBUTED,
    USE_CASE_HEADER_NAME,
    USE_CASE_PATH_FORM,
    Attribution,
    attribute,
    is_valid_use_case,
    mark_refused,
    resolve_use_case,
)
from aira_gateway.auth.dependencies import must_name_a_use_case, use_case_refusal
from aira_gateway.auth.principal import Principal

#: How a caller names a use case, in the words both refusals use.
_HOW_TO_SELECT = f"the '{USE_CASE_HEADER_NAME}' header, or the '{USE_CASE_PATH_FORM}' path prefix"


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
    memberships = principal.use_cases

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
    elif len(memberships) == 1:
        selected = memberships[0]
    elif not memberships:
        selected = None
    else:
        _mark_unattributed(principal, 403)
        raise errors.KiraError(
            403,
            errors.STANDARD_USER_PERMISSION_REQUIRED,
            "Your identity belongs to several use cases, so this request cannot be attributed to "
            f"one. Name one of {sorted(memberships)} with {_HOW_TO_SELECT}.",
        )

    if selected is None and must_name_a_use_case(request, principal):
        _mark_unattributed(principal, 403)
        raise errors.KiraError(
            403,
            errors.STANDARD_USER_PERMISSION_REQUIRED,
            "This request cannot be attributed to a use case, and an unattributed request would "
            f"bypass every budget and limit. Name one with {_HOW_TO_SELECT}.",
        )

    return attribute(request, Attribution.of(principal, selected))
