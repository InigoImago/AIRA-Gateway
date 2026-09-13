"""An OTLP batch rendered as OTLP/JSON, for whoever has to parse it (`FRD-617` §3.10).

**Not what goes over this leg.** The applications post `application/x-protobuf` and cannot be made
to post JSON (`INTEGRATIONS.md` §6); this is the protobuf-JSON mapping of the same content — the
shape a collector produces with `encoding: json`, and the shape a SIEM parses.
"""

from __future__ import annotations

import base64
import contextlib
import importlib
import json
from typing import Any

#: The encoder per signal — the **exporter's own**, so what is printed cannot drift from what is
#: sent.
_ENCODERS: dict[str, tuple[str, str]] = {
    "traces": ("opentelemetry.exporter.otlp.proto.common.trace_encoder", "encode_spans"),
    "logs": ("opentelemetry.exporter.otlp.proto.common._internal._log_encoder", "encode_logs"),
    "metrics": (
        "opentelemetry.exporter.otlp.proto.common._internal.metrics_encoder",
        "encode_metrics",
    ),
}

#: The OTLP fields that are protobuf `bytes` and are **hex** in OTLP/JSON. Protobuf's JSON mapping
#: renders every `bytes` field as base64, and a trace id in that alphabet cannot be looked up.
_HEX_FIELDS = frozenset({"traceId", "spanId", "parentSpanId"})

#: How many items of each export to render. 0 is off and the default. A number rather than a flag:
#: the useful request is "show me three spans", never the whole batch.
_payload_items = 0


def set_payload_rendering(items: int) -> int:
    """How many items per export to render as OTLP/JSON. Returns what is now set."""
    global _payload_items
    _payload_items = max(0, int(items))
    return _payload_items


def _to_otlp_json(document: Any) -> Any:
    """Turn protobuf's JSON into **OTLP's** JSON: the identifiers as hex.

    Walks the whole document: ids sit on spans, span links, log records and metric exemplars, so
    the rule is the field's shape, not a list of paths (`LESSONS.md` §1).
    """
    if isinstance(document, dict):
        return {
            key: (
                base64.b64decode(value).hex()
                if key in _HEX_FIELDS and isinstance(value, str)
                else _to_otlp_json(value)
            )
            for key, value in document.items()
        }
    if isinstance(document, list):
        return [_to_otlp_json(item) for item in document]
    return document


def payload_as_json(signal: str, args: tuple[Any, ...], items: int) -> str:
    """The batch as OTLP/JSON, rendered as a collector renders it: hex ids, integer enums.

    Rendered through the exporter's own encoder. Metrics arrive as a tree with no first-`n`, so
    they are handed over whole. Never raises: this runs inside the exporter.
    """
    if not args or items <= 0:
        return ""
    try:
        from google.protobuf.json_format import MessageToJson

        module_name, function_name = _ENCODERS[signal]
        encode = getattr(importlib.import_module(module_name), function_name)
        batch = args[0]
        with contextlib.suppress(TypeError):  # metrics are a tree: not sliceable, sent whole
            batch = batch[:items]
        rendered = MessageToJson(encode(batch), use_integers_for_enums=True)
        # Compact and on one line: every log line is one event, and the escaped copy sits inside
        # another JSON string. Pretty-printing is the reader's `jq`.
        return str(json.dumps(_to_otlp_json(json.loads(rendered)), separators=(",", ":")))
    except Exception:  # noqa: BLE001 — never the reason an export fails
        return ""


def show_payload(signal: str, args: tuple[Any, ...]) -> None:
    """Log the batch as OTLP/JSON, when somebody asked for it.

    Local-only, like every `otel` line (`FRD-617` §3.2): a rendered log batch that was itself
    exported would join the next batch, doubling per export.
    """
    if _payload_items <= 0:
        return
    rendered = payload_as_json(signal, args, _payload_items)
    if rendered:
        # Imported here: `aira_common.logging` imports this package at module scope.
        from aira_common.logging import get_logger

        get_logger("aira_common.observability").info(
            "otel_payload",
            signal=signal,
            shown=_payload_items,
            payload=rendered,
            local_only=True,
        )
