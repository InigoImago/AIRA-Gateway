"""Retiring and purging a use case, each written once (`FRD-607`).

The API and the test-suite clean-up command both purge, and two copies of a sequence that has to
remove evidence-bearing rows is two chances for one of them to forget a row.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from guardian.models import GroupObjectPermission, UserObjectPermission

from aira_management.apps.usecases.events import emit
from aira_management.apps.usecases.models import UseCase
from aira_management.apps.usecases.views.payloads import _snapshot


def retire(usecase: UseCase, *, by: str) -> bool:
    """Tombstone ``usecase`` and announce it, unless somebody already has. True if this call did.

    A conditional update rather than a read-then-save: two requests that both loaded the live row
    — a double click, a retry — must not both save, which would overwrite **who** retired it and
    send `usecase.deleted` twice, re-running the gateway's cascade. The database decides which of
    them was first.

    The event carries the use case **as it stood**, not its slug alone. It shares the slug's key
    with `usecase.upserted` on a compacted topic, so it is the last record of the use case that
    compaction keeps, and a gateway rebuilding its read-model from the topic writes the tombstone —
    the retention promise with it — from this event (`FRD-204` FR-6).
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
    """
    with transaction.atomic():
        slug = usecase.slug
        content_type = ContentType.objects.get_for_model(UseCase)
        for model in (UserObjectPermission, GroupObjectPermission):
            model.objects.filter(content_type=content_type, object_pk=str(usecase.pk)).delete()
        usecase.delete()
        emit("usecase.purged", {"slug": slug})
