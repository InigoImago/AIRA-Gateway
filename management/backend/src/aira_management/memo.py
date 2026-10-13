"""What one request asks about its caller, answered once per request (`FRD-206`).

The use-case list asks four predicates per row, and each re-read the caller's groups, roles,
grants and memberships — fifteen-odd queries a row, thousands for a page. The answers cannot change
in a request unless the request itself changes access, so they are kept for its length:

- **Only inside a request.** `RequestMemo` opens the memo for one request and closes it after; a
  management command, a seed or a test calling a predicate directly gets every answer fresh.
- **Forgotten on every write that changes access** — a membership, a group grant, an object
  permission, a role, a user's groups — through model signals, so no writer has to remember to.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest, HttpResponseBase

_MEMO: ContextVar[dict[Hashable, Any] | None] = ContextVar("aira_request_memo", default=None)


def remembered[T](key: Hashable, compute: Callable[[], T]) -> T:
    """``compute()``, once per request for ``key``; every time outside a request."""
    memo = _MEMO.get()
    if memo is None:
        return compute()
    if key not in memo:
        memo[key] = compute()
    value: T = memo[key]
    return value


@contextmanager
def remembering() -> Iterator[None]:
    """The memo of one request, open for the duration."""
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def forget(*_args: Any, **_kwargs: Any) -> None:
    """Drop every remembered answer. A signal receiver, so it takes whatever a signal sends."""
    memo = _MEMO.get()
    if memo is not None:
        memo.clear()


class RequestMemo:
    """Open the memo for one request. Sync and async, so the ASGI stack stays async through it."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response: Callable[[HttpRequest], Any]) -> None:
        self.get_response = get_response
        self._async = iscoroutinefunction(get_response)
        if self._async:
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest) -> HttpResponseBase | Awaitable[HttpResponseBase]:
        if self._async:
            return self.__acall__(request)
        with remembering():
            response: HttpResponseBase = self.get_response(request)
        return response

    async def __acall__(self, request: HttpRequest) -> HttpResponseBase:
        # The view runs in a thread with a copy of this context, which holds the same dict.
        with remembering():
            response: HttpResponseBase = await self.get_response(request)
        return response


def watch_access_writes() -> None:
    """Connect `forget` to every write that changes what a request may have remembered: the rows
    a membership, a group grant, an object permission or a role is made of, and a user's groups."""
    from django.contrib.auth import get_user_model
    from django.db.models.signals import m2m_changed, post_delete, post_save
    from guardian.models import GroupObjectPermission, UserObjectPermission

    from aira_management.apps.roles.models import StoredRole
    from aira_management.apps.usecases.models import UseCaseGroupGrant, UseCaseMembership

    for model in (
        UseCaseMembership,
        UseCaseGroupGrant,
        UserObjectPermission,
        GroupObjectPermission,
        StoredRole,
    ):
        label = model._meta.label
        post_save.connect(forget, sender=model, dispatch_uid=f"aira.memo.save.{label}")
        post_delete.connect(forget, sender=model, dispatch_uid=f"aira.memo.delete.{label}")
    groups = get_user_model().groups.through
    m2m_changed.connect(forget, sender=groups, dispatch_uid="aira.memo.groups")
