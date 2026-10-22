"""Everything a request goes through that is not about a particular wire format (`ADR-0010`).

Both API surfaces share this layer, so a control cannot exist on one surface and be missing on the
other. A surface keeps only three things: parsing its wire format, rendering its error envelope,
and its routes.

    context       request-scoped lookups: attribution, catalogue, use-case record, provenance
    body          the bounded body reader
    controls      the individual pre-dispatch controls
    pipeline_run  the use case's pipeline, and the model calls it made
    prepare       the pre-dispatch sequence, in its guaranteed order
    accounting    settle and record the request, however it ended
    streaming     the same for a streamed answer, however its connection ended
    answers       notices, the structured-output check, response headers
    refusals      which exceptions are refusals, and their statuses and outcomes
"""

from aira_gateway.api.serving.accounting import (
    Accounting,
    accounting,
    elapsed_ms,
    record_refusal,
)
from aira_gateway.api.serving.answers import (
    StreamedNotice,
    annotate,
    check_structured_result,
    deprecation_headers,
    notice_outcome,
    with_notices,
    withheld_because,
)
from aira_gateway.api.serving.body import ensure_body_is_encodable, json_body
from aira_gateway.api.serving.context import (
    attribution_of,
    catalog_of,
    declared_model,
    declared_routing,
    embedding_bounds,
    provenance,
    released_for,
    released_models,
    schema_bounds,
    served_models,
    use_case_of,
    use_case_record,
)
from aira_gateway.api.serving.controls import (
    EMBEDDING_METHODS,
    NotAReranker,
    ToolsNotPermitted,
    check_declaration,
    enforce_pre_dispatch,
    estimate,
    guard_before_work,
    refuse_unless_live,
    resolve_reasoning,
)
from aira_gateway.api.serving.pipeline_run import (
    _rewritten_body,
    record_pipeline_calls,
    run_pipeline,
    run_pipeline_over_texts,
)
from aira_gateway.api.serving.prepare import (
    Prepared,
    fitting,
    prepare_for_dispatch,
    rank,
    requirements_for,
    resolve_direct_target,
)
from aira_gateway.api.serving.refusals import (
    REFUSALS,
    caller_message,
    refusal_metadata,
    refusal_outcome,
    refusal_reason,
    stream_failure_status,
    upstream_status,
)
from aira_gateway.api.serving.streaming import AccountedStream, StreamAccounting

__all__ = [
    "EMBEDDING_METHODS",
    "REFUSALS",
    "AccountedStream",
    "Accounting",
    "Prepared",
    "StreamAccounting",
    "NotAReranker",
    "StreamedNotice",
    "ToolsNotPermitted",
    "_rewritten_body",
    "accounting",
    "annotate",
    "attribution_of",
    "catalog_of",
    "check_declaration",
    "check_structured_result",
    "declared_model",
    "declared_routing",
    "deprecation_headers",
    "elapsed_ms",
    "embedding_bounds",
    "enforce_pre_dispatch",
    "ensure_body_is_encodable",
    "estimate",
    "fitting",
    "guard_before_work",
    "json_body",
    "notice_outcome",
    "prepare_for_dispatch",
    "rank",
    "provenance",
    "record_pipeline_calls",
    "record_refusal",
    "caller_message",
    "refusal_metadata",
    "refusal_outcome",
    "refusal_reason",
    "refuse_unless_live",
    "released_for",
    "released_models",
    "requirements_for",
    "resolve_direct_target",
    "resolve_reasoning",
    "run_pipeline",
    "run_pipeline_over_texts",
    "schema_bounds",
    "served_models",
    "stream_failure_status",
    "upstream_status",
    "use_case_of",
    "use_case_record",
    "with_notices",
    "withheld_because",
]
