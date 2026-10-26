"""Serializer + validation for budgets (FRD-400, FRD-407)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from rest_framework import serializers

from aira_common.permissions import Permission
from aira_management.apps.budgets.models import Budget
from aira_management.apps.usecases.access import may_call, may_manage
from aira_management.config.runtime import get_settings
from aira_management.rbac import permissions_of

#: Said for each limit that is zero. The gateway's ledger refuses at `usage >= limit`, so zero
#: refuses every request — the accident the rate limit refuses too.
ZERO_LIMIT = (
    "A limit of zero refuses every request. Leave the field empty for no limit of this kind, or "
    "switch the budget off."
)


class BudgetSerializer(serializers.ModelSerializer[Budget]):
    """A use case's budget, as `/use-cases/<slug>/budgets/` writes it (`FRD-400`).

    A person's own budget names them, and under strict data mode the name is for the use case's
    administrators, who set it, for the person and for whoever sees people; every other reader
    reads the row as another member's (`FRD-407`, as a key's owner in `FRD-627` FR-15).
    """

    #: Declared rather than generated: a model default makes DRF treat the field as optional and
    #: leave it **out** of the validated data, and both upserts key on it. Omitted means the
    #: model's own default.
    period = serializers.ChoiceField(choices=Budget.PERIOD_CHOICES, default=Budget.MONTH)
    subject_withheld = serializers.SerializerMethodField()
    #: Whether the person a budget names can call the use case, as the gateway would decide it —
    #: `None` where nobody is named, where their account is gone, or where the name is withheld.
    #: A budget for somebody who left binds nobody, and says so rather than looking configured.
    reaches = serializers.SerializerMethodField()

    #: The scopes this route writes, and what to do instead with any other.
    SCOPES: tuple[str, ...] = (Budget.USE_CASE, Budget.EACH_MEMBER, Budget.MEMBER)
    OTHER_SCOPE = (
        "An installation budget belongs to no use case: set it on /api/v1/installation-budgets/."
    )

    class Meta:
        model = Budget
        fields = [
            "id",
            "scope",
            "subject",
            "subject_withheld",
            "reaches",
            "period",
            "limit_cost",
            "limit_tokens",
            "limit_requests",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def _withheld(self, budget: Budget) -> bool:
        """Whether the person this budget names is withheld from the reader (`FRD-627`).

        Only under strict data mode, and only from somebody who neither administers the use case
        (and so set it), nor is the person, nor may see people. Without a reader in the context
        nothing is withheld: the answer to a save goes to the administrator who made it.
        """
        viewer = self.context.get("viewer")
        if budget.scope != Budget.MEMBER or viewer is None or not get_settings().strict_data_mode:
            return False
        if budget.use_case is not None and may_manage(viewer, budget.use_case):
            return False
        if Permission.INCIDENT_INVESTIGATE in permissions_of(viewer):
            return False
        return bool(viewer.get_username() != budget.subject)

    def get_subject_withheld(self, budget: Budget) -> bool:
        return self._withheld(budget)

    def get_reaches(self, budget: Budget) -> bool | None:
        if budget.scope != Budget.MEMBER or budget.use_case is None or self._withheld(budget):
            return None
        person = get_user_model().objects.filter(username=budget.subject).first()
        return None if person is None else may_call(person, budget.use_case)

    def to_representation(self, instance: Budget) -> dict[str, Any]:
        data = super().to_representation(instance)
        if data["subject_withheld"]:
            data["subject"] = None
        return data

    def validate_scope(self, scope: str) -> str:
        """The check constraint holds the same rule underneath; this says it while somebody can
        still act on it, rather than as an `IntegrityError`."""
        if scope not in self.SCOPES:
            raise serializers.ValidationError(self.OTHER_SCOPE)
        return scope

    def validate_limit_cost(self, value: Decimal | None) -> Decimal | None:
        return _not_zero(value)

    def validate_limit_tokens(self, value: int | None) -> int | None:
        return _not_zero(value)

    def validate_limit_requests(self, value: int | None) -> int | None:
        return _not_zero(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs.get("scope") == Budget.MEMBER:
            # Which account the name belongs to, and whether it can reach the use case, is the
            # route's to check: it has the use case.
            named = str(attrs.get("subject") or "").strip()
            if not named:
                raise serializers.ValidationError(
                    {"subject": ["Name the person this budget is for, by their username."]}
                )
            attrs["subject"] = named
        else:
            # A shared or per-head row names nobody, so a subject is meaningless: cleared rather
            # than stored (the uniqueness constraint would otherwise admit the same rule twice) and
            # not refused, because a client still sending it asks for nothing harmful.
            attrs["subject"] = ""
        if (
            attrs.get("limit_cost") is None
            and attrs.get("limit_tokens") is None
            and attrs.get("limit_requests") is None
        ):
            raise serializers.ValidationError("Set at least one limit: cost, tokens, or requests.")
        return attrs


class InstallationBudgetSerializer(BudgetSerializer):
    """The installation's own budget (`FRD-610`), on its own route: the one scope that belongs to
    no use case."""

    SCOPES = (Budget.INSTALLATION,)
    OTHER_SCOPE = "This route sets the installation's own budget, and nothing else."


def _not_zero[N: (int, Decimal)](value: N | None) -> N | None:
    if value is not None and value <= 0:
        raise serializers.ValidationError(ZERO_LIMIT)
    return value
