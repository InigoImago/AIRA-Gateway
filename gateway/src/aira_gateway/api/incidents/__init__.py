"""Incident controls: stopping traffic by hand, and asking a model about itself.

Bounded by **role** rather than by use case, which is why none of this sits behind
`api/reporting`'s `visible_scope`. Every module registers its routes on the one `router`.

    common          the router, and reading the caller's JSON body
    content_reads   who read which stored content, for the platform roles (`FRD-622`)
    suspensions     the kill switch: list, create, lift (`FRD-503`)
    diagnostics     what both model checks share: the role gate, the upstream asked about, the row
    model_check     can this model be reached at all (`FRD-506`)
    thinking_check  does the model accept each declared thinking word (`ADR-0021`)
"""

# Importing the endpoint modules registers their routes on `router`.
from aira_gateway.api.incidents import (  # noqa: F401
    content_reads,
    model_check,
    suspensions,
    thinking_check,
)
from aira_gateway.api.incidents.common import router
from aira_gateway.api.incidents.diagnostics import MODEL_CHECK_TIMEOUT_SECONDS
from aira_gateway.api.incidents.suspensions import (
    MAX_SUSPENSION_MINUTES,
    MAX_TARGET_VALUE,
    MAX_THROTTLE_RPM,
)
from aira_gateway.api.incidents.thinking_check import MAX_LEVELS_PER_CHECK

__all__ = [
    "MAX_LEVELS_PER_CHECK",
    "MAX_SUSPENSION_MINUTES",
    "MAX_TARGET_VALUE",
    "MAX_THROTTLE_RPM",
    "MODEL_CHECK_TIMEOUT_SECONDS",
    "router",
]
