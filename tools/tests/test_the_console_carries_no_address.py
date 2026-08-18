"""The console must not know where anything is.

Reported plainly: *"the URLs and ports are plain text in the frontend, so changing one means going
through the whole frontend — we are building enterprise software here."* Three literals were
found, in three different mechanisms, and each would have survived a search for the other two:

- `auth.config.ts` fell back to `http://localhost:8080/realms/aira`, so a deployment whose runtime
  configuration failed to load sent every user to a login page on their own machine;
- `index.html` carried a **second** CSP as a `<meta>` tag, compiled into the bundle, naming that
  same origin — the nginx header could follow the issuer all it liked, the meta tag still refused
  the connection;
- the nginx header itself was a separate variable that, in the compose file's words, "has to agree
  with" the first.

One variable decides all three now (`AIRA_OIDC_ISSUER`), and this test is what keeps the fourth
from appearing. It reads the **shipped** sources only: a dev-server proxy and a Dockerfile default
are configuration of the build, not knowledge inside the product.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "management/frontend/src"

#: A scheme-and-host literal, or a bare `:port`. Deliberately broad: the point is that the console
#: names no address at all, whatever form it takes.
ADDRESS = re.compile(r"https?://(?!schema\.org|www\.w3\.org)[a-zA-Z0-9._-]+(:\d+)?|:\d{4,5}\b")

#: Literals that are not addresses this console reaches.
ALLOWED = (
    "opencode.ai/config.json",  # a JSON Schema id in a config file generated *for the user*
    "AIRA_ISSUER_ORIGIN",  # the placeholder the entrypoint substitutes
)


def _shipped_files() -> list[Path]:
    return [
        path
        for path in SHIPPED.rglob("*")
        if path.suffix in {".ts", ".html"} and not path.name.endswith(".spec.ts")
    ]


def test_there_are_shipped_files_to_check() -> None:
    """A guard on the guard: a wrong path passes every assertion below by checking nothing."""
    assert len(_shipped_files()) > 50, len(_shipped_files())


def test_no_shipped_source_names_a_host_or_a_port() -> None:
    offenders: list[str] = []
    for path in _shipped_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("*", "//", "<!--", "#")) or any(a in line for a in ALLOWED):
                continue
            found = ADDRESS.search(line)
            if found:
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {found.group(0)}")

    assert not offenders, (
        "The console names an address:\n  " + "\n  ".join(offenders) + "\n\n"
        "Everything it needs to reach is either same-origin (proxied by nginx) or comes from "
        "`runtime-config.js`, written at container start from AIRA_OIDC_ISSUER. A literal here is "
        "a deployment that cannot be moved without a rebuild."
    )
