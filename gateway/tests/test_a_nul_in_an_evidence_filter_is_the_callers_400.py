"""A NUL in a filter of the evidence endpoints is the caller's `400`, never the database's `500`.

PostgreSQL refuses a NUL in any text parameter; SQLite, which this suite runs on, stores it without
a word. So a `%00` in a filter of the request list, the content-read log or the report — or in a
request's id — reached the driver and came back as this gateway's `500`, while every hermetic test
stayed green. The suspension and anomaly lists and every cursor already refused one at the
boundary; these did not.

**What makes SQLite see it:** every statement's text parameters pass through **psycopg's own
dumper** — the adaptation step the production driver runs, and the one that raises
`DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`. A filter is refused naming the
parameter; an id that cannot name a request is the same `404` as one that names none.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from psycopg.adapt import PyFormat, Transformer
from sqlalchemy import event

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings

#: Both incident roles, so no filter is skipped for want of authority.
ADMIN = Principal(
    subject="root",
    method="oidc",
    roles=("global-admin", "it-security"),
    use_cases=("demo-uc",),
    username="root",
)

#: A member of one use case and nothing else: the weakest caller who reaches these queries.
MEMBER = Principal(subject="m-1", method="oidc", roles=(), use_cases=("demo-uc",), username="m")

_DUMPERS = Transformer()


def _adapt_like_psycopg(*args: Any) -> None:
    """``before_cursor_execute``: hand every text parameter to psycopg's dumper, as production
    does before a statement reaches PostgreSQL."""
    parameters = args[3]
    values = parameters.values() if isinstance(parameters, dict) else (parameters or ())
    for value in values:
        if isinstance(value, str):
            _DUMPERS.get_dumper(value, PyFormat.AUTO).dump(value)


def _client(principal: Principal) -> Iterator[TestClient]:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: principal
    event.listen(app.state.db_engine.sync_engine, "before_cursor_execute", _adapt_like_psycopg)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize(
    ("principal", "path", "parameter"),
    [
        (ADMIN, "/v1beta/traces?use_case=%00", "use_case"),
        (MEMBER, "/v1beta/traces?outcome=served%00", "outcome"),
        (MEMBER, "/v1beta/traces?request_id=%00", "request_id"),
        (ADMIN, "/v1beta/traces?subject=a%00b", "subject"),
        (ADMIN, "/v1beta/traces?credential=%00", "credential"),
        (ADMIN, "/v1beta/traces?source_ip=10.0.0.1%00", "source_ip"),
        (ADMIN, "/v1beta/content-reads?use_case=%00", "use_case"),
        (ADMIN, "/v1beta/content-reads?reader=%00", "reader"),
        (MEMBER, "/v1beta/reporting?person=%00", "person"),
        (ADMIN, "/v1beta/reporting?person=erika%00", "person"),
    ],
)
def test_a_nul_in_a_filter_is_refused_by_name(
    principal: Principal, path: str, parameter: str
) -> None:
    for client in _client(principal):
        response = client.get(path)

        assert response.status_code == 400, (path, response.status_code, response.text[:300])
        assert f"'{parameter}'" in response.json()["error"]["message"]


@pytest.mark.parametrize("path", ["/v1beta/traces/%00", "/v1beta/traces/a%00b/payload"])
def test_an_id_with_a_nul_names_no_request(path: str) -> None:
    for client in _client(MEMBER):
        response = client.get(path)

        assert response.status_code == 404, (path, response.status_code, response.text[:300])
        assert response.json()["error"]["message"] == "No such request."


def test_the_harness_is_what_postgres_would_say() -> None:
    """The control: the same filter without a NUL is answered, so the refusals above are about
    the NUL and the hook is not what produces them."""
    for client in _client(ADMIN):
        assert client.get("/v1beta/traces?use_case=demo-uc&subject=erika").status_code == 200
        assert client.get("/v1beta/reporting?person=erika").status_code == 200
        assert client.get("/v1beta/traces/no-such-row").status_code == 404
