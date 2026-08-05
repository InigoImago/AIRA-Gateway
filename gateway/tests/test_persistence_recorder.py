from types import SimpleNamespace

from starlette.requests import Request

from aira_gateway.config import GatewaySettings
from aira_gateway.persistence.recorder import client_ip


def _request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = None,
    *,
    trust_forwarded_for: bool = False,
) -> Request:
    settings = GatewaySettings(trust_forwarded_for=trust_forwarded_for)
    scope: dict = {
        "type": "http",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


def test_client_ip_uses_forwarded_for_when_trusted() -> None:
    request = _request({"x-forwarded-for": "9.9.9.9, 8.8.8.8"}, trust_forwarded_for=True)
    assert client_ip(request) == "9.9.9.9"


def test_client_ip_ignores_forwarded_for_by_default() -> None:
    """An untrusted client must not be able to forge its own audit-log entry."""
    request = _request({"x-forwarded-for": "9.9.9.9"}, client=("5.6.7.8", 1))
    assert client_ip(request) == "5.6.7.8"


def test_client_ip_truncates_oversized_forwarded_for() -> None:
    request = _request({"x-forwarded-for": "9" * 200}, trust_forwarded_for=True)
    assert client_ip(request) == "9" * 64


def test_client_ip_falls_back_to_peer() -> None:
    assert client_ip(_request(client=("5.6.7.8", 12345))) == "5.6.7.8"


def test_client_ip_none_when_unknown() -> None:
    assert client_ip(_request()) is None


def test_client_ip_blank_forwarded_falls_back() -> None:
    request = _request({"x-forwarded-for": "  "}, client=("1.1.1.1", 1), trust_forwarded_for=True)
    assert client_ip(request) == "1.1.1.1"
