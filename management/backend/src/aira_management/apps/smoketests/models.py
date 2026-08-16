"""A catalogue of questions, put to a use case's pipeline, and a human verdict on each answer.

`FRD-504` asked for evidence about how the **models** behave, as opposed to how callers behave —
every other control in AIRA governs access, and none of them says anything about what comes back.

**`ADR-0020` moved the subject of a run from a model to a use case.** The catalogue is unchanged
and still belongs to Global Administrators and IT Security; what changed is what a run is *about*.
A run names a use case and travels that use case's own pipeline, so the questions exercise the
filter, the router and the redactor somebody configured — which is what `FRD-504` §5.3 wanted from
the start and never got, because every run went to one seeded use case whose pipeline was empty.

Testing a *model* is then a use case: IT Security makes one, releases the models to it, and points
its pipeline's start model at the one under evaluation. Nothing about model testing is a special
path; it is the general mechanism aimed at one model.

The rest of the shape is the owner's and is unchanged: **a person reads each answer and rates it**.
Deliberately not an automatic pass/fail on a substring — whether an answer is acceptable is a
judgement, and a regex that pretends otherwise produces a number nobody trusts and everybody quotes.

What is stored is a governance artefact and outlives any gateway instance, which is why it lives in
the control plane rather than in the request log.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

#: The use case the demo seeds for **model** evaluation (`ADR-0020`).
#:
#: An ordinary use case in every respect: it has a released model, a pipeline that starts there, a
#: budget and a retention period, and any other use case may be run just as well. It is seeded so a
#: fresh installation has somewhere to demonstrate a model test from, and named here **only** so
#: the seed and its tests agree on a slug.
#:
#: It used to be the single place every run was attributed to, and the application branched on it.
#: That is what made the pipeline untestable and forced `_release_for_testing` to edit a governance
#: decision so a run could work at all.
DEMO_MODEL_TEST_USE_CASE = "smoke-test"


class TestCase(models.Model):
    """One question in the catalogue.

    **One flat list, deliberately.** The first version grouped questions into named batteries, and
    the owner's answer was that there is nothing to group: there is a catalogue of questions, and
    every model is asked all of them. Grouping bought nothing and cost the property that makes the
    catalogue a *standard* — with several batteries, "how does this model do" has as many answers
    as there are groups, and none of them is comparable to another model that was asked a different
    group.

    `topic` is the keyword saying what the question tests. It is a label on a row, not a
    categorisation: nothing branches on it, nothing is grouped by it, and two questions may
    perfectly well share one.
    """

    topic = models.CharField(max_length=120)
    prompt = models.TextField()
    #: What a good answer looks like, in a sentence. Shown to the person rating — not matched
    #: against, because matching is the thing this design deliberately does not do.
    expectation = models.TextField(blank=True)
    #: Position in the battery, so a run walks it in the order somebody intended — **and the key
    #: the seed upserts on**. Keying on `topic` cost two duplicate questions on 2026-08-09: the
    #: seed renamed three questions, and a rename against a name key is a *create*, so the old
    #: ones stayed with their answers attached and the battery quietly grew by two. The same
    #: lesson `FRD-208` recorded for anomaly rules, in a second place.
    position = models.PositiveIntegerField(default=0)
    #: A question that is no longer part of the standard but has already been answered.
    #:
    #: **Retired rather than deleted.** Its answers were judged by a person against the wording as
    #: it then stood; deleting the question would take those verdicts with it, and a standard whose
    #: history disappears each time it is corrected cannot show that anything improved.
    retired = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            # One question per position in the catalogue. Retired ones keep their old position
            # and are excluded, because they are history rather than part of the standard.
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

    Both identifying fields are **strings** rather than foreign keys, for the same reason: a run is
    evidence about what happened on a day, and it has to survive the model leaving the catalog and
    the use case being deleted. Deleting a declaration must not delete the finding.
    """

    #: The model the pipeline was **entered at** — its `start_model` as it stood when the run was
    #: made (`ADR-0020`).
    #:
    #: Recorded rather than looked up, because a pipeline's start model can be changed between two
    #: runs and the older run is still evidence about the configuration it actually met. It is not
    #: necessarily the model that *answered*: a `model_route` step may send the request elsewhere,
    #: and that is the pipeline doing its job.
    model = models.CharField(max_length=128)
    #: **What the run is about**: the use case whose pipeline was exercised.
    #:
    #: A run is real traffic — priced, budgeted, rate-limited and audited exactly like any other
    #: request (`FRD-504` §5) — and it is now that use case's traffic rather than a shared pot's.
    #: Which is also where the cost belongs: whoever asks for the evidence pays for it.
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
    """**Unrated is a state, not a missing value.**

    A run whose answers nobody has read yet is not a run with no failures — and a screen that
    reported it as "0 failed" would be stating something false in the most reassuring direction.
    """

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
    #: an empty answer, which is a *model* behaving oddly and is exactly what a battery is for.
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
