"""Paging and searching, at the server (FRD-208).

The first version of the console's search and paging was **client-side**: the whole list came down
in one response and the browser did the rest. That is defensible for a list that is small and
already fetched — and it is exactly wrong for the two lists that grow without bound, because it
fixes the only half of the problem that was never the expensive one.

The measurement that settles it: `GET /api/v1/use-cases/` on an installation with several hundred
use cases takes **seconds**, because the serializer computes object-level permissions per row
(`access.py`, `FRD-206`). Paging in the browser leaves every one of those computations happening,
every load. The reader waits exactly as long and then sees twenty-five rows.

So: a page is a page the database produced.

Two decisions worth keeping:

- **The envelope carries the total.** A list that does not say how much it is not showing reads as
  complete, and a reader who cannot see a total cannot tell a filtered list from a whole one. It is
  a `count` on every response, not a header somebody has to know to look for.
- **A search is a filter, not a ranking.** `?q=` is a case-insensitive substring over the fields a
  person would type. Nothing here scores or orders by relevance: a governance console listing use
  cases must be predictable, and "why is this one first" is a question with no good answer when the
  rows are equally valid.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from django.db.models import Q, QuerySet
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response

#: What one page holds. Matches the console's own page size so the two cannot disagree about what
#: "next" means.
PAGE_SIZE = 25
#: An upper bound, so a caller who mistyped a number cannot ask for the whole table. A script that
#: genuinely wants everything walks the pages, which is also the only way it stays correct while
#: the table is being written to.
MAX_PAGE_SIZE = 200


class ConsolePagination(PageNumberPagination):
    """``?page=`` and ``?page_size=``, with the total in the body."""

    page_size = PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE

    def get_paginated_response(self, data: Any) -> Response:
        # DRF types `page` and `request` as optional because they are only set once `paginate_
        # queryset` has run — which is the only situation this method is ever called from.
        page = self.page
        request = self.request
        assert page is not None and request is not None, (
            "get_paginated_response outside a paginated request"
        )
        return Response(
            OrderedDict(
                [
                    ("count", page.paginator.count),
                    ("page", page.number),
                    ("page_size", self.get_page_size(request) or PAGE_SIZE),
                    ("pages", page.paginator.num_pages),
                    ("results", data),
                ]
            )
        )


def apply_search(queryset: QuerySet[Any], request: Request, *fields: str) -> QuerySet[Any]:
    """Filter ``queryset`` by ``?q=`` across ``fields``.

    Substring, case-insensitive, and **at the database**: the point of moving this off the browser
    is that the rows a reader is not looking at are never built, serialised or sent.

    An empty or whitespace-only ``q`` is not a filter. Treating it as one would answer "nothing
    matches the empty string", which is both wrong and the sort of emptiness a reader reads as a
    broken screen.
    """
    needle = str(request.query_params.get("q", "")).strip()
    if not needle or not fields:
        return queryset
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__icontains": needle})
    return queryset.filter(condition)
