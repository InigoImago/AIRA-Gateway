"""Serializers for the smoke-test catalogue, runs and ratings."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import TestBattery, TestCase, TestResult, TestRun


class TestCaseSerializer(serializers.ModelSerializer[TestCase]):
    class Meta:
        model = TestCase
        fields = ["id", "battery", "topic", "prompt", "expectation", "position", "retired"]


class TestBatterySerializer(serializers.ModelSerializer[TestBattery]):
    cases = TestCaseSerializer(many=True, read_only=True)
    case_count = serializers.SerializerMethodField()

    def get_case_count(self, battery: TestBattery) -> int:
        """How many questions the standard *currently* asks — retired ones excluded.

        Counting them would make the picker promise a longer run than it performs, and the number
        beside a battery is the one somebody uses to decide whether they have time for it.
        """
        return battery.cases.filter(retired=False).count()

    class Meta:
        model = TestBattery
        fields = ["id", "name", "description", "cases", "case_count", "updated_at"]


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
        # A rating names its author, and the author is whoever is signed in — never a field a
        # caller may set. An attributable judgement the judged party could write is not one.
        read_only_fields = ["run", "case", "rated_by_name", "rated_at"]

    def get_rated_by_name(self, result: TestResult) -> str:
        return getattr(result.rated_by, "username", "") or ""


class TestRunSerializer(serializers.ModelSerializer[TestRun]):
    battery_name = serializers.CharField(source="battery.name", read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    counts = serializers.SerializerMethodField()

    class Meta:
        model = TestRun
        fields = [
            "id",
            "battery",
            "battery_name",
            "model",
            "use_case",
            "started_at",
            "finished_at",
            "requested_by_name",
            "counts",
        ]
        read_only_fields = ["started_at", "requested_by_name", "counts"]

    def get_requested_by_name(self, run: TestRun) -> str:
        return getattr(run.requested_by, "username", "") or ""

    def get_counts(self, run: TestRun) -> dict[str, Any]:
        """How the run stands. **`unrated` is reported**, never folded into a total.

        A run nobody has read yet is not a run with no failures, and the difference is the whole
        reason somebody opens this screen.
        """
        counts: dict[str, Any] = {"total": 0, "unrated": 0, "pass": 0, "fail": 0, "unclear": 0}
        for result in run.results.all():
            counts["total"] += 1
            counts[result.verdict] = counts.get(result.verdict, 0) + 1
        return counts
