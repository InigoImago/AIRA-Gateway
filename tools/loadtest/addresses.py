"""Where the load run connects — asked of the module that knows, never written down.

`tools/stack_addresses.py` is this repository's single owner of every published address, and
`tools/tests/test_one_owner_for_the_stack_addresses.py` fails when a second place writes one. A
load harness is the most tempting place to break that rule and the worst place to break it: it
opens hundreds of connections, and a run pointed at a port the stack no longer uses reports
"connection refused", which reads as *the gateway fell over* rather than *you knocked on the wrong
door*.

`stack_addresses` is a top-level module under `tools/`, imported by name because it imports
`compose_files` the same way. This package is imported as `tools.loadtest.…`, so `tools/` is put
on the path here — once, in the one module whose job that is — rather than at three call sites.

**The double's own port is not one of the stack's.** `deploy/compose/docker-compose.loadtest.yml`
is an overlay nobody registers in `tools/compose_files.py`, deliberately: it is a measuring
instrument layered on with an extra `-f`, not part of what a deployment runs, and registering it
would put a load-test service in front of every check that reads "the stack". So its port is
`AIRA_LOADTEST_MODELSIM_PORT` and not an `AIRA_PUBLISH_…` one — a name that does not claim
membership of a family it is not in.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parents[1])
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import stack_addresses  # noqa: E402

#: The double's default port, matching the overlay's own default.
MODELSIM_PORT = 8099


def gateway() -> str:
    """Where the gateway answers, by Compose's own resolution order."""
    return os.environ.get("AIRA_LOADTEST_GATEWAY") or stack_addresses.url("gateway")


def modelsim() -> str:
    """Where the double answers, for the baseline that takes the gateway out of the path."""
    if os.environ.get("AIRA_LOADTEST_MODELSIM"):
        return os.environ["AIRA_LOADTEST_MODELSIM"]
    port = os.environ.get("AIRA_LOADTEST_MODELSIM_PORT") or MODELSIM_PORT
    return f"http://{stack_addresses.host()}:{port}"
