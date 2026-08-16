"""This layer must not read the machine it runs on.

One test, guarding one fixture, because the fixture is the kind that disappears in a tidy-up: it
does nothing visible, and everything it prevents is invisible too. What it prevents happened —
see `conftest._no_redis_from_this_machine` for the incident.

`LESSONS.md` §7: *"a unit test that reads the developer's machine is a test about that machine"*.
"""

from __future__ import annotations

from aira_gateway.config import GatewaySettings


def test_the_unit_suite_never_finds_a_redis_that_happens_to_be_running() -> None:
    """`redis://localhost:6379/0` is both the default and the address `make up` publishes.

    So with the stack up, the "hermetic" suite shared a **durable** bucket store with the developer
    and with its own previous runs — a rate-limit test that sets one request per minute was refused
    its *first* request because a run half an hour earlier had spent the bucket. Green in CI, green
    on a machine with the stack down, red on a machine with it up.

    Asserted on the settings rather than on a limiter, because the leak is in what the process
    resolves before any limiter exists.
    """
    assert GatewaySettings().redis_url == "", (
        "the gateway unit suite is resolving a real Redis; the autouse fixture in conftest is gone "
        "or has stopped taking effect"
    )


def test_a_test_that_means_to_talk_about_redis_still_can() -> None:
    """The fixture sets a *default*, and must not become a ceiling.

    An explicit keyword outranks the environment in pydantic-settings, which is what lets the cases
    that are genuinely about the Redis path keep saying so.
    """
    assert GatewaySettings(redis_url="redis://elsewhere:6379/7").redis_url == (
        "redis://elsewhere:6379/7"
    )
