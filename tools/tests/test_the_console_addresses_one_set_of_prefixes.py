"""Which URL prefixes belong to AIRA is one fact, and it was stated in four places.

Every call the console makes goes to `/api/…` (management) or `/gw/…` (gateway). That routing was
written out four times, by four mechanisms that cannot see each other:

- fifty-odd call sites in `use-case.service.ts` and friends;
- `AIRA_PREFIXES` in `auth.interceptor.ts`, which decides where the bearer token is attached;
- `location` blocks in the nginx template that serves the built console;
- `proxy.conf.json`, which `ng serve` uses in development — JSON, so it could not ask anything.

A fifth prefix added to the services and forgotten in the interceptor sends the request **without a
token**. The `401` that comes back is then handled by the interceptor's own error branch, which
logs the user out — a valid session ended over a list nobody remembered to extend, with an error
message about credentials. Forgotten in nginx instead, the call 404s against the SPA's index.html
and arrives at the caller as unparseable HTML.

`prefixes.json` is now the single statement, read by the TypeScript module and by the CommonJS dev
proxy. nginx cannot read JSON, so it is compared here — the answer this repository has arrived at
six times now for two statements that must agree: compare them, in both directions, and fail on
the first divergence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "management" / "frontend"
PREFIXES = CONSOLE / "src" / "app" / "core" / "api" / "prefixes.json"
NGINX = CONSOLE / "deploy" / "default.conf.template"
SERVICES = CONSOLE / "src" / "app"


def _declared() -> dict[str, str]:
    return json.loads(PREFIXES.read_text())


def test_the_prefixes_file_says_something() -> None:
    """A guard on the guard: an empty mapping would satisfy every comparison below."""
    declared = _declared()

    assert declared, "no prefixes declared at all"
    assert set(declared) == {"management", "gateway"}, declared


def test_nginx_routes_exactly_the_prefixes_the_console_declares() -> None:
    """Both directions. A `location` nginx serves and the console never calls is dead routing that
    reads as supported; a prefix the console calls and nginx does not route 404s into the SPA."""
    routed = set(re.findall(r"^\s*location\s+(/[a-z]+)/\s*\{", NGINX.read_text(), re.M))
    declared = set(_declared().values())

    assert routed == declared, (
        "The nginx template and `prefixes.json` disagree about which paths belong to AIRA.\n"
        f"  routed by nginx, not declared: {sorted(routed - declared)}\n"
        f"  declared, not routed by nginx: {sorted(declared - routed)}\n"
        "A declared-but-unrouted prefix 404s into index.html and reaches the caller as HTML."
    )


def test_no_service_writes_a_prefix_by_hand() -> None:
    """The fifty call sites. Each one that spells `/api/` itself is a place the prefix has to be
    found again when it changes — which is the state this file was written out of."""
    offenders: list[str] = []
    declared = set(_declared().values())
    for path in sorted(SERVICES.rglob("*.ts")):
        if path.name.endswith(".spec.ts") or path.name == "prefixes.ts":
            # Specs keep the literals on purpose: they pin the wire contract, so a change to
            # `prefixes.json` fails them loudly instead of sliding through.
            continue
        source = path.read_text()
        for number, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("*", "//", "/*")):
                continue
            for prefix in declared:
                if re.search(rf"""['"`]{re.escape(prefix)}/""", line):
                    offenders.append(f"{path.relative_to(CONSOLE)}:{number}  {stripped[:80]}")

    assert not offenders, (
        "These spell a first-party prefix by hand instead of composing it from `API`/`GW` in "
        "`core/api/prefixes.ts`:\n  " + "\n  ".join(offenders)
    )


def test_the_dev_proxy_reads_the_same_file() -> None:
    """`ng serve` and the built image must route the same paths, or a bug reproduces in one and
    not the other — the most expensive kind of difference a development setup can have."""
    proxy = (CONSOLE / "proxy.conf.cjs").read_text()

    assert "prefixes.cjs" in proxy, "the dev proxy states its own prefixes again"
    assert not re.search(r"""['"]/(?:api|gw)""", proxy), proxy
    assert not (CONSOLE / "proxy.conf.json").exists(), (
        "the JSON proxy config is back; it cannot read `prefixes.json` and cannot follow a port"
    )


def test_the_typescript_module_derives_from_the_json_rather_than_restating_it() -> None:
    """The one file that could quietly become a fifth statement.

    `prefixes.ts` is what the application reads and `prefixes.json` is what the dev proxy reads.
    If the TypeScript stops deriving from the JSON — `export const API = '/api'` is a one-line
    edit — the two go on agreeing until somebody changes the JSON, at which point `ng serve`
    proxies one prefix and the app calls another, and the app works in the built image and not in
    development. Found by breaking exactly that and watching the other checks here pass.
    """
    source = (SERVICES / "core" / "api" / "prefixes.ts").read_text()

    assert "from './prefixes.json'" in source, "the module no longer reads the single statement"
    literals = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"""=\s*['"`]/""", line) and not line.strip().startswith(("*", "//"))
    ]
    assert not literals, (
        "these assign a path literal instead of deriving it from `prefixes.json`: " + str(literals)
    )
