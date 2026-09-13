"""A run's evaluation as CSV (`FRD-504`)."""

from __future__ import annotations

import csv
import io

from django.http import HttpResponse

from aira_common.spreadsheet import safe_cell
from aira_management.apps.smoketests.models import TestRun

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
    with `=` and become a formula without any attacker involved.
    """
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
                safe_cell(row.rated_at.isoformat() if row.rated_at else ""),
            ]
        )
    name = f"aira-smoketest-{run.model.replace('/', '_')}-{run.started_at.date()}.csv"
    # A plain `HttpResponse`: DRF's `Response` re-encodes the body through a renderer and loses
    # the BOM.
    response = HttpResponse(
        ("﻿" + buffer.getvalue()).encode("utf-8"),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response
