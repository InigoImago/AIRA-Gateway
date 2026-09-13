"""Use-case selection and request attribution (`FRD-102`).

The client chooses the use case (the header overrides the ``/uc/<slug>`` path); for OIDC it is
authorized against group membership. Which group paths grant which use cases is decided once, for
both planes, in `aira_common.access`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Request

from aira_common.observability import attribute_model_calls_to, set_span_attributes
from aira_gateway.scopes import person as person_key

#: The selector header, lowercase as it is matched.
USE_CASE_HEADER = "x-aira-use-case"
#: The same header as documented, for every message that names it on either surface.
USE_CASE_HEADER_NAME = "X-AIRA-Use-Case"
#: How the path selector is written in refusals on both surfaces.
USE_CASE_PATH_FORM = "/uc/<use-case>"
#: The ASGI scope key `UseCasePathMiddleware` stores the path slug under.
USE_CASE_PATH_KEY = "aira_use_case_path"

#: A client-supplied selector must look like a Management use-case slug, keeping unvalidated input
#: out of the audit log, the read-model lookups and the trace attributes (`ADR-0007`). **`\Z`, not
#: `$`**: `$` also matches before a trailing newline, which would let a newline into a log line.
_SLUG = re.compile(r"^[a-z0-9-]{1,64}\Z")


def is_valid_use_case(slug: str) -> bool:
    """True if ``slug`` is a syntactically valid use-case identifier."""
    return bool(_SLUG.match(slug))


@dataclass(frozen=True, slots=True)
class Attribution:
    """What a request is attributed to: identity + selected use case."""

    subject: str
    method: str
    use_case: str | None
    #: The calling system's credential identity, carried through to the audit row (`FRD-122` FR-5).
    credential: str | None = None
    #: The name this subject is known by, where the credential carries one (`FRD-606`). Not the
    #: identity — `subject` is — but what allowances are counted against (:attr:`person`).
    username: str | None = None
    #: Which Keycloak realm minted the token (`FRD-118`); `None` for an API key or demo mode.
    issuer: str | None = None

    @property
    def person(self) -> str | None:
        """Who allowances are counted against — one human, whichever credential they used."""
        return person_key(self.subject, self.username)


def attribute(request: Request, attribution: Attribution) -> Attribution:
    """Attach ``attribution`` to the request **and to its span** — one act, so no surface can
    half-perform it.

    `request.state` feeds the audit row; the span attributes let a trace be filtered by who, which
    use case and which credential (`FRD-101` §9, `FRD-102` §9).
    `test_every_attribution_reaches_the_span.py` fails on a site that assigns the state directly.
    """
    request.state.attribution = attribution
    identity = {
        "aira.subject": attribution.subject,
        "aira.auth_method": attribution.method,
        "aira.use_case": attribution.use_case,
        "aira.credential": attribution.credential,
    }
    set_span_attributes(identity)
    # And onto the model call this request is about to make (`FRD-619`), so the forwarded
    # model-access record says who caused it.
    attribute_model_calls_to(identity)
    return attribution


def resolve_use_case(request: Request) -> str | None:
    """Resolve the target use case: ``X-AIRA-Use-Case`` header overrides the ``/uc/<slug>`` path."""
    header = request.headers.get(USE_CASE_HEADER)
    if header and header.strip():
        return header.strip()
    path_slug = request.scope.get(USE_CASE_PATH_KEY)
    return path_slug if isinstance(path_slug, str) else None
