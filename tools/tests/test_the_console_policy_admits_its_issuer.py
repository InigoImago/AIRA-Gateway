"""The console's two content policies, and which of them may know where Keycloak is.

A browser enforces **every** policy a document carries: the `<meta>` tag compiled into
`index.html` and the header nginx adds (`deploy/aira-headers.inc.template`). A request has to pass
both.

The issuer is deployment configuration — `runtime-config.js`, written from `AIRA_OIDC_ISSUER` at
container start — so only the header can name it: the entrypoint derives `AIRA_CSP_CONNECT_SRC`
from the issuer and nginx renders it. The meta tag carried the issuer too, as a placeholder the
container substituted in the built `index.html`. Under `ng serve` nothing substitutes it, the
browser drops the unknown source, and the documented development mode could not reach its Keycloak
on `localhost:8080`: "The console cannot sign you in", for a provider that was up.

So the meta tag states only what is true of the bundle wherever it is served, and leaves
connections to the header. The second half of this file is the renewal: the console renews a
session with the refresh token, so nothing is framed, and both policies say so.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "management" / "frontend"
INDEX = CONSOLE / "src" / "index.html"
HEADERS = CONSOLE / "deploy" / "aira-headers.inc.template"
AUTH_CONFIG = CONSOLE / "src" / "app" / "core" / "auth" / "auth.config.ts"
ENTRYPOINT = CONSOLE / "deploy" / "10-runtime-config.envsh"

#: The page's own origin, for `'self'`. Any value would do; the policies never name it.
CONSOLE_ORIGIN = "https://console.example"

#: Issuers the client library accepts with `requireHttps: 'remoteOnly'`: any HTTPS host, and plain
#: HTTP on the loopback only — the development realm among them.
ACCEPTED_ISSUERS = (
    "http://localhost:8080/realms/aira/.well-known/openid-configuration",
    "http://127.0.0.1:8080/realms/aira/protocol/openid-connect/token",
    "https://sso.example.org/realms/aira/protocol/openid-connect/token",
)


def _meta_policy() -> dict[str, list[str]]:
    html = INDEX.read_text(encoding="utf-8")
    tag = re.search(r"<meta\s[^>]*http-equiv=\"Content-Security-Policy\"[^>]*>", html, re.S)
    assert tag, "index.html carries no content policy at all"
    content = re.search(r'content="([^"]*)"', tag.group(0), re.S)
    assert content, tag.group(0)
    return _directives(content.group(1))


def _header_policy() -> dict[str, list[str]]:
    text = HEADERS.read_text(encoding="utf-8")
    header = re.search(r'add_header Content-Security-Policy "([^"]*)" always;', text)
    assert header, "the nginx template sends no content policy"
    return _directives(header.group(1))


def _directives(policy: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0].lower()] = tokens[1:]
    return directives


def _sources(policy: dict[str, list[str]], directive: str) -> list[str]:
    """A directive's sources, falling back to `default-src` as a browser does."""
    return policy.get(directive, policy.get("default-src", []))


#: A host source: an optional scheme, a host (optionally `*.`-prefixed), an optional port or `*`.
HOST_SOURCE = re.compile(
    r"(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?(?P<host>(?:\*\.)?[^:/\s]+)(?::(?P<port>\d+|\*))?(/\S*)?"
)
DEFAULT_PORT = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def _admits(sources: list[str], url: str) -> bool:
    """Whether a CSP source list lets the page at `CONSOLE_ORIGIN` fetch `url` (CSP Level 3)."""
    target = urlsplit(url)
    target_port = target.port or DEFAULT_PORT.get(target.scheme)
    for source in sources:
        if source == "*":
            if target.scheme in DEFAULT_PORT:
                return True
            continue
        if source == "'self'":
            if f"{target.scheme}://{target.netloc}" == CONSOLE_ORIGIN:
                return True
            continue
        if re.fullmatch(r"[a-z][a-z0-9+.-]*:", source):
            if target.scheme == source[:-1]:
                return True
            continue
        parsed = HOST_SOURCE.fullmatch(source)
        if source.startswith("'") or not parsed:
            continue
        scheme = parsed["scheme"] or urlsplit(CONSOLE_ORIGIN).scheme
        host = parsed["host"]
        host_matches = (
            target.hostname is not None and target.hostname.endswith(host[1:])
            if host.startswith("*.")
            else host == target.hostname
        )
        port = parsed["port"]
        port_matches = (
            port == "*" or (int(port) if port else DEFAULT_PORT.get(scheme)) == target_port
        )
        if scheme == target.scheme and host_matches and port_matches:
            return True
    return False


def test_the_meta_policy_names_nothing_a_deployment_decides() -> None:
    """No placeholder for somebody else to fill in: a built file that needs a substitution before
    it works is a file that does not work wherever the substitution does not happen."""
    html = INDEX.read_text(encoding="utf-8")
    script = "\n".join(
        line
        for line in ENTRYPOINT.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )

    assert "AIRA_ISSUER_ORIGIN" not in html
    assert "index.html" not in script, "the entrypoint edits the built index.html again"


def test_the_meta_policy_lets_the_console_reach_any_issuer_it_accepts() -> None:
    connect = _sources(_meta_policy(), "connect-src")

    refused = [url for url in ACCEPTED_ISSUERS if not _admits(connect, url)]
    assert refused == [], (
        f"the policy compiled into index.html refuses {refused}. Under `ng serve` it is the only "
        "policy there is, and the console cannot reach its identity provider."
    )


def test_the_header_names_the_issuer_and_nothing_else() -> None:
    """The header is where connections are narrowed: this origin and the issuer's, derived from
    `AIRA_OIDC_ISSUER` at start, so one variable moves the login and the policy together."""
    assert _header_policy()["connect-src"] == ["${AIRA_CSP_CONNECT_SRC}"]
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    assert "AIRA_CSP_CONNECT_SRC=\"${AIRA_CSP_CONNECT_SRC:-'self' ${origin}}\"" in entrypoint


def test_the_meta_policy_keeps_what_is_true_of_the_bundle_everywhere() -> None:
    """Loosening `connect-src` must not loosen the rest: scripts stay same-origin wherever the
    bundle is served, which is what stops an injected payload from running."""
    meta = _meta_policy()

    assert meta["script-src"] == ["'self'"]
    assert meta["object-src"] == ["'none'"]
    assert meta["base-uri"] == ["'self'"]


def test_nothing_is_framed() -> None:
    """The session is renewed with the refresh token (`auth.config.ts`, `useSilentRefresh: false`).
    The hidden-iframe renewal would have had to frame the issuer, and nothing else is framed."""
    for policy in (_meta_policy(), _header_policy()):
        assert _sources(policy, "frame-src") == ["'none'"], policy


def test_the_session_is_renewed_without_a_frame() -> None:
    """`useSilentRefresh` picks the renewal for the code flow, and `true` sends every renewal
    through a hidden iframe that has to load the issuer — refused by `frame-src 'none'`, so the
    token simply expires and the next call sends the reader through a full login, unsaved work and
    all. The behaviour is asserted against the real library in `auth.service.spec.ts`; this is the
    shape, so that `tools/mutation_check.py`, which runs pytest only, can reach it."""
    code = "\n".join(
        line
        for line in AUTH_CONFIG.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith(("//", "*", "/*"))
    )

    assert re.search(r"^\s*useSilentRefresh: false,$", code, re.M), (
        "the console no longer renews with the refresh token"
    )
    assert "silentRefreshRedirectUri" not in code
