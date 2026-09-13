"""Paging and searching, at the server (`FRD-208`).

A page is a page the database produced: the use-case list computes object permissions per row
(`access.py`), so paging in the browser would leave every one of those computations happening.

- **The envelope carries the total**, so a reader can tell a filtered list from a whole one.
- **A search is a filter, not a ranking**: ``?q=`` is a case-insensitive substring over the
  fields a person would type, so the order stays predictable.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from django.db.models import Q, QuerySet
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response

#: What one page holds — the console's own page size, so the two agree on what "next" means.
PAGE_SIZE = 25
#: An upper bound, so a caller cannot ask for the whole table; a script walks the pages.
MAX_PAGE_SIZE = 200


class ConsolePagination(PageNumberPagination):
    """``?page=`` and ``?page_size=``, with the total in the body."""

    page_size = PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE

    def get_paginated_response(self, data: Any) -> Response:
        # DRF types these as optional because they are set by `paginate_queryset`, which has
        # always run by the time this is called.
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
    """Filter ``queryset`` by ``?q=`` across ``fields`` — substring, case-insensitive, in the DB.

    An empty or whitespace-only ``q`` is not a filter; "nothing matches the empty string" would
    read as a broken screen.
    """
    needle = str(request.query_params.get("q", "")).strip()
    if not needle or not fields:
        return queryset
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__icontains": needle})
    return queryset.filter(condition)
