"""Both planes read `AIRA_KAFKA_BOOTSTRAP_SERVERS` alike — what their readiness probes dial and
what their settings refuse at startup (`G1-11` on the gateway, its twin on Management).

The rule lives in `aira_common.kafka.parse_bootstrap_servers`, which Management reads; the gateway
still carries its own copy in `aira_gateway.config`. Until it imports the shared one, the two are
held equal here on the shapes an operator writes — including the ones each must refuse.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in (ROOT / "gateway" / "src", ROOT / "libs" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from aira_common.kafka import parse_bootstrap_servers as shared_parse  # noqa: E402
from aira_gateway.config import parse_bootstrap_servers as gateway_parse  # noqa: E402

ACCEPTED = ["", "kafka:9092", "kafka", " a:1 , b:2 ,", "[::1]:9093", "[2001:db8::1]", "x:65535"]
REFUSED = ["kafka:9092x", "kafka:0", "kafka:65536", "[::1", "::1:9092", ":9092", "[::1]x"]


@pytest.mark.parametrize("value", ACCEPTED)
def test_both_planes_dial_the_same_brokers(value: str) -> None:
    assert shared_parse(value) == gateway_parse(value)


@pytest.mark.parametrize("value", REFUSED)
def test_both_planes_refuse_the_same_brokers(value: str) -> None:
    with pytest.raises(ValueError):
        shared_parse(value)
    with pytest.raises(ValueError):
        gateway_parse(value)
