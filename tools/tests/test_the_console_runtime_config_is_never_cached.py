"""`runtime-config.js` is the one script the console serves that changes without being renamed.

Every other script, stylesheet and font the console serves is content-hashed by the build, so the
nginx template caches them for a year as `immutable` — a changed file is a new name. The runtime
configuration is the exception: `deploy/10-runtime-config.envsh` rewrites it at every container
start from `AIRA_OIDC_ISSUER` and `AIRA_OIDC_CLIENT_ID`, under the same name, and that rewrite is
the whole point of the file ("move Keycloak without rebuilding").

Matched by the hashed-bundle rule, it was served `Cache-Control: public, immutable` with a year's
expiry. A browser that had opened the console once kept the old issuer for up to a year after an
operator moved Keycloak, and signed people in at the old address until somebody cleared a cache.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "management" / "frontend"
NGINX = CONSOLE / "deploy" / "default.conf.template"
ENTRYPOINT = CONSOLE / "deploy" / "10-runtime-config.envsh"
INDEX = CONSOLE / "src" / "index.html"

#: The file's name as the document loads it and as the entrypoint writes it.
NAME = "runtime-config.js"


def _locations() -> dict[str, str]:
    """Each `location` block of the template, by its matcher, mapped to its body.

    The template's locations do not nest, so a body ends at the first line holding only `}`.
    """
    text = NGINX.read_text(encoding="utf-8")
    return {
        match.group(1).strip(): match.group(2)
        for match in re.finditer(r"^\s*location\s+([^{]+?)\s*\{(.*?)^\s*\}", text, re.M | re.S)
    }


def _code(body: str) -> str:
    """A block without its comments: the reasons written beside a directive are not directives."""
    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))


def test_the_document_loads_the_file_the_entrypoint_writes() -> None:
    """A guard on the guard: a renamed file would make every assertion below about nothing."""
    assert f'src="{NAME}"' in INDEX.read_text(encoding="utf-8")
    assert f"/usr/share/nginx/html/{NAME}" in ENTRYPOINT.read_text(encoding="utf-8")
    assert "root  /usr/share/nginx/html;" in NGINX.read_text(encoding="utf-8")


def test_the_hard_cache_would_match_it() -> None:
    """Why an exact location is needed at all: the rule for hashed files matches this name too, and
    nginx takes an exact match over any regular expression, whatever their order."""
    hashed = next(matcher for matcher in _locations() if matcher.startswith("~*"))
    pattern = hashed.removeprefix("~*").strip()

    assert re.search(pattern, f"/{NAME}", re.I), hashed
    assert "immutable" in _code(_locations()[hashed])


def test_the_runtime_configuration_is_never_cached() -> None:
    exact = _locations().get(f"= /{NAME}")

    assert exact is not None, (
        f"`/{NAME}` is served by the rule for content-hashed files, cached for a year as immutable "
        "— while the entrypoint rewrites it at every start. A browser keeps the old issuer."
    )
    code = _code(exact)
    assert re.search(r'add_header\s+Cache-Control\s+"no-store"\s+always;', code), code
    assert "expires" not in code and "immutable" not in code, code
