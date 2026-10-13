"""`make otel-status` exits non-zero whenever it reports telemetry being lost.

It printed *the collector refused some* or *delivery gave up on some* and exited `0` — non-zero
only for a queue that was not draining. A script or CI step running it could not tell the loss it
had just reported from a clean bill of health.
"""

from __future__ import annotations

import otel_status
import pytest

ACCEPTED = 'otelcol_receiver_accepted_spans{receiver="otlp",transport="grpc"} 20\n'
SENT = 'otelcol_exporter_sent_spans{exporter="otlp/backend"} 15\n'


@pytest.mark.parametrize(
    "loss",
    [
        'otelcol_exporter_send_failed_spans{exporter="otlp/backend"} 5\n',
        'otelcol_receiver_refused_spans{receiver="otlp",transport="grpc"} 3\n',
    ],
    ids=["delivery gave up", "collector refused"],
)
def test_a_reported_loss_is_a_failing_exit(loss: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otel_status, "_fetch", lambda url: ACCEPTED + SENT + loss)
    monkeypatch.setattr(otel_status, "_recent_failures", lambda *a, **k: [])

    assert otel_status.main() == 1


def test_nothing_lost_is_a_passing_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otel_status, "_fetch", lambda url: ACCEPTED + SENT)
    monkeypatch.setattr(otel_status, "_recent_failures", lambda *a, **k: [])

    assert otel_status.main() == 0
