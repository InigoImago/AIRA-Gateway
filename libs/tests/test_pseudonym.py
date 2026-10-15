"""Telemetry and logs carry a pseudonym, not a name, under strict data mode (`FRD-627` FR-14)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from aira_common import pseudonym as module
from aira_common.logging import configure_logging, get_logger
from aira_common.pseudonym import configure_pseudonyms, pseudonym, pseudonymise_event


@pytest.fixture(autouse=True)
def _names_again_afterwards() -> Iterator[None]:
    yield
    configure_pseudonyms(enabled=False, secret="")


def test_off_a_name_is_written_as_it_is() -> None:
    configure_pseudonyms(enabled=False, secret="key")

    assert pseudonym("alice") == "alice"


def test_on_one_person_is_one_pseudonym_and_no_name() -> None:
    configure_pseudonyms(enabled=True, secret="key")

    first, again, other = pseudonym("alice"), pseudonym("alice"), pseudonym("bob")
    assert first == again != other
    assert first.startswith("p-") and "alice" not in first
    assert pseudonym(first) == first, "a pseudonym is not pseudonymised twice"


def test_the_key_decides_so_a_directory_cannot_be_hashed_back() -> None:
    configure_pseudonyms(enabled=True, secret="one")
    under_one = pseudonym("alice")
    configure_pseudonyms(enabled=True, secret="two")

    assert pseudonym("alice") != under_one


def test_without_a_secret_the_key_is_drawn_and_said_to_be_unstable() -> None:
    assert configure_pseudonyms(enabled=True, secret="") is False
    assert module._key is not None
    assert pseudonym("alice") != "alice"
    assert configure_pseudonyms(enabled=True, secret="key") is True


def test_every_person_field_of_a_log_line_and_a_persons_authorship() -> None:
    configure_pseudonyms(enabled=True, secret="key")
    event = {
        "event": "x",
        "subject": "alice",
        "username": "alice",
        "author": "user:alice",
        "model": "m",
    }
    rule = {"author": "rule:7"}

    pseudonymise_event(None, "info", event)
    pseudonymise_event(None, "info", rule)

    assert event["subject"] == event["username"] == pseudonym("alice")
    assert event["author"] == "user:" + pseudonym("alice")
    assert event["model"] == "m"
    assert rule["author"] == "rule:7"


def test_a_log_line_leaves_with_the_pseudonym(capsys: pytest.CaptureFixture[str]) -> None:
    configure_pseudonyms(enabled=True, secret="key")
    configure_logging("INFO", json_output=True)

    get_logger("t").info("rate_limited", subject="alice")

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["subject"] == pseudonym("alice")
