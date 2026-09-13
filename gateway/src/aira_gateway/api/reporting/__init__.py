"""Read-only evidence, bounded by use case: spend, the register, findings and traces (`FRD-601`).

Every endpoint resolves `visible_scope` exactly once (`test_csv_export.py` holds each to it):

    oversight role  → every use case
    otherwise       → the caller's Keycloak group memberships
    neither         → an empty answer — "there is nothing here yet", not "you may not look"

Endpoints bounded by *role* instead live in `api/incidents`. The aggregates reach no payload; the
one payload read is `traces.trace_payload`, authorised and recorded per request.

    common      the router, the visibility rule, the window, content negotiation, cursors
    spend       the spend report and the register of processing activities, JSON or CSV
    anomalies   what the detector found
    traces      request metadata, and one request's payload
"""

# Importing the endpoint modules registers their routes on `router`.
from aira_gateway.api.reporting import anomalies, spend, traces  # noqa: F401
from aira_gateway.api.reporting.anomalies import _about_this_caller
from aira_gateway.api.reporting.common import MAX_WINDOW_DAYS, router, visible_scope
from aira_gateway.api.reporting.traces import (
    INCIDENT_FIELDS,
    MAX_TRACE_PAGE,
    TRACE_FIELDS,
    TRACE_PAGE,
)

__all__ = [
    "INCIDENT_FIELDS",
    "MAX_TRACE_PAGE",
    "MAX_WINDOW_DAYS",
    "TRACE_FIELDS",
    "TRACE_PAGE",
    "_about_this_caller",
    "router",
    "visible_scope",
]
