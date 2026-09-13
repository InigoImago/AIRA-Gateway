"""Serializers for the smoke-test catalogue, runs and ratings."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rest_framework import serializers

from aira_management.apps.smoketests.models import TestCase, TestResult, TestRun


def verdict_counts(results: Iterable[TestResult]) -> dict[str, int]:
    """How a run stands. **`unrated` is reported**, never folded into a total: a run nobody has
    read yet is not a run with no failures."""
    counts = {"total": 0, "unrated": 0, "pass": 0, "fail": 0, "unclear": 0}
    for result in results:
        counts["total"] += 1
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    return counts


class TestCaseSerializer(serializers.ModelSerializer[TestCase]):
    class Meta:
        model = TestCase
        fields = ["id", "topic", "prompt", "expectation", "position", "retired"]


class TestResultSerializer(serializers.ModelSerializer[TestResult]):
    topic = serializers.CharField(source="case.topic", read_only=True)
    prompt = serializers.CharField(source="case.prompt", read_only=True)
    expectation = serializers.CharField(source="case.expectation", read_only=True)
    rated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TestResult
        fields = [
            "id",
            "run",
            "case",
            "topic",
            "prompt",
            "expectation",
            "response",
            "error",
            "latency_ms",
            "verdict",
            "note",
            "rated_by_name",
            "rated_at",
        ]
        # A rating's author is whoever is signed in — never a field the caller may set.
        read_only_fields = ["run", "case", "rated_by_name", "rated_at"]

    def get_rated_by_name(self, result: TestResult) -> str:
        return getattr(result.rated_by, "username", "") or ""


class TestRunSerializer(serializers.ModelSerializer[TestRun]):
    requested_by_name = serializers.SerializerMethodField()
    counts = serializers.SerializerMethodField()

    class Meta:
        model = TestRun
        fields = [
            "id",
            "model",
            "use_case",
            "started_at",
            "finished_at",
            "requested_by_name",
            "counts",
        ]
        #: `model` is **writable** — the caller picks where the run enters the pipeline — and
        #: bounded by `TestRunViewSet._entry_model` to what is released to the use case, which
        #: this serializer has no business resolving.
        read_only_fields = ["started_at", "requested_by_name", "counts"]
        #: Optional on the wire: "whichever" is legitimate, and the view then takes the first
        #: released model. The column itself is required.
        extra_kwargs = {"model": {"required": False, "allow_blank": True}}

    def get_requested_by_name(self, run: TestRun) -> str:
        return getattr(run.requested_by, "username", "") or ""

    def get_counts(self, run: TestRun) -> dict[str, Any]:
        return verdict_counts(run.results.all())
