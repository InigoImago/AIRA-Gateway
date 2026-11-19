"""Every client the live suite builds from a plane's settings reaches the stack under test.

`tools/stack_addresses.py` decides where this stack answers, and the suite's own clients ask it.
Three kinds did not: a `GatewaySettings()` or `ManagementSettings()` a test builds, the engine the
gateway's migrations build for themselves, and `manage.py relay` in a subprocess. They read the
planes' settings, whose defaults are a stack on today's ports, and the hermetic `conftest.py`
removes every `AIRA_*` variable from a session, so nothing a developer exported could move them.
Beside a second stack on moved ports they reached the first one: a relay run read its outbox, and
the migration check would have made and dropped its scratch database there.

On a stack on today's ports both answers are the same, as they should be; on a moved one this is
what fails first.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
import stack_addresses
from aira_management.config.app_settings import ManagementSettings

from aira_gateway.config import GatewaySettings

pytestmark = pytest.mark.integration

#: What a plane's process prints about where it would connect, in a subprocess of its own.
WHERE = (
    "from aira_management.config.app_settings import ManagementSettings as S; s = S(); "
    "print(s.postgres_host, s.postgres_port, s.kafka_bootstrap_servers)"
)


def test_a_planes_settings_built_by_a_test_reach_this_stack() -> None:
    database = (stack_addresses.host(), stack_addresses.port("postgres"))
    for settings in (GatewaySettings(), ManagementSettings()):
        assert (settings.postgres_host, settings.postgres_port) == database
        assert settings.kafka_bootstrap_servers == stack_addresses.netloc("kafka")
    assert GatewaySettings().redis_url == stack_addresses.redis_url()


def test_a_planes_process_started_by_a_test_reaches_this_stack() -> None:
    done = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", WHERE], capture_output=True, text=True, timeout=60
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == [
        stack_addresses.host(),
        str(stack_addresses.port("postgres")),
        stack_addresses.netloc("kafka"),
    ]
