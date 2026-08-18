"""Nothing that talks to the local stack may write its address down.

On 2026-08-18 a parallel system on the same machine collided with this stack's ports, and the only
way out was editing the Compose file. The fourteen published ports became
`${AIRA_PUBLISH_…_PORT:-<today's value>}` the same day — and that fixed the *stack* while fixing
nothing that talks to it. The Makefile carried twenty literal addresses, `tools/` five,
`tests/integration/` a dozen, `e2e/` eight, and the Angular dev proxy two more in a JSON file that
cannot ask anything. Move a port and the stack comes up correctly while `make showcase` waits
forever on the old one and `make test-integration` reports "connection refused", which reads as
*nothing is running* rather than *you are knocking on the wrong door*.

This is the same defect the console had the day before — the issuer stated in three mechanisms, so
changing it meant finding all three — and it is the fifth time this repository has met the shape.
The answer is the one that worked there: **one place decides, everybody asks**, and a test that
fails when a second place appears.

`tools/stack_addresses.py` is that place. This file checks two things, in both directions:

1. no file outside the owners writes a published address as a literal;
2. every `AIRA_PUBLISH_…` variable in Compose is one the owner knows about, and vice versa — so a
   fifteenth service cannot be published into a hole.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import stack_addresses

ROOT = Path(__file__).resolve().parents[2]

#: The files allowed to name an address, and why each one is.
OWNERS = {
    # The owner itself, and its Node counterpart, which asks it.
    "tools/stack_addresses.py",
    "tools/stack-addresses.cjs",
    # Compose declares the ports; that is the statement everything else derives from.
    "deploy/compose/docker-compose.yml",
    "deploy/compose/docker-compose.apps.yml",
    "deploy/compose/docker-compose.sandbox.yml",
    "deploy/compose/.env.example",
    # This file quotes the addresses it forbids.
    "tools/tests/test_one_owner_for_the_stack_addresses.py",
    # Prose: the setup guide, the configuration reference and the log all name today's values on
    # purpose, because a reader following instructions needs a number to type. They are checked by
    # `test_documented_addresses_are_todays_defaults` below rather than banned.
}

#: Directories searched. Documentation is handled separately — see the docstring above.
SEARCHED = ("tools", "tests", "e2e", "management/frontend/src", "management/frontend/deploy")

#: Ports that are not this stack's and must stay literal: `:1` is a deliberately-dead address in
#: the Vault test, and `:80`/`:443` are the world's.
NOT_OURS = {"1", "80", "443", "8080"}

ADDRESS = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d+)")


def _tracked(directory: str) -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", directory], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [ROOT / name for name in listed if (ROOT / name).exists()]


def _docstring_lines(path: Path, source: str) -> set[int]:
    """Every line number occupied by a docstring, for Python files. Empty for anything else."""
    if path.suffix != ".py":
        return set()
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a file that does not parse has bigger problems
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = node.body[0] if node.body else None
        if (
            isinstance(doc, ast.Expr)
            and isinstance(doc.value, ast.Constant)
            and isinstance(doc.value.value, str)
            and doc.end_lineno is not None
        ):
            lines |= set(range(doc.lineno, doc.end_lineno + 1))
    return lines


def _published_ports() -> set[str]:
    return {str(stack_addresses.port(service)) for service in stack_addresses.PUBLISHED}


def test_the_search_finds_files_at_all() -> None:
    """A guard on the guard: an empty file list passes every assertion below by checking nothing."""
    files = [f for directory in SEARCHED for f in _tracked(directory)]

    assert len(files) > 100, len(files)


def test_no_file_outside_the_owner_writes_a_published_address() -> None:
    published = _published_ports()
    offenders: list[str] = []
    for directory in SEARCHED:
        for path in _tracked(directory):
            relative = str(path.relative_to(ROOT))
            if relative in OWNERS or path.suffix in {".md", ".json", ".lock"}:
                continue
            source = path.read_text(errors="replace")
            prose = _docstring_lines(path, source)
            for number, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                # Prose may quote an address — that is how the reason gets recorded. Comments are
                # matched by prefix; **docstrings are parsed**, because half of this repository's
                # explanations live in one and a prefix heuristic would either miss their middle
                # lines or, worse, be widened until it excused real code.
                if stripped.startswith(("#", "*", "//", "/*")) or number in prose:
                    continue
                for port in ADDRESS.findall(line):
                    if port in published and port not in NOT_OURS:
                        offenders.append(f"{relative}:{number}  {stripped[:90]}")

    assert not offenders, (
        "These write down an address the stack publishes, so moving that port with "
        "`AIRA_PUBLISH_…_PORT` leaves them pointing at nothing — and the failure they produce "
        "names neither the port nor the variable:\n  " + "\n  ".join(offenders) + "\n\n"
        "Ask `tools/stack_addresses.py` instead (`stack_addresses.url('gateway')`), or "
        "`tools/stack-addresses.cjs` from Node, or `$(GATEWAY_URL)` in the Makefile."
    )


def test_every_published_variable_is_one_the_owner_knows() -> None:
    """The other direction, and the one that fails silently.

    A fifteenth service published with a new `AIRA_PUBLISH_…` variable and not added to `PUBLISHED`
    is not an error anywhere: Compose is happy, the owner simply cannot answer for it, and whoever
    needs its address writes a literal — which is where this started.
    """
    declared = set()
    for name in ("docker-compose.yml", "docker-compose.apps.yml"):
        text = (ROOT / "deploy" / "compose" / name).read_text()
        declared |= set(re.findall(r"\$\{(AIRA_PUBLISH_[A-Z_]+)", text))
    known = set(stack_addresses.PUBLISHED.values())

    assert declared == known, (
        "Compose and the address owner disagree about which services are published.\n"
        f"  published in Compose, unknown to the owner: {sorted(declared - known)}\n"
        f"  known to the owner, not published: {sorted(known - declared)}"
    )


def test_the_owner_can_answer_for_every_service_it_lists() -> None:
    """Each entry resolves to a real port rather than raising — the defaults are read from Compose,
    so an entry whose variable has no `:-default` there would blow up at the first call site."""
    for service in stack_addresses.PUBLISHED:
        assert stack_addresses.port(service) > 0, service
        assert stack_addresses.url(service).startswith("http://"), service


def test_an_environment_override_moves_every_answer(monkeypatch) -> None:
    """The property the whole arrangement exists for, proved rather than assumed."""
    monkeypatch.setenv("AIRA_PUBLISH_GATEWAY_PORT", "19001")

    assert stack_addresses.url("gateway") == "http://localhost:19001"


def test_a_bind_address_of_everything_is_not_handed_out_as_a_destination(monkeypatch) -> None:
    """`0.0.0.0` tells a listener *every interface*; as a destination it is a connection to nowhere
    on some stacks. The correction lives in the owner so it cannot be forgotten at a call site."""
    monkeypatch.setenv("AIRA_BIND_HOST", "0.0.0.0")

    assert stack_addresses.host() == "localhost"


def test_documented_addresses_are_todays_defaults() -> None:
    """Prose may name a port — a reader following a setup guide needs a number to type — but it
    must be the number they will actually get. A guide naming a port the stack no longer publishes
    is worse than one naming none."""
    published = _published_ports()
    wrong: list[str] = []
    for path in _tracked("docs") + [ROOT / "README.md"]:
        if path.suffix != ".md":
            continue
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for port in ADDRESS.findall(line):
                # Only ports that *look* like ours are checked: a doc quoting some other system's
                # `:3001` is not making a claim about this stack.
                if port in NOT_OURS or port in published:
                    continue
                if port in {"8001", "8002", "4200", "5432", "6379", "29092", "8200", "3000"}:
                    wrong.append(f"{path.relative_to(ROOT)}:{number}  port {port}")

    assert not wrong, (
        "These name a port that was one of this stack's defaults and is not any more:\n  "
        + "\n  ".join(wrong)
    )


def test_the_owner_follows_every_name_compose_consults(monkeypatch) -> None:
    """Compose's fallback for the three application services is **nested**, and the inner name is
    the one `docs/SETUP.md` documented for months:

        ${AIRA_PUBLISH_GATEWAY_PORT:-${AIRA_GATEWAY_PORT:-8001}}

    Reading only the outer name published the stack where the reader asked and left every tool
    asking for 8001 — this module's own defect, on the day it was written. `tests/integration/`
    proves the agreement against `docker compose config` itself; this checks the same property
    hermetically, so the mutation harness can break it.
    """
    monkeypatch.setenv("AIRA_GATEWAY_PORT", "19001")

    assert stack_addresses.port("gateway") == 19001, "the legacy name Compose still honours"

    monkeypatch.setenv("AIRA_PUBLISH_GATEWAY_PORT", "17001")

    assert stack_addresses.port("gateway") == 17001, "the outer name wins when both are set"


def test_a_service_with_no_legacy_name_still_resolves(monkeypatch) -> None:
    """The nested form exists for three services only; the other eleven have one name. A chain
    walker that assumed two would drop them, which is the mirror of the bug above."""
    monkeypatch.setenv("AIRA_PUBLISH_KEYCLOAK_PORT", "18080")

    assert stack_addresses.port("keycloak") == 18080
