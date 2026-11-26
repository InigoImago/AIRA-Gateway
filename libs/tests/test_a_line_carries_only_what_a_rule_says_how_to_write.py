"""A log line carries only the fields the register names, each written by its rule (`FRD-631`).

The register is an allow-list (`aira_common.logging.fields`): a field nobody registered leaves the
line by name, a person is a pseudonym under strict data mode, an address is written as
`AIRA_TELEMETRY_IP` says — and the line a SIEM receives says each of them the same way whichever
call site wrote it. Rendered through the real pipeline and read back from stdout, because the rule
is what reaches the reader, not what a processor returns.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aira_common.addresses import AddressMode, configure_addresses
from aira_common.logging import FIELDS, WITHHELD, FieldKind, configure_logging, get_logger
from aira_common.pseudonym import PERSON_FIELDS, configure_pseudonyms, pseudonym


def _line(capsys: Any, event: str, **fields: Any) -> dict[str, Any]:
    configure_logging("INFO", json_output=True)
    get_logger("t").info(event, **fields)
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_a_field_nobody_registered_leaves_the_line_by_name(capsys: Any) -> None:
    """`directory.search` wrote the name somebody looked up under `query` for as long as the rule
    was a list of what to hide. Unknown now means withheld — and said, so it is registered rather
    than wondered about."""
    line = _line(capsys, "directory.search", actor="ada", query="johannes", use_case="uc")

    assert "query" not in line
    assert "johannes" not in json.dumps(line)
    assert line[WITHHELD] == ["query"]
    assert line["use_case"] == "uc"


def test_a_line_with_only_registered_fields_names_nothing_withheld(capsys: Any) -> None:
    line = _line(capsys, "relay_run", published=2, pending=0)

    assert WITHHELD not in line
    assert (line["published"], line["pending"]) == (2, 0)


def test_every_person_field_is_registered_as_a_person_and_nothing_else_is() -> None:
    """The register and the pseudonymiser must agree, or a person goes out as plain text."""
    registered = {name for name, field in FIELDS.items() if field.kind is FieldKind.PERSON}
    assert registered == set(PERSON_FIELDS) | {"author"}


def test_a_person_leaves_as_a_pseudonym_under_strict_data_mode(capsys: Any) -> None:
    configure_pseudonyms(enabled=True, secret="key")

    line = _line(capsys, "config.changed", actor="ada", person="bob", object="membership")

    assert line["actor"] == pseudonym("ada") and line["person"] == pseudonym("bob")
    assert "ada" not in json.dumps(line) and "bob" not in json.dumps(line)


def test_a_findings_target_is_a_pseudonym_only_where_it_is_a_person(capsys: Any) -> None:
    """`anomaly_detected` wrote the person a finding is about in plain text under strict data
    mode: its `target_value` is a person when the target is `subject`, a key prefix or a use case
    otherwise — which name nobody and stay."""
    configure_pseudonyms(enabled=True, secret="key")

    person = _line(capsys, "anomaly_detected", target="subject", target_value="ada")
    key = _line(capsys, "anomaly_detected", target="credential", target_value="aira_ab12")

    assert person["target_value"] == pseudonym("ada")
    assert key["target_value"] == "aira_ab12"


def test_a_kafka_records_key_names_a_member_and_is_written_as_one(capsys: Any) -> None:
    configure_pseudonyms(enabled=True, secret="key")

    line = _line(capsys, "integration_call", key="uc|max.mustermann", topic="aira.memberships")

    assert "mustermann" not in json.dumps(line)


@pytest.mark.parametrize(
    ("mode", "present", "absent"),
    [
        (AddressMode.PSEUDONYM, {"source_net", "source_id"}, {"source_ip"}),
        (AddressMode.NETWORK, {"source_net"}, {"source_ip", "source_id"}),
        (AddressMode.FULL, {"source_ip"}, {"source_net", "source_id"}),
    ],
)
def test_an_address_is_written_as_the_installation_says(
    capsys: Any, mode: AddressMode, present: set[str], absent: set[str]
) -> None:
    configure_addresses(mode)

    line = _line(capsys, "auth_refused", source_ip="10.1.2.3")

    assert present <= line.keys() and not (absent & line.keys())
    if mode is not AddressMode.FULL:
        assert "10.1.2.3" not in json.dumps(line)
        assert line["source_net"] == "10.1.2.0/24"


def test_every_registered_field_has_a_type_and_a_meaning() -> None:
    """`docs/LOGGING.md` is generated from these, and a SIEM maps a field to the type it states."""
    for name, field in FIELDS.items():
        assert field.type in {"str", "int", "float", "bool", "list", "json"}, name
        assert field.meaning.strip().endswith("."), name
