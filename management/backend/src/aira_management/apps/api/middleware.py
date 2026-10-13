"""A NUL character is refused where it enters, before any view reads it.

PostgreSQL's text types cannot hold U+0000: a NUL that reaches a query — a slug, a member's name, a
key prefix, a search term — comes back from the driver as a `DataError`, which is a `500` for a
value the caller typed. SQLite stores it, so the hermetic suite cannot see the difference. One
check here covers every path segment and query value of every route, including the ones written
next; bodies are checked where they are parsed (`apps.api.parsers`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest, HttpResponseBase, JsonResponse

from aira_management.apps.api.exceptions import envelope

NUL = "\x00"

#: The refusal, in the envelope every other refusal of this API uses.
NUL_REFUSAL = "The address contains a NUL character (%00), which no value here can hold."


def carries_nul(request: HttpRequest) -> bool:
    """Whether the decoded path or any query name or value contains a NUL."""
    if NUL in request.path_info:
        return True
    return any(
        NUL in name or any(NUL in value for value in values) for name, values in request.GET.lists()
    )


class RefuseNulCharacters:
    """Answer `400` to a request whose address carries a NUL; pass everything else through.

    Both a sync and an async path, so the ASGI stack stays async through it (Django's documented
    pattern for a middleware that has nothing to await itself).
    """

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
        if carries_nul(request):
            return _refusal()
        response: HttpResponseBase = self.get_response(request)
        return response

    async def __acall__(self, request: HttpRequest) -> HttpResponseBase:
        if carries_nul(request):
            return _refusal()
        response: HttpResponseBase = await self.get_response(request)
        return response


def _refusal() -> JsonResponse:
    return JsonResponse(envelope("invalid_argument", NUL_REFUSAL), status=400)
