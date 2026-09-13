"""The showcase's content (FRD-130): use cases, memberships, limits, rules and pipelines.

Chosen so the differences between the demo accounts are visible rather than described:

    admin    Global Administrator: every use case, and the only one who may price a model
    itgov    IT Steuerung: every use case and the whole spend report — and may change none of it
    itsec    IT Security: the governance view without the commercial one
    ucadmin  administers the use cases it was granted, and cannot see `personalwesen` at all
    ucuser   a member of two use cases, read-only

The tables the seed consumes entry by entry (it pops keys) are functions returning fresh lists.
`tools/tests` read `CHAT_MODEL`, `RELEASES` and `MEMBERSHIPS` from this file's source, so those stay
literal module-level assignments.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from aira_common.anomalies import RuleAction, RuleKind, RuleTarget
from aira_management.apps.budgets.models import Budget
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.usecases.models import UseCaseMembership

#: The chat model the local endpoint serves. From the environment, so a deployment running a
#: different one still gets a coherent demo.
CHAT_MODEL = os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", "qwen3:0.6b")

#: Which models each demo use case is released (`FRD-308`); a slug missing here gets every approved
#: model. **Seed data, not a default**: a real use case starts with nothing released, and a demo
#: whose requests were all refused would teach that rule backwards. `entwicklung` shows the choice.
RELEASES: dict[str, list[str]] = {"entwicklung": [CHAT_MODEL]}

#: Who is in what. `ucadmin` deliberately does **not** administer `personalwesen`, which shows that
#: the scoping is real and not a filter in the frontend.
MEMBERSHIPS: dict[str, list[tuple[str, str]]] = {
    "kundenservice": [("ucadmin", UseCaseMembership.ADMIN), ("ucuser", UseCaseMembership.USER)],
    "entwicklung": [("ucadmin", UseCaseMembership.ADMIN)],
    # The key is issued to whoever owns the use case (`FRD-604`): the name beside an agent's
    # traffic says who is accountable for the credential, not who typed the request.
    "coding-assistant": [("ucadmin", UseCaseMembership.ADMIN), ("ucuser", UseCaseMembership.USER)],
    # Owned by the global administrator, not an oversight role: IT Steuerung gets every figure and
    # no write anywhere (PRD §154).
    "personalwesen": [("admin", UseCaseMembership.ADMIN)],
}


def _use_cases() -> list[dict[str, Any]]:
    """Four use cases, each chosen to make one governance decision visible."""
    return [
        {
            "slug": "kundenservice",
            "name": "Kundenservice",
            "description": (
                "Antworten für den First-Level-Support. Prompts werden gespeichert, damit ein "
                "Vorfall nachvollziehbar bleibt — mit der kürzesten Aufbewahrung, die dafür reicht."
            ),
            "processing_notes": (
                "Kundendaten möglich. Aufbewahrung 7 Tage; Inhaltsmaskierung ist noch nicht "
                "implementiert (FRD-406), was hier bewusst offen dokumentiert ist."
            ),
            "store_payloads": True,
            "retention_days": 7,
        },
        {
            "slug": "entwicklung",
            "name": "Entwicklung",
            "description": (
                "Code- und Recherchefragen aus dem Engineering. Höheres Volumen, deshalb eine "
                "Ratenbegrenzung statt eines engen Budgets."
            ),
            "processing_notes": "Keine personenbezogenen Daten erwartet.",
            "store_payloads": True,
            "retention_days": 30,
        },
        {
            "slug": "coding-assistant",
            "name": "Coding Assistant",
            "description": (
                "Agentische Coding-Unterstützung. **Function Calling ist eingeschaltet** — der "
                "einzige Use Case im Demo, der es braucht, und der Grund, warum der Schalter "
                "standardmäßig aus ist. Eine Anweisung eines Menschen wird hier zu vielen "
                "Modellaufrufen, also sind Limit und Budget dafür bemessen und nicht für einen "
                "Chatbot."
            ),
            "processing_notes": (
                "Quellcode und Dateipfade sind Inhalt: sie stehen in gespeicherten Prompts. "
                "Prompt-Caching ist bewusst **aus** — das lokale Modell meldet keine gecachten "
                "Token, ein eingeschalteter Schalter ohne Wirkung wäre eine Anzeige, die nichts "
                "anzeigt (FRD-125). Einschalten, sobald ein Modell dahintersteht, das cachen kann."
            ),
            "store_payloads": True,
            "retention_days": 7,
            # The one use case in the demo that needs tools, and so the one where they are on.
            "tools_enabled": True,
        },
        {
            "slug": "personalwesen",
            "name": "Personalwesen",
            "description": (
                "Textentwürfe für HR. **Speicherung ist abgeschaltet** — die Zahlen werden weiter "
                "erfasst, die Prompts nicht."
            ),
            "processing_notes": (
                "Besondere Kategorien personenbezogener Daten möglich. store_payloads=false: es "
                "wird nichts geschrieben, was später maskiert werden müsste."
            ),
            "store_payloads": False,
            "retention_days": 1,
        },
    ]


def _budgets() -> list[dict[str, Any]]:
    """A spread across every axis the UI offers, **calibrated against the demo traffic**.

    Each is set so one run of `tools/demo_traffic.py` moves its bar into the middle of its range and
    a second reaches it — a limit somebody can reach by clicking rather than take on trust.
    """
    return [
        # Money, monthly, whole use case — the headline control. ~40% after one traffic run.
        {
            "use_case": "kundenservice",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.MONTH,
            "limit_cost": Decimal("0.000300"),
        },
        # A per-head cap under it. Twice the observed maximum of one traffic run's six requests
        # here (50 600–129 400 nanos, the model's verbosity varies), so it never fires mid-run on
        # the injection and embedding rows the demo exists to show — and stays the tighter of the
        # two per person.
        {
            "use_case": "kundenservice",
            "scope": Budget.EACH_MEMBER,
            "subject": "",
            "period": Budget.DAY,
            "limit_cost": Decimal("0.000250"),
        },
        # Tokens rather than money, for a team that thinks in tokens.
        {
            "use_case": "entwicklung",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.MONTH,
            "limit_tokens": 1_200,
        },
        # A request count, which is the one a runaway loop trips first.
        {
            "use_case": "entwicklung",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.DAY,
            "limit_requests": 20,
        },
        # The assistant's request count — the figure an agent moves fastest. Generous enough for a
        # real session, small enough to reach deliberately.
        {
            "use_case": "coding-assistant",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.DAY,
            "limit_requests": 500,
        },
        {
            "use_case": "personalwesen",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.MONTH,
            "limit_cost": Decimal("0.000500"),
        },
    ]


def _rate_limits() -> list[dict[str, Any]]:
    return [
        {
            "use_case": "entwicklung",
            "scope": RateLimit.USE_CASE,
            "subject": "",
            "limit_rpm": 60,
            "burst": 20,
        },
        {
            "use_case": "entwicklung",
            "scope": RateLimit.EACH_MEMBER,
            "subject": "",
            "limit_rpm": 20,
            "burst": 5,
        },
        # **Sized for an assistant**: one human instruction becomes several model calls and an
        # agent arrives in bursts, so a chatbot's limit would trip in the first minute.
        {
            "use_case": "coding-assistant",
            "scope": RateLimit.USE_CASE,
            "subject": "",
            "limit_rpm": 240,
            "burst": 60,
        },
    ]


def _anomaly_rules() -> list[dict[str, Any]]:
    """Rules worth looking at, not rules that fire (`FRD-500`).

    They show the range of the vocabulary — one global rule, three per use case, four kinds, two
    targets — and are `alert` or `throttle`, never `block`: a demo that stopped somebody's traffic
    on a first run would teach that detection is something to switch off. Thresholds are
    calibrated against the demo traffic, like the budgets.
    """
    return [
        {
            # Global: IT Security's to author, everybody's to read, nobody in a use case can turn
            # it off.
            "use_case": None,
            "name": "A caller being refused over and over",
            "kind": RuleKind.REFUSAL_RATE,
            "window_minutes": 15,
            "threshold": 40,
            "min_sample": 20,
            "target": RuleTarget.SUBJECT,
            "action": RuleAction.ALERT,
        },
        {
            "use_case": "entwicklung",
            "name": "An API key that suddenly costs much more",
            "kind": RuleKind.SPEND_SPIKE,
            "window_minutes": 60,
            # A ratio, not a number: a fixed figure is a budget, and there is one (`FRD-500`).
            "threshold": 300,
            "min_sample": 10,
            "target": RuleTarget.CREDENTIAL,
            "action": RuleAction.ALERT,
        },
        {
            "use_case": "entwicklung",
            "name": "A machine nobody has seen before",
            "kind": RuleKind.NEW_SOURCE_IP,
            "window_minutes": 60,
            "threshold": 1,
            "min_sample": 1,
            "target": RuleTarget.SUBJECT,
            "action": RuleAction.ALERT,
        },
        {
            "use_case": "kundenservice",
            "name": "Prompts the filter keeps objecting to",
            "kind": RuleKind.BLOCKED_PROMPT_RATE,
            "window_minutes": 30,
            "threshold": 25,
            "min_sample": 8,
            "target": RuleTarget.SUBJECT,
            # The one rule that acts, and it slows a caller rather than stopping them. A throttle
            # without a rate is not a decision (`FRD-503`).
            "action": RuleAction.THROTTLE,
            "action_minutes": 30,
            "throttle_rpm": 5,
        },
    ]


def _pipelines() -> dict[str, dict[str, Any]]:
    """One heuristic injection filter — cheap and deterministic.

    The LLM classifier is **not** seeded: against a 0.6B model it answers INJECTION to everything
    (`FRD-125` §9), so the demo would show a filter blocking innocent questions. `entwicklung`'s
    narrower scope is a release (`RELEASES`), enforced at every hop (`FRD-308`).
    """
    return {
        "kundenservice": {
            "steps": [
                {
                    "type": "injection_filter",
                    "config": {"mode": "heuristic", "action": "block", "scope": "system_user"},
                }
            ],
            "fallback_models": [],
        },
    }
