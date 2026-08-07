"""What counts as abnormal, in one vocabulary both planes share (`FRD-500`).

Management authors rules and validates them; the gateway evaluates them and acts. Two copies of
"which kinds exist" would drift, and the drift would be discovered in whichever plane was not
tested — the same argument that put :mod:`aira_common.roles` and :mod:`aira_common.models` here.

The vocabulary is **closed on purpose** (`FRD-500` §4.1). The tempting alternative is a rule engine
— a field, an operator, a value — and it fails on the first review: a rule reading
``p95_latency > 900`` is perfectly sensible and unimplementable against a store with no percentile
function, which `FRD-601` already ran into and said so. A closed set means every kind is one
somebody implemented, tested and can explain, and adding one is a code change with a test rather
than a configuration line that may or may not evaluate.
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

    ``ALERT`` is the default everywhere it can be, and that is a safety property rather than
    timidity: a detection system whose first setting is ``BLOCK`` blocks the wrong thing once and
    is then switched off forever. A rule is a hypothesis about what abnormal looks like until
    somebody has watched it be right (`FRD-500` §4.3).
    """

    ALERT = "alert"
    THROTTLE = "throttle"
    BLOCK = "block"


class RuleTarget(StrEnum):
    """What the action lands on when the rule fires.

    Getting this wrong is expensive in both directions: blocking a use case because one key
    misbehaved stops everybody, and blocking one subject while a whole use case is under attack
    stops nothing.
    """

    #: The caller — the identity the credential belongs to.
    SUBJECT = "subject"
    #: The credential itself: an API key prefix or an OIDC client. The right target for a leak.
    CREDENTIAL = "credential"
    #: Everything attributed to the use case.
    USE_CASE = "use_case"


#: Kinds whose threshold is a **share of requests**, expressed in percent.
RATE_KINDS = frozenset(
    {
        RuleKind.REFUSAL_RATE,
        RuleKind.ERROR_RATE,
        RuleKind.PAYLOAD_SIZE,
        RuleKind.BLOCKED_PROMPT_RATE,
    }
)

#: Kinds whose threshold is a **multiple of the preceding window**, expressed in percent.
#:
#: A ratio rather than a fixed number, because a fixed number is a budget and there is already one.
#: What these catch is a change of shape: a use case that has spent €4/day for a month spending €40
#: today is worth a look even though its cap is €100, and no cap expresses that without being
#: lowered until it refuses normal traffic.
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


#: Kinds that need a **second** number, and what it means.
#:
#: Found while implementing the engine: `payload_size` is "the share of requests above a byte
#: threshold", and the rule carried one threshold — the share. The byte figure had nowhere to live.
#: Stage A's model, serializer, API, 18 tests and six mutations were all green, because they tested
#: that a rule round-trips and nothing had yet tried to *evaluate* one. A configuration schema is
#: only proved by the code that consumes it.
#:
#: Deliberately a map rather than a free-form field: required where it is listed, refused
#: everywhere else, so it cannot quietly become a second untyped parameter. Still no operators and
#: still a closed set of kinds — a kind that wants a third number is a code change with a test.
PARAMETER_MEANING: dict[RuleKind, str] = {
    RuleKind.PAYLOAD_SIZE: "request bytes",
}


def needs_parameter(kind: RuleKind) -> bool:
    return kind in PARAMETER_MEANING


def threshold_unit(kind: RuleKind) -> str:
    """What the threshold *means* for this kind, in words a form can print.

    Written here rather than in the UI because the answer is a property of the kind, and a copy in
    TypeScript is a copy that stops matching the day a kind is added.
    """
    if kind in RATE_KINDS:
        return "percent of requests"
    if kind in RATIO_KINDS:
        return "percent of the previous window"
    return "occurrences"


def needs_sample(kind: RuleKind) -> bool:
    """Whether a minimum sample is meaningful for this kind.

    An event kind is not a proportion of anything: a credential used from a new address is one
    observation, and requiring twenty of them before saying so would be requiring twenty leaks.
    """
    return kind in RATE_KINDS or kind in RATIO_KINDS
