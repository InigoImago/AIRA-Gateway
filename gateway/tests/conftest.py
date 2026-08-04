from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings


@pytest.fixture
def settings() -> GatewaySettings:
    return GatewaySettings(log_json=True)


@pytest.fixture
def client(settings: GatewaySettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
