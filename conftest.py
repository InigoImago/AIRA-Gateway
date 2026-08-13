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

**A third thing of the same family, found 2026-08-12 by watching the suite finish and then not
exit.** Two tests configure real OpenTelemetry providers — that is what they are for — and the
providers are **global** and were never taken down again. Each one starts three non-daemon batch
exporters aimed at `http://localhost:4318`, and installs an OTLP handler on the **root logger**.
So from those tests onwards every span the gateway tests produce and every log record anything
emits was queued for shipping to a collector that is not there; at exit the SDK tries to flush them
and retries. Measured: those two tests run in **0.13 s** and their process takes **15.7 s** to end.
Across the whole suite it is minutes, and none of it is test time — `-q` prints its last line and
the run appears to hang.

Same shape as the two above. A unit test that reads the developer's machine is a test whose green
is about that machine; a unit test that leaves a global exporter running makes every test after it
do background network I/O, which is the one thing "hermetic" is supposed to mean. It is invisible
because nothing fails: the exports quietly do not arrive.
"""

from __future__ import annotations

import logging
import os

import pytest
from opentelemetry import trace
from opentelemetry._logs import _internal as logs_internal
from opentelemetry.metrics import _internal as metrics_internal
from opentelemetry.sdk._logs import LoggingHandler

from aira_common.config import BaseAiraSettings

# Applied at import time, before any test module constructs a settings object. A fixture would be
# too late for a module-level `create_app(...)` and for the settings that some modules build on
# import — and "too late" here means the file is read once and the whole session inherits it.
BaseAiraSettings.model_config["env_file"] = None

# An export attempt in the hermetic suite must not be allowed to take time. Nothing is listening on
# the endpoint the telemetry tests name, and connection-refused is instant — but the OTLP exporter
# then **retries with exponential backoff**, which is where the seconds go: measured at 7.6 s for
# fifty spans and 8.2 s for one log record, all of it inside one test's teardown.
#
# These are read when a processor is constructed, so they are set at import time for the same
# reason as the line above. They bound the *test environment* and say nothing about production,
# where a collector answers and these figures would be wrong.
os.environ.setdefault("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
os.environ.setdefault("OTEL_BSP_EXPORT_TIMEOUT", "300")
os.environ.setdefault("OTEL_BLRP_EXPORT_TIMEOUT", "300")
os.environ.setdefault("OTEL_METRIC_EXPORT_TIMEOUT", "300")


@pytest.fixture(autouse=True, scope="session")
def _no_ambient_configuration() -> None:
    """Remove `AIRA_*` and `VAULT_*` from the environment for the whole session.

    Not `monkeypatch`, which is function-scoped: this is a property of the run, and restoring it
    between tests would let one that spawns a subprocess inherit the shell's values back.
    """
    for name in [key for key in os.environ if key.startswith(("AIRA_", "VAULT_"))]:
        del os.environ[name]


#: The three global slots `configure_observability` writes, as (module, value attribute, set-once
#: attribute). Private names, deliberately and with the reason stated: the SDK's public setters are
#: **set-once** — `set_tracer_provider` on an already-configured process logs a warning and does
#: nothing — so there is no supported way to put a global back. Naming them here rather than at
#: three call sites means a version that renames one breaks in a single place, and the guard below
#: is what makes that break loud instead of silent.
_OTEL_GLOBALS = (
    (trace, "_TRACER_PROVIDER", "_TRACER_PROVIDER_SET_ONCE"),
    (metrics_internal, "_METER_PROVIDER", "_METER_PROVIDER_SET_ONCE"),
    (logs_internal, "_LOGGER_PROVIDER", "_LOGGER_PROVIDER_SET_ONCE"),
)


def _otel_state() -> list[object | None]:
    return [getattr(module, value) for module, value, _once in _OTEL_GLOBALS]


@pytest.fixture(autouse=True)
def _no_leaked_telemetry() -> object:
    """Whatever a test installs globally into OpenTelemetry, it takes with it.

    **Autouse, and that is the point.** The failure this prevents is a test installing a global
    without saying so, so a fixture the leaking test has to opt into is a fixture the next one
    forgets — which is how both current leaks happened. It costs three attribute reads per test
    when nothing changed, which is every test but two.

    Teardown does two things, in order, and both are needed:

    - **Shut the provider down**, which stops its exporter threads and drops what they queued.
      Restoring the global alone would leave those threads running for the rest of the session,
      still exporting, still holding the process open at exit.
    - **Put the previous global back**, including the `SET_ONCE` latch. Leaving a shut-down
      provider installed is not neutral: every later span goes to an object that silently discards
      it, so a test asserting on tracing would pass or fail depending on what ran before it.

    The root logger's OTLP handler goes too. It is not part of the provider — it is a stdlib
    handler pointed at one — so shutting the provider down leaves it attached, and every log record
    from every subsequent test would be handed to a dead exporter.
    """
    before = _otel_state()
    handlers = list(logging.getLogger().handlers)

    yield

    for handler in logging.getLogger().handlers:
        if isinstance(handler, LoggingHandler) and handler not in handlers:
            logging.getLogger().removeHandler(handler)

    for (module, value, once), previous in zip(_OTEL_GLOBALS, before, strict=True):
        current = getattr(module, value)
        if current is previous:
            continue
        shutdown = getattr(current, "shutdown", None)
        if callable(shutdown):
            shutdown()
        setattr(module, value, previous)
        # The latch, not only the value. `Once` records that a provider was set, and the setter
        # consults it before assigning — so a restored `None` with a tripped latch means the next
        # `set_tracer_provider` is ignored and the *next* test to configure observability gets a
        # no-op provider while believing it configured one.
        latch = getattr(module, once, None)
        if latch is not None and previous is None:
            setattr(module, once, type(latch)())


#: The threads the OTLP batch processors run under. Matched by prefix because the SDK appends a
#: counter, and named here so the check below reads as a list of what is being looked for rather
#: than as a regex nobody can evaluate.
OTEL_EXPORTER_THREADS = ("OtelBatchSpan", "OtelPeriodicExp", "OtelBatchLogRec")


@pytest.fixture(autouse=True, scope="session")
def _the_suite_leaves_no_exporters_running() -> object:
    """The property the fixture above exists for, asserted where it is visible: at the end.

    A leaked exporter does not fail anything. It exports into the void, so nothing is missing and
    nothing is wrong — the only symptom is that the run takes longer than the tests do, and that
    reads as "the suite is slow" rather than as a defect with an address. The two that were found
    on 2026-08-12 had been there long enough that the hang was folded into what people expected.

    Asserted on the *threads* rather than on the globals, because the threads are what the cost
    actually is: they are non-daemon, so they hold the process open, and at exit the SDK flushes
    what they queued to a collector that is not there. Restoring a global while leaving its threads
    running would satisfy a tidier check and fix nothing.
    """
    yield

    import threading

    leaked = sorted(
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith(OTEL_EXPORTER_THREADS)
    )

    assert not leaked, (
        f"the suite finished with OpenTelemetry exporters still running: {leaked}. A test "
        "configured global providers and did not take them down, so every test after it queued "
        "spans and log records for a collector that is not there, and the process cannot exit "
        "until the SDK gives up flushing them."
    )
