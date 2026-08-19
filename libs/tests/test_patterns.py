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


#: Shapes the previous detector accepted, with what each was **measured** to cost on a
#: thirty-character input that does not match. Not estimates: `re` was timed on 2026-08-19.
MEASURED_DISASTERS = (
    ("(a+){20}$", "51 s"),
    ("(a+){2,}$", "76 s"),
    ("((a)*)*b", "159 s"),
    (r"(\d+){15}$", "35 s"),
)


@pytest.mark.parametrize(("pattern", "cost"), MEASURED_DISASTERS)
def test_a_counted_or_nested_repetition_is_refused(pattern: str, cost: str) -> None:
    """The two holes the old detector had, each found by timing rather than by reading.

    It was one regex — `\\([^)]*[+*}][^)]*\\)\\s*[+*]` — and a regex cannot ask this question:

    - the **outer** quantifier was matched as `[+*]` only, so every counted form walked past it.
      `(a+){20}` repeats twenty times and took **51 s** on thirty characters;
    - `[^)]*` cannot see past the first `)`, so a group inside a group was invisible. `((a)*)*b`
      took **159 s**.

    A pattern language cannot describe its own nesting, which is why the detector is a scanner now.
    The consequence of the hole was not theoretical: these are operator-supplied patterns compiled
    onto the **request path**, so one of them in a pipeline filter is a gateway that stalls for
    minutes on a short prompt, for every caller, until somebody finds the configuration.
    """
    assert is_catastrophic(pattern), f"{pattern} takes {cost} and would be compiled"


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bsecret\b",
        r"[0-9]{4,6}",
        r"sk-[A-Za-z0-9]{20,}",
        r"(foo|bar)baz",
        r"(?i)password",
        r"AKIA[0-9A-Z]{16}",
        r"(a|b)?",
        r"(abc){3}",
        r"[^)]*",
        r"\(literal\)+",
    ],
)
def test_an_ordinary_pattern_is_still_accepted(pattern: str) -> None:
    """The other direction, and the one a broader check breaks first.

    Erring towards refusal is safe in principle and expensive in practice: a redaction pattern
    refused at startup stops the gateway, so a detector that widens without care takes an
    installation down to prevent a hazard it does not have. Three of these are the shapes most
    likely to be caught by accident — a group repeated a fixed few times (`(abc){3}`), a character
    class holding a bracket (`[^)]*`), and **escaped** parentheses that are not a group at all.
    """
    assert not is_catastrophic(pattern)


def test_every_pattern_this_project_ships_is_still_compilable() -> None:
    """A widened detector that refuses one of our own built-ins is an outage, not a fix.

    `PatternRedactor` raises at construction on a pattern it will not compile, and the redactor is
    built during `create_app` — so this failing means the gateway does not start at all.
    """
    from aira_gateway.persistence.redaction import BUILTIN_PATTERNS

    refused = [pattern for pattern in BUILTIN_PATTERNS if is_catastrophic(pattern)]

    assert not refused, f"the detector now refuses patterns this gateway ships: {refused}"
