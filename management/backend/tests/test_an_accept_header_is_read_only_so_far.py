"""A request's `Accept` header is negotiated before it is authenticated, and only so much of it.

DRF picks a renderer for every request, a refusal included, before any view runs. Until 3.18.2 it
read every token of an `Accept` header of any length, several times per request, through Django's
`parse_header_parameters` — which takes quadratic time over a quoted value packed with semicolons
(GHSA-33wh-fxxf-88vv). So whoever could reach this plane could make it work hard for a `401`.

Asserted on what is read, never on a clock: a token too long to be a media type is skipped, the
list is cut short, and the media types a client actually sends still decide the answer.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory
from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

#: The shape the advisory names — a quoted parameter packed with semicolons — at a length the
#: header as a whole may still carry, so what is tested is the bound on one token.
PACKED = 'a/b;p="' + ";" * 4000 + '"'
#: Far more tokens than any client sends.
MANY = 200


def _accepted(header: str) -> list[str]:
    negotiator = api_settings.DEFAULT_CONTENT_NEGOTIATION_CLASS()
    request = Request(RequestFactory().get("/", HTTP_ACCEPT=header))
    tokens: list[str] = negotiator.get_accept_list(request)
    return tokens


def test_a_token_too_long_to_be_a_media_type_is_never_parsed() -> None:
    accepted = _accepted(f"application/json,{PACKED},text/html")

    assert PACKED not in accepted, "a 4 KB token reaches the media-type parser"
    # Skipped, not stopped at: the type after it still counts.
    assert accepted == ["application/json", "text/html"]


def test_the_list_of_media_types_is_cut_short() -> None:
    accepted = _accepted(",".join(["text/plain"] * MANY))

    assert 0 < len(accepted) < MANY, f"all {MANY} tokens are parsed, each several times"


@pytest.mark.django_db
def test_a_refusal_still_answers_in_json_through_such_a_header() -> None:
    """The bound must not cost the answer a client reads: the refusal is still the envelope."""
    response = APIClient().get(
        "/api/v1/use-cases/", HTTP_ACCEPT=",".join(["application/json", *[PACKED] * 25])
    )

    assert response.status_code == 401
    assert response["Content-Type"].startswith("application/json")
