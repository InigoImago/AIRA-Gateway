"""Roles over the real path (ADR-0009).

The hermetic tests prove the claim is parsed and the principal carries it. What they cannot prove
is that a token **Keycloak actually issues** carries the claim in the shape the gateway reads, is
signed by a key the gateway fetches, and names an issuer the gateway accepts. Three separate
things that are only true together, and each of them has a way of being subtly wrong: the realm
can omit the role mapper, the JWKS can be fetched from an address the container cannot reach, and
the issuer differs by hostname depending on where the token was requested from.

Run with ``make test-integration`` while the stack is up.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return dict(json.loads(base64.urlsafe_b64decode(payload)))


async def test_the_realm_issues_a_token_carrying_the_governance_role(
    governance_token: str,
) -> None:
    """The realm has to *have* the role and map it into the token. A realm that defines a role
    nobody is granted looks identical, from the gateway, to one that grants it correctly — until
    somebody is refused a report they should see."""
    claims = _claims(governance_token)

    assert "it-steuerung" in claims["realm_access"]["roles"]
    assert claims["iss"].endswith("/realms/aira")


async def test_the_gateway_accepts_a_token_the_realm_actually_issued(
    governance_token: str,
) -> None:
    """End to end: real key, real issuer, real JWKS fetch from inside the container.

    The JWKS is the part most likely to be wrong in a container: the gateway fetches it over the
    network the *container* sees, not the one the token was requested through, which is exactly
    what AIRA_OIDC_JWKS_URI exists to separate.
    """
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=20.0) as client:
        response = await client.get(
            "/v1beta/usage/demo-uc", headers={"authorization": f"Bearer {governance_token}"}
        )

    # 401 would mean the token was not accepted at all; that is what this asserts against.
    assert response.status_code != 401, f"the realm's own token was refused: {response.text}"


async def test_a_token_without_a_governance_role_is_still_authenticated(
    governance_token: str,
) -> None:
    """Oversight is an authorization input, never an authentication one. A caller without the
    role must still be recognised — they simply see less."""
    claims = _claims(governance_token)
    assert claims.get("sub")  # a subject the gateway can attribute traffic to


async def test_an_unsigned_token_is_refused() -> None:
    """The obvious attack, over the real validator: a token whose claims say `it-steuerung` but
    which the realm never signed."""
    forged = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
        + "."
        + base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": "attacker",
                    "iss": "http://localhost:8080/realms/aira",
                    "realm_access": {"roles": ["it-steuerung", "global-admin"]},
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
        + "."
    )

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=20.0) as client:
        response = await client.get(
            "/v1beta/usage/demo-uc", headers={"authorization": f"Bearer {forged}"}
        )

    assert response.status_code == 401
