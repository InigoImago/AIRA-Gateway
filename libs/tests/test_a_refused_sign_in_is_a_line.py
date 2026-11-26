"""A refused access attempt is a line, at most so many per source (`FRD-631` FR-6).

A 401 or a 403 before attribution writes no audit row, and its span was its only record — so with
no trace exported, a failed sign-in was nowhere. It is an `auth_refused` line now; and because a
credential-stuffing run is thousands a minute from a handful of addresses, one source writes at
most ten lines a minute, and the next line it writes says how many were held back.
"""

from __future__ import annotations

import json
from typing import Any

from aira_common.logging import configure_logging
from aira_common.logging.refusals import (
    LINES_PER_SOURCE,
    WINDOW_SECONDS,
    PerSource,
    say_refused,
)
from aira_common.pseudonym import configure_pseudonyms, pseudonym


def _lines(capsys: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip() and json.loads(line).get("event") == "auth_refused"
    ]


def test_a_refusal_says_what_a_siem_correlates_on(capsys: Any) -> None:
    configure_pseudonyms(enabled=True, secret="key")
    configure_logging("INFO")

    say_refused(
        outcome="forbidden",
        status=403,
        source_ip="10.1.2.3",
        subject="ada",
        auth_method="oidc",
        use_case_requested="uc-a",
        reason="Not a member of use case 'uc-a'.",
        now=0.0,
    )

    (line,) = _lines(capsys)
    assert (line["level"], line["category"], line["outcome"], line["status"]) == (
        "warning",
        "authorization",
        "forbidden",
        403,
    )
    assert line["subject"] == pseudonym("ada")
    assert line["use_case_requested"] == "uc-a"
    assert line["source_net"] == "10.1.2.0/24" and "10.1.2.3" not in json.dumps(line)


def test_a_refusal_of_nobody_is_an_authentication_event(capsys: Any) -> None:
    configure_logging("INFO")
    say_refused(outcome="unauthenticated", status=401, source_ip="10.0.0.9", now=0.0)

    (line,) = _lines(capsys)
    assert line["category"] == "authentication" and "subject" not in line


def test_one_source_writes_its_share_and_the_next_line_says_how_many_it_held_back(
    capsys: Any,
) -> None:
    configure_logging("INFO")
    for _ in range(LINES_PER_SOURCE + 5):
        say_refused(outcome="unauthenticated", status=401, source_ip="203.0.113.9", now=1.0)
    say_refused(
        outcome="unauthenticated", status=401, source_ip="203.0.113.9", now=1.0 + WINDOW_SECONDS
    )

    lines = _lines(capsys)
    assert len(lines) == LINES_PER_SOURCE + 1
    assert lines[-1]["suppressed"] == 5
    assert "suppressed" not in lines[0]


def test_one_noisy_source_does_not_silence_another(capsys: Any) -> None:
    configure_logging("INFO")
    for _ in range(LINES_PER_SOURCE * 3):
        say_refused(outcome="unauthenticated", status=401, source_ip="203.0.113.9", now=1.0)
    assert say_refused(outcome="unauthenticated", status=401, source_ip="198.51.100.7", now=1.0)


def test_the_sources_remembered_are_bounded() -> None:
    bound = PerSource(limit=1, window=60.0, sources=3)
    for index in range(10):
        bound.admit(f"10.0.0.{index}", now=0.0)

    assert len(bound._seen) == 3
    # The first source was forgotten, so it starts a fresh window rather than being held back.
    assert bound.admit("10.0.0.0", now=0.0) == (True, 0)
