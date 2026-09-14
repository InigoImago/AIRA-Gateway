"""Gateway ORM models.

    owned        tables the gateway writes itself: keys, the audit trail, incidents, usage counters
    read_models  Management's configuration, applied from Kafka (`FRD-204`)

Importing this package registers every table on ``Base.metadata``, which the migrations rely on.
"""

from aira_gateway.db.base import Base
from aira_gateway.db.models.owned import (
    AccessSuspension,
    AnomalyEvent,
    ApiKey,
    BudgetUsage,
    PayloadAccess,
    RequestLog,
    RetentionRun,
)
from aira_gateway.db.models.read_models import (
    AnomalyRuleRead,
    BudgetRead,
    ModelRead,
    PipelineConfigRead,
    RateLimitRead,
    RoleRead,
    UseCaseGroupRead,
    UseCaseMemberRead,
    UseCaseRead,
)

__all__ = [
    "AccessSuspension",
    "AnomalyEvent",
    "AnomalyRuleRead",
    "ApiKey",
    "Base",
    "BudgetRead",
    "BudgetUsage",
    "ModelRead",
    "PayloadAccess",
    "PipelineConfigRead",
    "RateLimitRead",
    "RequestLog",
    "RetentionRun",
    "RoleRead",
    "UseCaseGroupRead",
    "UseCaseMemberRead",
    "UseCaseRead",
]
