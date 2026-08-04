from starlette.requests import Request

from aira_gateway.persistence.recorder import client_ip


def _request(
    headers: dict[str, str] | None = None, client: tuple[str, int] | None = None
) -> Request:
    scope: dict = {
        "type": "http",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


def test_client_ip_prefers_forwarded_for() -> None:
    assert client_ip(_request({"x-forwarded-for": "9.9.9.9, 8.8.8.8"})) == "9.9.9.9"


def test_client_ip_falls_back_to_peer() -> None:
    assert client_ip(_request(client=("5.6.7.8", 12345))) == "5.6.7.8"


def test_client_ip_none_when_unknown() -> None:
    assert client_ip(_request()) is None


def test_client_ip_blank_forwarded_falls_back() -> None:
    assert client_ip(_request({"x-forwarded-for": "  "}, client=("1.1.1.1", 1))) == "1.1.1.1"
