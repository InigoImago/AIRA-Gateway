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

**`SERVICES` is a hand-written list, which is the shape this repository keeps getting caught by.**
It was written by reading `create_app` once. Nothing tied the two together, so the twenty-first
service would have been governed by nothing — and it would announce itself through nothing at all,
because the rule below is a rule *about a set*, and a set that does not contain a thing has no
opinion about it. That is the same silence as a capability the console cannot declare and a Kafka
event with no topic. The answer is the one arrived at five times before it: compare the list
against what `create_app` actually assembles, **in both directions**. A service missing from
`SERVICES` is ungoverned; a name in `SERVICES` that `create_app` no longer sets is a rule about
something that is gone, which is the harmless-looking half that makes the list stop describing the
system.
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

#: The attributes `create_app` puts on `app.state` that hold no object with methods worth
#: type-checking, and why each is exempt. Named individually rather than as a blanket "anything not
#: in `SERVICES`", because the whole point of the check below is that the default for a **new**
#: attribute is to be governed: an exemption should cost a line and a sentence.
NOT_A_SERVICE = {
    "settings": "a pydantic settings object, read for its fields; a wrong one fails on the field",
    "db_engine": "the SQLAlchemy engine, handed to a sessionmaker and otherwise disposed of",
    "counters": "may be `None` by design when Redis is absent, and every reader tests for that",
    "degradation": "a log of what is degraded, read for its contents by the readiness probe",
}


def _service_reads() -> list[tuple[str, int, str]]:
    """Every read of a service off `app.state` outside the accessor module, unannotated.

    An annotated assignment (`registry: ProviderRegistry = request.app.state.providers`) is the
    whole point, so those are excluded: they are exactly the fix this file asks for.

    **Both spellings.** The first version matched attribute access only, and missed
    `getattr(request.app.state, "rate_limits", None)` — which is the *more* dangerous of the two
    and was in use at three sites. An attribute read of a renamed service raises `AttributeError`
    and is loud; a `getattr` with a default answers `None`, and the call site's `if x is None:
    return` then turns a renamed or mistyped service into a control that silently does not run.
    The bound on failed authentications is one of those sites, so the guard was blind exactly
    where a security control could disappear without a sound.
    """
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        if relative == ACCESSOR_MODULE:
            continue
        tree = ast.parse(path.read_text())
        annotated = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in SERVICES:
                if _is_app_state(node.value) and id(node) not in annotated:
                    offenders.append((relative, node.lineno, node.attr))
                continue
            named = _getattr_on_app_state(node)
            if named is not None and id(node) not in annotated:
                offenders.append((relative, node.lineno, named))
    return offenders


def _getattr_on_app_state(node: ast.AST) -> str | None:
    """The service name in `getattr(<x>.app.state, "<service>", …)`, or None."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return None
    if node.func.id != "getattr" or len(node.args) < 2:
        return None
    target, name = node.args[0], node.args[1]
    if not _is_app_state(target) or not isinstance(name, ast.Constant):
        return None
    return name.value if name.value in SERVICES else None


def _is_app_state(node: ast.expr) -> bool:
    """True for the `<x>.app.state` part of `<x>.app.state.<service>`."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "state"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "app"
    )


def _assembled_services() -> set[str]:
    """Every attribute `create_app` assigns to `app.state`.

    Assignments only. A *read* of `app.state.suspensions` inside `create_app` — there is one, where
    the anomaly service is handed the suspension service — is not this application declaring a new
    attribute, and counting it would make the two lists agree for the wrong reason.

    Matched on the bare ``app.state``, not on `_is_app_state`: that predicate looks for
    ``<x>.app.state``, which is how a *request* reaches it, and `create_app` holds the application
    directly. Reusing it here found nothing at all and reported every tracked name as stale — the
    guard's own failure mode, caught by having written both directions.
    """
    tree = ast.parse((SOURCE / "app.py").read_text())
    return {
        target.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "state"
        and isinstance(target.value.value, ast.Name)
        and target.value.value.id == "app"
    }


def test_every_service_create_app_assembles_is_governed_by_this_rule() -> None:
    """A new service on `app.state` is checked, or it is exempt on purpose and says why."""
    ungoverned = sorted(_assembled_services() - SERVICES - set(NOT_A_SERVICE))

    assert not ungoverned, (
        f"`create_app` puts {ungoverned} on `app.state` and this file has no opinion about it.\n\n"
        "Every call made through one of those is invisible to mypy, which is how a `throttle` "
        "suspension became a 500. Add it to `SERVICES` if the request path calls methods on it, "
        "or to `NOT_A_SERVICE` with the reason it does not need to be."
    )


def test_this_rule_names_nothing_create_app_has_stopped_assembling() -> None:
    """The other direction. A name left behind describes a service that is gone — and the guard
    then reads as covering more than it does, which is the half nobody notices because removing
    something never breaks a test."""
    tracked = SERVICES | set(NOT_A_SERVICE)
    stale = sorted(tracked - _assembled_services())

    assert not stale, f"`create_app` no longer puts these on `app.state`: {stale}"


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
