"""A run's evaluation as CSV (`FRD-504`)."""

from __future__ import annotations

import csv
import io
import re

from django.http import HttpResponse

from aira_common.clock import local_date, zone_named
from aira_common.spreadsheet import safe_cell
from aira_management.apps.smoketests.models import TestRun
from aira_management.config.runtime import get_settings

#: Everything a file name in `Content-Disposition` may carry; the rest becomes `_`. A model name is
#: whatever the catalogue holds, and a quote in it adds a second `filename`, a newline a header.
#: The console's own `csvFileName` keeps the same characters.
_UNSAFE_IN_A_FILE_NAME = re.compile(r"[^A-Za-z0-9._-]")

#: The export's columns, in order.
COLUMNS = [
    "topic",
    "prompt",
    "expectation",
    "response",
    "error",
    "latency_ms",
    "verdict",
    "note",
    "rated_by",
    "rated_at",
]


def run_as_csv(run: TestRun) -> HttpResponse:
    """The evaluation as CSV, with the conventions `FRD-602` established.

    A **BOM** so Excel reads UTF-8, **CRLF** per RFC 4180, every field quoted so a comma cannot
    shift a column — and every cell through `safe_cell`, because a model's own answer may begin
    with `=` and become a formula without any attacker involved. Its moments and its file name's
    date are on the installation's clock (`FRD-629`), as the console showed the run.
    """
    zone = zone_named(get_settings().time_zone)
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in run.results.select_related("case", "rated_by").all():
        writer.writerow(
            [
                safe_cell(row.case.topic),
                safe_cell(row.case.prompt),
                safe_cell(row.case.expectation),
                safe_cell(row.response),
                safe_cell(row.error),
                safe_cell(row.latency_ms if row.latency_ms is not None else ""),
                safe_cell(row.verdict),
                safe_cell(row.note),
                safe_cell(getattr(row.rated_by, "username", "") or ""),
                safe_cell(row.rated_at.astimezone(zone).isoformat() if row.rated_at else ""),
            ]
        )
    day = local_date(run.started_at, zone)
    name = f"aira-smoketest-{_UNSAFE_IN_A_FILE_NAME.sub('_', run.model)}-{day}.csv"
    # A plain `HttpResponse`: DRF's `Response` re-encodes the body through a renderer and loses
    # the BOM.
    response = HttpResponse(
        ("﻿" + buffer.getvalue()).encode("utf-8"),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response
