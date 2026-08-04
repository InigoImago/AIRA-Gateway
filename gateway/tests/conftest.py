from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings


@pytest.fixture
def settings() -> GatewaySettings:
    # API-surface tests run with auth disabled; auth itself is tested via `authed_client`.
    return GatewaySettings(log_json=True, auth_required=False)


@pytest.fixture
def client(settings: GatewaySettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def authed_client() -> Iterator[TestClient]:
    # Auth required; demo mode seeds the deterministic demo API key on startup.
    settings = GatewaySettings(log_json=True, auth_required=True, demo_mode=True)
    with TestClient(create_app(settings)) as test_client:
        yield test_client
