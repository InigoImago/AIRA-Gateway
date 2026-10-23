"""Retiring and purging a use case, each written once (`FRD-607`).

The API and the test-suite clean-up command both purge, and two copies of a sequence that has to
remove evidence-bearing rows is two chances for one of them to forget a row.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from guardian.models import GroupObjectPermission, UserObjectPermission

from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.budgets.models import Budget
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase, UseCaseGroupGrant, UseCaseMembership
from aira_management.apps.usecases.views.grants import _revoke_key
from aira_management.apps.usecases.views.payloads import _snapshot


def end_access(usecase: UseCase) -> None:
    """End every child of ``usecase`` **on that child's own key** (`FRD-607` §4.1b).

    The topics are compacted, and a use case's children live on keys of their own — a key on its
    prefix, a member on `slug|username`, a budget on its id — so the retirement on the slug's key
    protects them only while it is the last record of that key, and a purge replaces it. Once each
    child's last record ends it, a rebuild and an offset reset converge on *no access* in any
    order, whatever the slug's record is by then — a retirement, a purge, or a new use case of the
    same name.

    Keys are **revoked**, here as at the gateway: a key that stopped working is a fact the record
    keeps. Memberships, grants, budgets, limits, rules and the pipeline keep their rows — they are
    the record of who could reach the use case and how it was governed (`FRD-607` §4.4) — and grant
    nothing, because every route excludes a retired use case.
    """
    slug = usecase.slug
    for key in ApiKey.objects.filter(use_case=usecase, is_active=True).order_by("pk"):
        _revoke_key(key, slug)
    for membership in (
        UseCaseMembership.objects.filter(use_case=usecase).select_related("user").order_by("pk")
    ):
        emit("membership.removed", {"slug": slug, "username": membership.user.get_username()})
    for grant in UseCaseGroupGrant.objects.filter(use_case=usecase).order_by("pk"):
        emit("use_case_group.revoked", {"slug": slug, "group": grant.group_path})
    for budget_pk in Budget.objects.filter(use_case=usecase).values_list("pk", flat=True):
        emit("budget.deleted", {"id": budget_pk, "use_case": slug})
    for limit_pk in RateLimit.objects.filter(use_case=usecase).values_list("pk", flat=True):
        emit("ratelimit.deleted", {"id": limit_pk, "use_case": slug})
    for rule_pk in AnomalyRule.objects.filter(use_case=usecase).values_list("pk", flat=True):
        emit("anomaly_rule.deleted", {"id": rule_pk, "use_case": slug})
    if PipelineConfig.objects.filter(use_case=usecase).exists():
        emit("pipeline.deleted", {"use_case": slug})


def retire(usecase: UseCase, *, by: str) -> bool:
    """Tombstone ``usecase`` and announce it, unless somebody already has. True if this call did.

    A conditional update rather than a read-then-save: two requests that both loaded the live row
    — a double click, a retry — must not both save, which would overwrite **who** retired it and
    send `usecase.deleted` twice, re-running the gateway's cascade. The database decides which of
    them was first.

    The event carries the use case **as it stood**, not its slug alone. It shares the slug's key
    with `usecase.upserted` on a compacted topic, so it is the last record of the use case that
    compaction keeps, and a gateway rebuilding its read-model from the topic writes the tombstone —
    the retention promise with it — from this event (`FRD-204` FR-6). Then each child is ended on
    its own key (:func:`end_access`), so that a purge, which replaces this record, leaves nothing
    on the topics a replay could grant from.
    """
    with transaction.atomic():
        moment = timezone.now()
        changed = (
            UseCase.objects.live()
            .filter(pk=usecase.pk)
            .update(deleted_at=moment, deleted_by=by, updated_at=moment)
        )
        if changed:
            emit("usecase.deleted", _snapshot(usecase))
            end_access(usecase)
    if changed:
        usecase.deleted_at, usecase.deleted_by = moment, by
    return bool(changed)


def purge(usecase: UseCase) -> None:
    """Remove a retired use case for good, with the object permissions that named it.

    Guardian's object-permission rows point at an object by content type and primary key, not by a
    foreign key, so deleting the use case leaves them behind: a record of who had access to
    something that no longer exists — personal data a purge promises to remove — and a grant a
    re-used primary key would inherit. Removed in the same transaction as the row, and announced
    with `usecase.purged` so the gateway drops the last row it kept.

    **Its children are ended first, whatever its retirement did** (:func:`end_access`). A use case
    retired before retirement ended them still has records on the topics that grant, and the rows
    that name their keys go with this purge — after it, nothing could end them. For one retired
    since, the removals are sent a second time, which changes nothing.
    """
    with transaction.atomic():
        slug = usecase.slug
        end_access(usecase)
        content_type = ContentType.objects.get_for_model(UseCase)
        for model in (UserObjectPermission, GroupObjectPermission):
            model.objects.filter(content_type=content_type, object_pk=str(usecase.pk)).delete()
        usecase.delete()
        emit("usecase.purged", {"slug": slug})
