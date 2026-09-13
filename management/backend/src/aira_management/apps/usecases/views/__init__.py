"""The use-case API (`FRD-202`): one viewset, composed from one mixin per resource.

viewset        CRUD over live use cases, the list's scoping, paging and search
base           the object-level predicates every mixin asks, and child-row deletion
retirement     retire, the retired list, purge (`FRD-607`)
members        members by name and grants to Keycloak groups (`FRD-209`)
api_keys       issue, list and revoke keys (`FRD-205`, `FRD-604`)
pipeline       the pre-dispatch pipeline (`FRD-300`/`303`)
budgets        usage budgets (`FRD-400`)
rate_limits    request-rate limits (`FRD-405`)
anomaly_rules  the use case's own anomaly rules (`FRD-500`)
grants         object permissions, resolving named people, revoking keys whose access ended
payloads       the event payloads the gateway's read-model is built from
"""

from aira_management.apps.usecases.views.grants import _grant, _revoke
from aira_management.apps.usecases.views.payloads import (
    _budget_payload,
    _rate_limit_payload,
    _snapshot,
)
from aira_management.apps.usecases.views.viewset import UseCaseViewSet

__all__ = [
    "UseCaseViewSet",
    "_budget_payload",
    "_grant",
    "_rate_limit_payload",
    "_revoke",
    "_snapshot",
]
