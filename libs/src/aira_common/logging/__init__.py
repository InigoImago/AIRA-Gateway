"""Structured logging for both planes, one module per concern (`FRD-001`, `FRD-631`).

pipeline    structlog's configuration: the processors every line passes, and the two sinks
fields      the register of fields a line may carry, and the rule for one it does not name
exceptions  what of an error a line says
foreign     a library's record, rendered and exported as AIRA's own lines are
lifecycle   what a process says about itself when it starts and stops
access      one line per HTTP request, as both planes write it
"""

from aira_common.logging.exceptions import (
    database_diagnostics,
    describe_exceptions,
    exc_info_of,
    location,
    loggable,
)
from aira_common.logging.fields import (
    FIELDS,
    KNOWN,
    WITHHELD,
    Field,
    FieldKind,
    keep_known_fields,
)
from aira_common.logging.foreign import ForeignRecords, take_over_foreign_records
from aira_common.logging.pipeline import (
    EXPORT_LOGGER,
    LOCAL_ONLY_KEY,
    PROCESS_KEY,
    add_process,
    add_trace_context,
    configure_logging,
    forward_to_stdlib,
    get_logger,
    hold_back_from_export,
    process_name,
    process_named,
)

__all__ = [
    "EXPORT_LOGGER",
    "FIELDS",
    "KNOWN",
    "LOCAL_ONLY_KEY",
    "PROCESS_KEY",
    "WITHHELD",
    "Field",
    "FieldKind",
    "ForeignRecords",
    "add_process",
    "add_trace_context",
    "configure_logging",
    "database_diagnostics",
    "describe_exceptions",
    "exc_info_of",
    "forward_to_stdlib",
    "get_logger",
    "hold_back_from_export",
    "keep_known_fields",
    "location",
    "loggable",
    "process_name",
    "process_named",
    "take_over_foreign_records",
]
