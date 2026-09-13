"""What counts as abnormal, in one vocabulary both planes share (`FRD-500`).

Management authors and validates rules; the gateway evaluates them and acts. One definition, so the
two cannot drift — the argument that put :mod:`aira_common.roles` and :mod:`aira_common.models`
here too.

The vocabulary is **closed on purpose** (§4.1). A generic field-operator-value engine accepts rules
such as ``p95_latency > 900`` that no store here can evaluate; every kind below is implemented,
tested and explainable, and adding one is a code change with a test.
"""

from __future__ import annotations

from enum import StrEnum


class RuleKind(StrEnum):
    """What a rule watches. Each exists because a real question was asked of it."""

    #: Share of requests refused by a control. Somebody probing, or a client hammering a wall.
    REFUSAL_RATE = "refusal_rate"
    #: Share of requests that failed upstream — a model or a region failing for one use case.
    ERROR_RATE = "error_rate"
    #: Spend this window against the one before it. A *change of shape*, which a cap cannot express.
    SPEND_SPIKE = "spend_spike"
    #: The same question where nothing is priced.
    REQUEST_SPIKE = "request_spike"
    #: A credential used from an address never seen for it. A leaked key used from somewhere new.
    NEW_SOURCE_IP = "new_source_ip"
    #: Share of requests above a byte threshold — bulk extraction, which a request counter misses.
    PAYLOAD_SIZE = "payload_size"
    #: Share of requests the pipeline blocked. The filter earning its keep, or a use case attacked.
    BLOCKED_PROMPT_RATE = "blocked_prompt_rate"


class RuleAction(StrEnum):
    """What happens when a rule fires (`FRD-503` defines each precisely).

    ``ALERT`` is the default everywhere it can be, as a safety property: a detection system whose
    first setting is ``BLOCK`` blocks the wrong thing once and is then switched off forever. A rule
    is a hypothesis until somebody has watched it be right (`FRD-500` §4.3).
    """

    ALERT = "alert"
    THROTTLE = "throttle"
    BLOCK = "block"


class RuleTarget(StrEnum):
    """What the action lands on when the rule fires.

    Wrong in either direction is expensive: blocking a use case because one key misbehaved stops
    everybody, and blocking one subject while the whole use case is attacked stops nothing.
    """

    #: The caller — the identity the credential belongs to.
    SUBJECT = "subject"
    #: The credential itself: an API key prefix or an OIDC client. The right target for a leak.
    CREDENTIAL = "credential"
    #: Everything attributed to the use case.
    USE_CASE = "use_case"


#: Kinds whose threshold is a **share of requests**, in percent.
RATE_KINDS = frozenset(
    {
        RuleKind.REFUSAL_RATE,
        RuleKind.ERROR_RATE,
        RuleKind.PAYLOAD_SIZE,
        RuleKind.BLOCKED_PROMPT_RATE,
    }
)

#: Kinds whose threshold is a **multiple of the preceding window**, in percent. A ratio, because a
#: fixed number is a budget and there already is one: these catch a change of shape — €4 a day for
#: a month, then €40 today — that no cap expresses without refusing normal traffic.
RATIO_KINDS = frozenset({RuleKind.SPEND_SPIKE, RuleKind.REQUEST_SPIKE})

#: Kinds that are neither: a fact is either observed or it is not.
EVENT_KINDS = frozenset({RuleKind.NEW_SOURCE_IP})

#: Minutes. One minute is the shortest window that can hold more than a single request; a day is
#: the longest over which "the window before this one" still describes comparable traffic.
MIN_WINDOW_MINUTES = 1
MAX_WINDOW_MINUTES = 24 * 60

#: Below this many requests in the window, a rate or a ratio says nothing. One refusal out of one
#: request is 100 %, and doubling from one request to two is not a spike.
DEFAULT_MIN_SAMPLE = 20

#: Minutes an automatic action lasts. Bounded above because an automatic block that outlives the
#: incident is an outage with a good reason (`ADR-0014` §2), and below because an action shorter
#: than the window that produced it fires again immediately.
MIN_ACTION_MINUTES = 1
MAX_ACTION_MINUTES = 7 * 24 * 60

#: Kinds that need a **second** number, and what it means. A map rather than a free-form field:
#: required where listed and refused everywhere else, so it cannot become an untyped parameter.
PARAMETER_MEANING: dict[RuleKind, str] = {
    RuleKind.PAYLOAD_SIZE: "request bytes",
}

#: Actions that need a number of their own (`FRD-501` §4.4). An enum member is not a
#: specification: a new value must state what it needs that the others do not.
ACTIONS_NEEDING_RATE = frozenset({RuleAction.THROTTLE})


def needs_throttle_rate(action: RuleAction) -> bool:
    return action in ACTIONS_NEEDING_RATE


def needs_parameter(kind: RuleKind) -> bool:
    return kind in PARAMETER_MEANING


def threshold_unit(kind: RuleKind) -> str:
    """What the threshold *means* for this kind, in words a form can print.

    Here rather than in the UI: the answer is a property of the kind, and a TypeScript copy stops
    matching the day a kind is added.
    """
    if kind in RATE_KINDS:
        return "percent of requests"
    if kind in RATIO_KINDS:
        return "percent of the previous window"
    return "occurrences"


def needs_sample(kind: RuleKind) -> bool:
    """Whether a minimum sample is meaningful for this kind.

    An event kind is not a proportion: requiring twenty new-address sightings before saying so
    would be requiring twenty leaks.
    """
    return kind in RATE_KINDS or kind in RATIO_KINDS
