from types import SimpleNamespace

from starlette.requests import Request

from aira_gateway.auth.attribution import Attribution
from aira_gateway.config import GatewaySettings
from aira_gateway.persistence.recorder import client_ip

#: The proxies these requests pass, as an installation behind Docker names them (`FRD-156`).
DOCKER = "172.18.0.0/16"


def _request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = None,
    *,
    trusted_proxies: str = "",
    forwarded_lines: list[str] | None = None,
) -> Request:
    settings = GatewaySettings(trusted_proxies=trusted_proxies)
    raw = [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    raw += [(b"x-forwarded-for", line.encode()) for line in forwarded_lines or []]
    scope: dict = {
        "type": "http",
        "headers": raw,
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


def test_client_ip_uses_forwarded_for_from_a_named_proxy() -> None:
    """The console's nginx, named, appended the address it saw."""
    request = _request(
        {"x-forwarded-for": "9.9.9.9"}, client=("172.18.0.5", 1), trusted_proxies=DOCKER
    )
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
        client=("172.18.0.5", 1),
        trusted_proxies=DOCKER,
    )

    assert client_ip(forged) == "203.0.113.7"
    assert client_ip(forged) != "10.9.9.9"


def test_the_address_is_read_through_every_named_proxy_and_no_further() -> None:
    """The platform's proxy and the console's nginx both named: the workstation is the first
    address from the right that neither is — a count of two, without counting."""
    request = _request(
        {"x-forwarded-for": "10.9.9.9, 198.51.100.4, 172.18.0.1"},
        client=("172.18.0.5", 1),
        trusted_proxies=DOCKER,
    )

    assert client_ip(request) == "198.51.100.4"


def test_a_connection_nobody_named_is_the_caller_whatever_its_header_says() -> None:
    """The request did not come through the proxies the installation vouches for, so the header
    describes nothing this deployment can vouch for. An unspoofable address that is merely the
    peer's beats a spoofable one claiming to be the client's."""
    request = _request(
        {"x-forwarded-for": "10.9.9.9"}, client=("203.0.113.9", 1), trusted_proxies=DOCKER
    )

    assert client_ip(request) == "203.0.113.9"


def test_every_header_line_is_read() -> None:
    """The first line is not the only one: a proxy that adds a line of its own is read too — and
    reading only the first would have handed the caller's own line back as the address."""
    request = _request(
        client=("172.18.0.5", 1),
        trusted_proxies=DOCKER,
        forwarded_lines=["6.6.6.6", "198.51.100.4"],
    )

    assert client_ip(request) == "198.51.100.4"


def test_an_empty_forwarded_header_falls_back_to_the_socket() -> None:
    request = _request({"x-forwarded-for": " , "}, client=("172.18.0.5", 1), trusted_proxies=DOCKER)
    assert client_ip(request) == "172.18.0.5"


def test_client_ip_ignores_forwarded_for_by_default() -> None:
    """An untrusted client must not be able to forge its own audit-log entry."""
    request = _request({"x-forwarded-for": "9.9.9.9"}, client=("5.6.7.8", 1))
    assert client_ip(request) == "5.6.7.8"


def test_client_ip_is_as_long_as_its_column() -> None:
    """`request_logs.source_ip` is 64 characters; an address with a long zone fits it cut."""
    zoned = "fe80::1%" + "x" * 80
    request = _request({"x-forwarded-for": zoned}, client=("172.18.0.5", 1), trusted_proxies=DOCKER)
    assert client_ip(request) == zoned[:64]


def test_client_ip_falls_back_to_peer() -> None:
    assert client_ip(_request(client=("5.6.7.8", 12345))) == "5.6.7.8"


def test_client_ip_none_when_unknown() -> None:
    assert client_ip(_request()) is None


# ---- the wire from the attribution to the row (`FRD-606`) -----------------------------------


async def test_the_name_on_the_attribution_reaches_the_row() -> None:
    """`request_logs.username` is what lets one person be one figure instead of two rows nothing
    joins — an OIDC subject is a directory id, an API key's is a username, and neither recognises
    the other.

    Asserted **through the recorder**, not by inserting a row: the reporting tests build rows
    directly, so the column, the grouping and the display were all covered while the one step that
    fills the column was not. The mutation harness said so — deleting `username=username` from the
    write survived every test in the suite. Two correct halves and no wire, in my own change.
    """
    from aira_gateway.persistence.recorder import record_request

    written: dict[str, object] = {}

    class _Writer:
        async def submit(self, entry: object) -> None:
            written["entry"] = entry

    request = _request(client=("10.0.0.1", 1234))
    # The real dataclass, not a namespace. A stand-in that is *less* complete than the thing it
    # replaces breaks the moment the thing grows a field — and one that is *more* permissive hides
    # a defect, which is the same mistake seen from the other side (`CLAUDE.md` §3).
    request.state.attribution = Attribution(
        subject="kc-uuid-1",
        method="oidc",
        username="erika",
        use_case="uc",
        credential="console",
        issuer="https://keycloak.test/realms/aira",
    )
    request.app.state.log_writer = _Writer()

    await record_request(
        request,
        operation="generateContent",
        model="mock-1",
        status=200,
        usage=None,
        latency_ms=1,
        request_payload=None,
        response_payload=None,
        api="gemini",
    )

    entry = written["entry"]
    assert entry.subject == "kc-uuid-1", "the identity is unchanged"
    assert entry.username == "erika", "the name did not reach the row"


async def test_a_credential_that_names_nobody_writes_no_name() -> None:
    """Null rather than an empty string: "no name was carried" and "the name is blank" are
    different facts, and the grouping falls back to the subject for exactly the first one."""
    from aira_gateway.persistence.recorder import record_request

    written: dict[str, object] = {}

    class _Writer:
        async def submit(self, entry: object) -> None:
            written["entry"] = entry

    request = _request(client=("10.0.0.1", 1234))
    request.state.attribution = Attribution(
        subject="svc-1", method="api_key", username=None, use_case="uc", credential="aira_abc"
    )
    request.app.state.log_writer = _Writer()

    await record_request(
        request,
        operation="generateContent",
        model="mock-1",
        status=200,
        usage=None,
        latency_ms=1,
        request_payload=None,
        response_payload=None,
        api="gemini",
    )

    assert written["entry"].username is None
