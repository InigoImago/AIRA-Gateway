"""The hermetic suite is hermetic, and this file is what makes that true.

`BaseAiraSettings` loads `.env` from the working directory, because a developer running
`make run-gateway` wants their local credentials picked up. The unit tests construct those same
settings classes — so **the suite read whatever `.env` the developer happened to have**, and every
test that touches the provider registry or a configured host behaved differently from machine to
machine.

CI is what noticed. Two tests failed there and passed everywhere the project had been developed:

- `test_a_declaration_reaches_the_model_list` asserts that models the registry serves besides the
  declared one report themselves as undeclared. With a Google key in `.env` the registry carries a
  Gemini upstream, so there *were* others; with no `.env` the mock serves one model, the list has
  one entry, and the assertion had nothing to assert on.
- `test_an_unreachable_upstream_degrades_rather_than_failing_readiness` calls `/readyz`, which
  makes real TCP checks against Postgres and Kafka. It passed on a machine with the Compose stack
  running.

Both are the same defect wearing different clothes: **a unit test that reads the developer's
machine is a test whose green is about that machine.** The suite reports on the code now.

Two things are neutralised here, and neither is a preference:

- the dotenv, by name — a test may still set a variable deliberately, and `GatewaySettings(...)`
  keyword arguments are unaffected;
- `AIRA_*` and `VAULT_*` in the process environment, for the same reason one step out. A shell
  that exported `AIRA_GOOGLE_API_KEY` is a shell in which the suite tests a different product.

The second failure is not fixed here — a readiness test that needs a reachable Postgres has to say
so itself, which it now does by opening a socket. This file only removes the *ambient* dependency.
"""

from __future__ import annotations

import os

import pytest

from aira_common.config import BaseAiraSettings

# Applied at import time, before any test module constructs a settings object. A fixture would be
# too late for a module-level `create_app(...)` and for the settings that some modules build on
# import — and "too late" here means the file is read once and the whole session inherits it.
BaseAiraSettings.model_config["env_file"] = None


@pytest.fixture(autouse=True, scope="session")
def _no_ambient_configuration() -> None:
    """Remove `AIRA_*` and `VAULT_*` from the environment for the whole session.

    Not `monkeypatch`, which is function-scoped: this is a property of the run, and restoring it
    between tests would let one that spawns a subprocess inherit the shell's values back.
    """
    for name in [key for key in os.environ if key.startswith(("AIRA_", "VAULT_"))]:
        del os.environ[name]
