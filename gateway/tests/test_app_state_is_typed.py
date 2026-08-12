"""Nothing reaches a service through ``app.state`` without saying what it is.

**What this guards, and what it cost not to have.** `app.state` is typed `Any`, so every call made
through it is invisible to `mypy --strict` — which passes over 255 files and, at these seams,
verifies nothing. On 2026-08-11 that cost a control: `SuspensionService.check` returns `Throttle`,
`RateLimitService.check` consumes `BucketRequest`, and the gate handed the first straight to the
second. They share no field the limiter reads, so a `throttle` suspension raised `AttributeError`
and became a **500** for every request from the caller it was meant to slow down — while the
console showed the decision as active and enforcing.

Neither existing layer could see it. mypy could not, for the reason above. The suite could not:
`test_suspensions.py` asserts the throttle carries `limit_rpm == 5` and **stops exactly at the
seam**. Coverage could not — every line ran.

One annotation makes it a build failure, and that was verified rather than assumed: reintroducing
the defect with the annotations in place produces

    error: Argument "extra" to "check" of "RateLimitService" has incompatible type
    "list[Throttle]"; expected "Sequence[BucketRequest]"

So the rule is: **a service read off `app.state` is annotated, or it goes through
`aira_gateway.state`.** Both give mypy the declared type; which one a site uses is a matter of how
often it is read.

`settings`, `db_engine`, `counters` and `degradation` are deliberately **out of scope**. The
failure mode this guards is *calling a method on the wrong kind of object*, and those are read for
their attributes by code that would break loudly and immediately. Widening the rule to everything
would make it a style preference, and a guard that is mostly style is one people learn to silence.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_gateway"

#: The attributes that hold an object with **methods the request path calls**. A wrong type here
#: is the throttle defect: an `AttributeError` at request time, from code that type-checks.
SERVICES = frozenset(
    {
        "anomalies",
        "budgets",
        "catalog",
        "db_sessionmaker",
        "group_grants",
        "log_writer",
        "oidc_validator",
        "pipeline_engine",
        "pipeline_store",
        "pricing",
        "providers",
        "rate_limits",
        "redactor",
        "reporting",
        "suspensions",
        "upstream_probe",
    }
)

#: Where the accessors live. Reads there are the definition of the rule, not exceptions to it.
ACCESSOR_MODULE = "state.py"


def _service_reads() -> list[tuple[str, int, str]]:
    """Every `<something>.app.state.<service>` read outside the accessor module, unannotated.

    An annotated assignment (`registry: ProviderRegistry = request.app.state.providers`) is the
    whole point, so those are excluded: they are exactly the fix this file asks for.
    """
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        if relative == ACCESSOR_MODULE:
            continue
        tree = ast.parse(path.read_text())
        annotated = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in SERVICES:
                continue
            if not _is_app_state(node.value):
                continue
            if id(node) in annotated:
                continue
            offenders.append((relative, node.lineno, node.attr))
    return offenders


def _is_app_state(node: ast.expr) -> bool:
    """True for the `<x>.app.state` part of `<x>.app.state.<service>`."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "state"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "app"
    )


def test_the_parser_finds_the_accessors_it_is_built_around() -> None:
    """The guard's own failure mode. A parser that matches nothing passes the assertion below, and
    this project has shipped two guards that could not fail — both silently green, both found only
    by breaking something on purpose."""
    accessor = ast.parse((SOURCE / ACCESSOR_MODULE).read_text())
    reads = [
        node
        for node in ast.walk(accessor)
        if isinstance(node, ast.Attribute) and node.attr in SERVICES and _is_app_state(node.value)
    ]

    assert len(reads) >= 8, f"state.py should read most services; found {len(reads)}"


def test_every_service_read_declares_what_it_got() -> None:
    offenders = _service_reads()

    assert not offenders, (
        "these read a service off `app.state` without saying what it is:\n  "
        + "\n  ".join(f"{path}:{line} -> {attr}" for path, line, attr in offenders)
        + "\n\n`app.state` is `Any`, so a call made through one of these is checked by nothing — "
        "which is how a `throttle` suspension came to be a 500 rather than a throttle. Annotate "
        "it (`x: TheType = request.app.state.thing`) or use the accessor in `aira_gateway.state`."
    )
