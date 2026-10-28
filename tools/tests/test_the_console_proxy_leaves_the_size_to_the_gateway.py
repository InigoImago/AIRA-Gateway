"""How large a request may be is the gateway's to decide, never the console's proxy's.

Every client the console sets up is pointed at `/gw` — the examples on a use case's Overview and
the `opencode.json` it downloads — where the console's nginx forwards to the gateway. nginx refuses
a body over 1 MiB unless told otherwise, and a coding assistant resends its whole conversation with
every step, so a session of about 300 000 tokens ended in nginx's own `413`: an HTML page, sent
before the gateway had seen the request, which therefore recorded nothing — while the gateway's own
limit, `AIRA_MAX_REQUEST_BYTES`, was never the one that applied.

The live suite sends such a body through the real image (`tests/integration/test_stack_smoke.py`);
this holds the two directives that make it pass, where the template is edited.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "management" / "frontend"
NGINX = CONSOLE / "deploy" / "default.conf.template"
PREFIXES = CONSOLE / "src" / "app" / "core" / "api"
DOWNLOAD = CONSOLE / "src" / "app" / "features" / "use-cases" / "api-keys-panel.ts"


def _gateway_location() -> str:
    """The directives of the location that forwards to the gateway, without their comments."""
    prefix = json.loads((PREFIXES / "prefixes.json").read_text(encoding="utf-8"))["gateway"]
    text = NGINX.read_text(encoding="utf-8")
    found = re.search(rf"^\s*location\s+{re.escape(prefix)}/\s*\{{(.*?)^\s*\}}", text, re.M | re.S)
    assert found, f"the template forwards nothing under {prefix}/"
    lines = found.group(1).splitlines()
    return "\n".join(line for line in lines if not line.strip().startswith("#"))


def _directive(body: str, name: str) -> str | None:
    """The value a directive is given, or None where it is not given and nginx's default holds."""
    values = re.findall(rf"^\s*{name}\s+([^;]+);", body, re.M)
    return values[-1].strip() if values else None


def test_every_client_the_console_sets_up_is_pointed_at_its_proxy() -> None:
    """A guard on the guard: a client pointed past the console would not meet its limit at all."""
    assert "`${window.location.origin}${GW}`" in (PREFIXES / "prefixes.ts").read_text(
        encoding="utf-8"
    )
    assert "baseURL: `${gatewayBase()}/uc/" in DOWNLOAD.read_text(encoding="utf-8")


def test_the_proxy_sets_no_limit_of_its_own() -> None:
    """`0` is nginx's word for "not checked"; left out, the limit is 1 MiB."""
    assert _directive(_gateway_location(), "client_max_body_size") == "0"


def test_the_proxy_does_not_hold_a_request_back() -> None:
    """Buffered, a request with no limit would be spooled to the container's disk whole, whatever
    its size, before the gateway could refuse it; streamed through, the gateway refuses it as it
    arrives."""
    assert _directive(_gateway_location(), "proxy_request_buffering") == "off"
