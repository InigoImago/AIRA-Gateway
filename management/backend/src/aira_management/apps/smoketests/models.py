"""A catalogue of questions, put to a use case's pipeline, and a human verdict on each answer.

`FRD-504` asks for evidence about what comes back from the models, where every other control
governs access. Since `ADR-0020` a run names a **use case** and travels its own pipeline, so the
questions exercise the filter, router and redactor somebody configured; testing a *model* is a use
case whose pipeline starts at it and does nothing else.

**A person reads each answer and rates it** (owner's decision). Whether an answer is acceptable is
a judgement, and a substring match that pretends otherwise produces a number nobody should trust.
Stored in the control plane because it is a governance artefact that outlives any gateway instance.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

#: The use case the demo seeds for **model** evaluation (`ADR-0020`). An ordinary use case in every
#: respect; named here only so the seed and its tests agree on a slug.
DEMO_MODEL_TEST_USE_CASE = "smoke-test"


class TestCase(models.Model):
    """One question in the catalogue.

    **One flat list, deliberately** (owner's decision): every model is asked every question, which
    is what makes the catalogue a standard. `topic` is a label saying what a question tests;
    nothing branches on it or groups by it.
    """

    topic = models.CharField(max_length=120)
    prompt = models.TextField()
    #: What a good answer looks like, in a sentence. Shown to the person rating — never matched
    #: against.
    expectation = models.TextField(blank=True)
    #: Position in the catalogue, the order a run walks it — **and the key the seed upserts on**,
    #: so renaming a question corrects it in place instead of creating a second one.
    position = models.PositiveIntegerField(default=0)
    #: No longer part of the standard but already answered. **Retired rather than deleted**: its
    #: answers were judged against its wording, and deleting it would take those verdicts with it.
    retired = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            # One question per position; retired ones keep their old position and are excluded.
            models.UniqueConstraint(
                fields=["position"],
                condition=models.Q(retired=False),
                name="unique_position_in_catalogue",
            )
        ]

    def __str__(self) -> str:
        return f"{self.topic}: {self.prompt[:40]}"


class TestRun(models.Model):
    """The catalogue, put to one use case's pipeline, at one time.

    Both identifying fields are **strings** rather than foreign keys: a run is evidence about what
    happened, and must survive the model leaving the catalog and the use case being deleted.
    """

    #: The model this run **entered the pipeline at**, chosen per run and bounded by what is
    #: released to the use case (`FRD-308`). Not necessarily the model that answered: a
    #: `model_route` step may send the request elsewhere.
    model = models.CharField(max_length=128)
    #: **What the run is about**: the use case whose pipeline was exercised, and whose traffic —
    #: priced, budgeted, rate-limited, audited (`FRD-504` §5) — the run is.
    use_case = models.CharField(max_length=64)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="test_runs"
    )

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"catalogue against {self.use_case}"


class Verdict(models.TextChoices):
    """**Unrated is a state, not a missing value** — a run nobody has read is not "0 failed"."""

    UNRATED = "unrated", "not yet rated"
    PASS = "pass", "acceptable"
    FAIL = "fail", "not acceptable"
    UNCLEAR = "unclear", "cannot tell"


class TestResult(models.Model):
    """What one model said to one question, and what a person made of it."""

    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="results")
    case = models.ForeignKey(TestCase, on_delete=models.PROTECT, related_name="results")
    #: The answer, as it came back. Empty until the run reaches this case.
    response = models.TextField(blank=True)
    #: Set when the request itself failed — a refusal, a timeout, an upstream error. Distinct from
    #: an empty answer, which is the model behaving oddly.
    error = models.CharField(max_length=255, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    verdict = models.CharField(
        max_length=16, choices=Verdict.choices, default=Verdict.UNRATED, db_index=True
    )
    note = models.TextField(blank=True)
    rated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="test_ratings",
    )
    rated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["case__position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["run", "case"], name="uq_result_per_case_per_run")
        ]

    def __str__(self) -> str:
        return f"{self.case.topic} -> {self.verdict}"
