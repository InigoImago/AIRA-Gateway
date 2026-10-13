"""`make showcase`'s traffic step reports a failure in a sentence, about this stack.

Three defects of one family — output that misleads the person reading it:

- a gateway that does not answer raised an `httpx.ConnectError` traceback out of `make showcase`;
- the detailed explanation of an all-`401` run sat behind a check that an earlier `return` had
  already answered, so it could never print;
- the hints named `aira-postgres` and `aira-management-relay` whatever `AIRA_STACK` was, sending a
  second stack's operator to the first stack's containers.
"""

from __future__ import annotations

import asyncio
import inspect

import demo_traffic
import demo_wait_ready
import pytest
import stack_addresses


def test_a_gateway_that_does_not_answer_is_one_line_and_a_failing_exit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Port 1 on loopback: nothing listens there, so the connection is refused at once.
    monkeypatch.setattr(demo_traffic, "GATEWAY", "http://127.0.0.1:1")

    code = asyncio.run(demo_traffic.run())

    printed = capsys.readouterr().err
    assert code == 1
    assert "did not answer" in printed and "Traceback" not in printed


def test_the_hints_name_this_stacks_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIRA_STACK", "second")
    sources = inspect.getsource(demo_traffic) + inspect.getsource(demo_wait_ready)

    assert stack_addresses.container("postgres") == "second-postgres"
    assert not [
        name
        for name in ("aira-postgres", "aira-management-relay", "aira-ollama-pull")
        if f"docker logs {name}" in sources or f"docker exec {name}" in sources
    ]
