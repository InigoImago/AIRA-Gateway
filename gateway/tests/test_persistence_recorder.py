from types import SimpleNamespace

from starlette.requests import Request

from aira_gateway.config import GatewaySettings
from aira_gateway.persistence.recorder import client_ip


def _request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = None,
    *,
    trust_forwarded_for: bool = False,
    trusted_proxy_hops: int = 1,
) -> Request:
    settings = GatewaySettings(
        trust_forwarded_for=trust_forwarded_for, trusted_proxy_hops=trusted_proxy_hops
    )
    scope: dict = {
        "type": "http",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


def test_client_ip_uses_forwarded_for_when_trusted() -> None:
    """One proxy in front, so the address it appended is the last entry."""
    request = _request({"x-forwarded-for": "9.9.9.9"}, trust_forwarded_for=True)
    assert client_ip(request) == "9.9.9.9"


def test_a_caller_cannot_choose_the_address_that_is_recorded() -> None:
    """**The header is caller-controlled at its left end**, and this used to read it there.

    A proxy *appends*: the nginx this repository ships uses `$proxy_add_x_forwarded_for`, so a
    client sending `X-Forwarded-For: 10.9.9.9` arrives here as `10.9.9.9, <real address>`. Reading
    the leftmost entry let the caller pick what went into the audit trail, what `FRD-505`'s
    incident view finds when somebody searches for the real address, and — because
    `record_failed_authentication` keys on this same value — which bucket the brute-force bound
    counted against, so rotating the header made that bound unreachable.

    Asserted as the forgery it prevents, not as "the parser picks index -1": a test naming the
    index would pass against any code that happens to end there.
    """
    forged = _request(
        {"x-forwarded-for": "10.9.9.9, 203.0.113.7"},
        client=("172.16.0.1", 1),
        trust_forwarded_for=True,
    )

    assert client_ip(forged) == "203.0.113.7"
    assert client_ip(forged) != "10.9.9.9"


def test_the_address_is_read_one_entry_per_configured_hop() -> None:
    """Two proxies append, so the caller's own address is two from the right. Anything further
    left is still the caller's to write."""
    request = _request(
        {"x-forwarded-for": "10.9.9.9, 198.51.100.4, 203.0.113.7"},
        trust_forwarded_for=True,
        trusted_proxy_hops=2,
    )

    assert client_ip(request) == "198.51.100.4"


def test_a_chain_shorter_than_the_hops_falls_back_to_the_socket() -> None:
    """The request did not come through the proxies that were supposed to append to it, so the
    header describes nothing this deployment can vouch for. An unspoofable address that is merely
    the peer's beats a spoofable one claiming to be the client's."""
    request = _request(
        {"x-forwarded-for": "10.9.9.9"},
        client=("172.16.0.1", 1),
        trust_forwarded_for=True,
        trusted_proxy_hops=2,
    )

    assert client_ip(request) == "172.16.0.1"


def test_an_empty_forwarded_header_falls_back_to_the_socket() -> None:
    request = _request(
        {"x-forwarded-for": " , "}, client=("172.16.0.1", 1), trust_forwarded_for=True
    )
    assert client_ip(request) == "172.16.0.1"


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
