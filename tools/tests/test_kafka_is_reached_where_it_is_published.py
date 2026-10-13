"""Moving Kafka's published port moves everything that reaches it.

`AIRA_PUBLISH_KAFKA_PORT` publishes the host-facing listener anywhere, and two things did not
follow it. The broker **advertised** `localhost:29092` whatever the port was — and a Kafka client
bootstraps at the address it is given, then reconnects to the one advertised, so a host client on a
moved port was sent back to 29092, to nothing or to the very service the port was moved away from.
And `make kafka-topics` ran *inside* the broker's container with the host-side published address,
which inside the container is nobody.
"""

from __future__ import annotations

import re
from pathlib import Path

import compose_files

ROOT = Path(__file__).resolve().parents[2]
PUBLISHED = "${AIRA_PUBLISH_KAFKA_PORT:-29092}"


def test_the_host_listener_advertises_the_port_it_is_published_on() -> None:
    kafka = compose_files.load(compose_files.INFRA)["services"]["kafka"]
    advertised = dict(
        listener.split("://", 1)
        for listener in kafka["environment"]["KAFKA_ADVERTISED_LISTENERS"].split(",")
    )

    assert kafka["ports"], "the broker publishes nothing, so this compares nothing"
    assert all(str(entry).endswith(f":{PUBLISHED}:29092") for entry in kafka["ports"]), kafka
    assert advertised["PLAINTEXT_HOST"].endswith(f":{PUBLISHED}"), advertised


def test_topics_are_created_through_the_brokers_own_listener() -> None:
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.partition("\nkafka-topics:")[2].partition("\n\n")[0]
    bootstrap = re.search(r"--bootstrap-server (\S+)", recipe)

    assert bootstrap, "kafka-topics no longer names a bootstrap server"
    variable = bootstrap.group(1).removeprefix("$(").removesuffix(")")
    value = re.search(rf"^{re.escape(variable)}\s*:=\s*(\S+)", makefile, re.M)
    assert value, f"{bootstrap.group(1)} is not a Makefile assignment"
    assert value.group(1) == "localhost:9092", (
        "the command runs inside the broker's container, where only the broker's own listener is "
        f"reachable — not {value.group(1)}"
    )
