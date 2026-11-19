"""A caller's text is refused or stored on PostgreSQL — never a server error, never a lost row.

PostgreSQL refuses a NUL in any text, half a surrogate pair in any string and a value wider than
its varchar; SQLite, the hermetic suite's database, stores all three. So each defect pinned here
showed on this layer only:

- a refusal that quoted a NUL from the URL failed its own INSERT and left no audit row, while the
  caller got the refusal it was owed;
- a NUL in a filter of the trace list, the content-read log or the report reached the driver as
  the gateway's `500`, and a request id with one did the same;
- half a surrogate pair in a suspension, or in a model Management was asked to catalogue, was a
  `500` where the body is read;
- a group grant on a deep path wrote an outbox key longer than its column of 255, so granting,
  revoking and retiring all answered `500`.

The hermetic suite now hands every statement's text to psycopg's own dumper, so it sees what
PostgreSQL would refuse; this is the same question asked of PostgreSQL itself.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from . import installation, keycloak
from .conftest import GATEWAY_URL, MANAGEMENT_URL
from .test_access_by_group import _create_use_case, _delete_use_case, _grant, _revoke

pytestmark = pytest.mark.integration

#: Half a surrogate pair, as JSON escapes it. No UTF-8 text can carry it, so the body is written
#: by hand: a JSON encoder that emits UTF-8 refuses to produce it at all.
LONE = "\\ud800"
#: Two group names, nested, whose path is longer than the outbox key's old 255 characters once the
#: use case's slug is in front of it — and short enough for Keycloak, which takes 255 per name.
DEEP_NAME = 125


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_refusal_that_quotes_a_nul_from_the_url_is_still_recorded(fixture) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            "/v1beta/models/mock-1%00:generateContent",
            headers=fixture.headers(),
            json={"contents": [{"parts": [{"text": "hi"}]}]},
        )
    assert response.status_code in (400, 404), response.text

    (row,) = await fixture.wait_for_rows(1)
    assert all("\x00" not in value for value in row.values() if isinstance(value, str)), row


@pytest.mark.parametrize(
    ("path", "parameter", "value"),
    [
        ("/v1beta/traces", "use_case", "\x00"),
        ("/v1beta/traces", "outcome", "served\x00"),
        ("/v1beta/traces", "subject", "a\x00b"),
        ("/v1beta/traces", "request_id", "\x00"),
        ("/v1beta/content-reads", "reader", "\x00"),
        ("/v1beta/reporting", "person", "erika\x00"),
    ],
)
async def test_a_nul_in_a_filter_is_refused_by_name(
    admin_token: str, path: str, parameter: str, value: str
) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(path, params={parameter: value}, headers=_bearer(admin_token))

    assert response.status_code == 400, response.text
    assert f"'{parameter}'" in response.json()["error"]["message"]


@pytest.mark.parametrize("path", ["/v1beta/traces/%00", "/v1beta/traces/a%00b/payload"])
async def test_an_id_with_a_nul_names_no_request(admin_token: str, path: str) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(path, headers=_bearer(admin_token))

    assert response.status_code == 404, response.text


async def test_half_a_surrogate_pair_in_a_suspension_is_refused_where_the_body_is_read(
    security_token: str,
) -> None:
    target = installation.subject("itest-surrogate")
    body = f'{{"target": "subject", "target_value": "{target}{LONE}"}}'
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            "/v1beta/suspensions",
            content=body.encode(),
            headers={**_bearer(security_token), "content-type": "application/json"},
        )

    assert response.status_code == 400, response.text


async def test_half_a_surrogate_pair_in_a_management_body_is_refused_by_name(
    admin_token: str,
) -> None:
    body = f'{{"name": "itest-{uuid.uuid4().hex[:8]}{LONE}", "provider": "mock"}}'
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=30.0) as client:
        response = await client.post(
            "/api/v1/models/",
            content=body.encode(),
            headers={**_bearer(admin_token), "content-type": "application/json"},
        )

    assert response.status_code == 400, response.text
    assert "lone surrogate" in response.json()["error"]["message"]


async def test_a_group_on_a_deep_path_is_granted_revoked_and_its_use_case_retired(
    admin_token: str,
) -> None:
    slug = f"deep-{uuid.uuid4().hex[:8]}"
    outer = f"itest-{uuid.uuid4().hex[:8]}".ljust(DEEP_NAME, "o")
    inner = f"itest-{uuid.uuid4().hex[:8]}".ljust(DEEP_NAME, "i")
    path = f"/{outer}/{inner}"
    assert len(f"{slug}|{path}") > 255

    async with httpx.AsyncClient(timeout=30.0) as client:
        outer_id = await keycloak.create_group(client, outer)
        inner_id = await keycloak.create_group(client, inner, parent=f"/{outer}")
    try:
        await _create_use_case(admin_token, slug)
        granted = await _grant(admin_token, slug, path)
        assert granted.status_code == 201, granted.text
        revoked = await _revoke(admin_token, slug, path)
        assert revoked.status_code in (200, 204), revoked.text
        assert (await _grant(admin_token, slug, path)).status_code == 201
        async with httpx.AsyncClient(timeout=30.0) as client:
            retired = await client.delete(
                f"{MANAGEMENT_URL}/api/v1/use-cases/{slug}/", headers=_bearer(admin_token)
            )
        assert retired.status_code == 204, retired.text
    finally:
        await _delete_use_case(admin_token, slug)
        async with httpx.AsyncClient(timeout=30.0) as client:
            await keycloak.delete_group(client, inner_id)
            await keycloak.delete_group(client, outer_id)
