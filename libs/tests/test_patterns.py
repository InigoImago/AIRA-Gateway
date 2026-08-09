"""Which operator-supplied regexes may run on the request path.

The rule is shared because it used to live in one plane only: Management refused a nested
quantifier at authoring time and the gateway compiled whatever reached its read-model. Protection
at one end of a link and trust at the other is the shape of three of `ADR-0018`'s four findings.
"""

from __future__ import annotations

import pytest

from aira_common.patterns import is_catastrophic


@pytest.mark.parametrize(
    "pattern",
    [
        "(a+)+",
        "(a*)*",
        "(ab|a)+",
        "(x+x+)+y",
        "ignore (all|every)+ instruction",
        "(a{2,})+",
    ],
)
def test_a_nested_quantifier_is_refused(pattern: str) -> None:
    assert is_catastrophic(pattern) is True


@pytest.mark.parametrize(
    "pattern",
    [
        "ignore previous instructions",
        "system prompt",
        "(?:reveal|show) your instructions",
        "a+b+",
        r"\bdisregard\b.{0,20}\brules\b",
        "[A-Z]{3,}",
    ],
)
def test_an_ordinary_pattern_is_allowed(pattern: str) -> None:
    """The other half. A rule that refused everything would pass every case above and quietly
    disable the whole heuristic filter — which is `FRD-125`'s defect exactly: a control that shows
    as active while matching nothing."""
    assert is_catastrophic(pattern) is False


def test_a_pattern_that_is_not_valid_regex_is_not_refused() -> None:
    """The gateway matches those **literally** (`_compile` falls back to `re.escape`), so they
    cannot backtrack. Refusing them would reject a plain string somebody wrote with a stray
    bracket — a phrase like `(confidential` is a perfectly good thing to look for."""
    assert is_catastrophic("(unclosed") is False
    assert is_catastrophic("a{2,1}") is False


def test_the_rule_is_the_same_object_both_planes_read() -> None:
    """Not a value test — an identity one. Two copies of this expression would agree until one of
    them was tightened, and the plane that kept the looser copy is the one on the request path."""
    from aira_management.apps.pipelines import serializers as management
    from aira_gateway.pipeline import classifiers as gateway

    assert management.is_catastrophic is is_catastrophic
    assert gateway.is_catastrophic is is_catastrophic
