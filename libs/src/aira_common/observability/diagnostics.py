"""The OpenTelemetry SDK's own log lines, printed rather than posted to itself (`FRD-617` §3.2).

The SDK reports an export failure on a stdlib logger under `opentelemetry.*`. That record
propagates to the root logger, whose only handler with `AIRA_OTEL_ENABLED=true` is the OTLP one —
so the explanation of a failed export was queued for export through the exporter that failed.
`logging.lastResort` does not help: it prints only when a record finds no handler at all.
"""

from __future__ import annotations

import logging

from aira_common.observability.redaction import redact_url_query


class SdkDiagnostics(logging.Handler):
    """Write the `opentelemetry` logger tree to stdout, marked local-only.

    The level is inherited (`WARNING`) on purpose: lowering it buys every instrumentation's `DEBUG`
    chatter, and successful exports are already reported by `WatchedExport`.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Imported here: `aira_common.logging` imports this package at module scope.
            from aira_common.logging import get_logger

            log = get_logger("opentelemetry.sdk")
            say = log.warning if record.levelno >= logging.WARNING else log.info
            say(
                "otel_sdk",
                level=record.levelname,
                logger=record.name,
                message=redact_url_query(record.getMessage())[:400],
                local_only=True,
            )
        except Exception:  # noqa: BLE001 — a diagnostic must never be the reason something fails
            return


def route_sdk_diagnostics() -> bool:
    """Send the `opentelemetry` logger tree to stdout rather than into OTLP. Idempotent.

    Returns whether it installed the handler.
    """
    logger = logging.getLogger("opentelemetry")
    if any(isinstance(handler, SdkDiagnostics) for handler in logger.handlers):
        return False
    logger.addHandler(SdkDiagnostics())
    # The fix itself: nothing under `opentelemetry` may reach the root logger, where OTLP lives.
    logger.propagate = False
    return True
