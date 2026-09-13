"""The question-catalogue API (`FRD-504`, `ADR-0020`).

Two questions, asked separately:

- *Writing* the catalogue is bounded by role — Global Administrators and IT Security, who author a
  global anomaly rule for the same reason: it states what the installation considers acceptable.
- *Running* it is bounded per use case by `access.may_run_tests_queryset`: the gateway would accept
  this caller for that use case **and** they administer it, or they hold an installation role. A
  run spends the use case's budget a hundred prompts at a time and reads prompts §8 calls
  sensitive, so membership alone is not enough.

The console drives a run, sending each prompt through the gateway with the signed-in person's own
credentials and posting the answer back here (`FRD-504` §5). A run therefore travels the ordinary
request path — priced, budgeted, rate-limited and audited against the use case it is about.

    runnable   where the catalogue can be run, and at which models
    catalogue  the questions
    runs       runs, and the answers and verdicts in them
    export     a run as CSV
    stats      the latest run per use case
"""

from aira_management.apps.smoketests.views.catalogue import TestCaseViewSet
from aira_management.apps.smoketests.views.runnable import (
    NOTHING_RELEASED,
    TestAttributionViewSet,
    entry_models,
    runnable,
)
from aira_management.apps.smoketests.views.runs import TestResultViewSet, TestRunViewSet
from aira_management.apps.smoketests.views.stats import TestStatsViewSet

__all__ = [
    "NOTHING_RELEASED",
    "TestAttributionViewSet",
    "TestCaseViewSet",
    "TestResultViewSet",
    "TestRunViewSet",
    "TestStatsViewSet",
    "entry_models",
    "runnable",
]
