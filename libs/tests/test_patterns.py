"""Which operator-supplied regexes may run on the request path.

The rule is shared because it used to live in one plane only: Management refused a nested
quantifier at authoring time and the gateway compiled whatever reached its read-model. Protection
at one end of a link and trust at the other is the shape of three of `ADR-0018`'s four findings.
"""

from __future__ import annotations

import pytest

from aira_common.patterns import catastrophic_reason, is_catastrophic


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

    from aira_gateway.pipeline.classifiers import injection as gateway

    assert management.catastrophic_reason is catastrophic_reason
    assert gateway.catastrophic_reason is catastrophic_reason
    assert is_catastrophic("(a+)+") is (catastrophic_reason("(a+)+") is not None)


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


#: The second shape, and what each was **measured** to cost on inputs of 20, 26 and 32 characters
#: that do not match. Timed on 2026-09-08, and every one of them was `is_catastrophic → False`.
#:
#: The figures are here rather than in a comment because they are the argument: the cost grows with
#: the *number* of adjacent quantifiers, and the input on the request path is a **prompt**, so
#: thirty-two characters is the small case rather than the large one.
MEASURED_RUNS = (
    ("a*a*a*a*a*a*a*a*b", "62 ms · 363 ms · 1 532 ms"),
    (r"\s*\s*\s*\s*\s*\s*\s*\s*\s*x", "229 ms · 1 733 ms · over 5 s"),
    ("[a-z]*[a-z]*[a-z]*[a-z]*[a-z]*!", "1 ms · 4 ms · 11 ms, and cubic in the chain length"),
    (".*.*.*.*.*!", "2 ms · 6 ms · 15 ms"),
    ("a{0,10}a{0,10}a{0,10}a{0,10}a{0,10}a{0,10}b", "4 ms · 12 ms · 30 ms"),
    # Lazy backtracks exactly as greedy does, and the suffix has to be *consumed* for the atoms
    # either side of it to be adjacent at all — left unconsumed, this one walked through.
    ("a*?a*?a*?a*?a*?a*?a*?a*?b", "130 ms · 937 ms · 4 264 ms"),
    # **An optional atom is not a separator**, which is the half a sequence-reader misses: `x?`
    # can match nothing, so the two `\\s*` either side of it are adjacent after all.
    (r"\s*x?\s*x?\s*x?\s*x?\s*x?\s*x?\s*x?\s*!", "125 ms · 709 ms · 3 021 ms"),
    # The same thing counted rather than marked: `{0,3}` starts at zero.
    (r"\s*x{0,3}\s*x{0,3}\s*x{0,3}\s*x{0,3}\s*x{0,3}\s*x{0,3}\s*!", "35 ms · 168 ms · 601 ms"),
    # And a **zero-width assertion**, which consumes nothing at all. Measured with `\\B`, which
    # *succeeds* between two spaces — the first attempt at this case used `\\b`, which fails there
    # and prunes the search, so it timed at 0.1 ms flat and would have argued the opposite.
    (r"\s*\B\s*\B\s*\B\s*\B\s*\B\s*\B\s*\B\s*!", "137 ms · 785 ms · 3 366 ms"),
    (r"\s*(?=)\s*(?=)\s*(?=)\s*(?=)\s*(?=)\s*(?=)\s*!", "27 ms · 122 ms · 457 ms"),
)

#: The same rule at **prompt scale**, which is where the two bounds were actually decided. Timed
#: against 1 000 / 5 000 / 20 000 characters, because `classifiers.MAX_SCANNED_CHARS` hands this
#: filter 20 000 of them and thirty-two characters says nothing about that.
MEASURED_AT_SCALE = (
    # A **pair** of unbounded repeats. Harmless-looking at thirty characters (0.1 ms), which is
    # why the first draft of the threshold missed it.
    ("a*a*b", "152 ms at 1 000 · over 10 s at 5 000"),
    # And a pair that overlaps through `.` rather than by being spelled the same way.
    (".*[a-z]*!", "261 ms at 1 000 · over 10 s at 5 000"),
    # Four bounded repeats: 11⁴ = 14 641 ways, which is the first figure over the bound.
    ("a{0,10}a{0,10}a{0,10}a{0,10}b", "31 ms · 164 ms · 637 ms"),
    # `{3,}` is `+` spelled with a floor, and the check has to know that: the counted form is
    # exactly where the *previous* detector's hole was, one rule along.
    (r"[0-9]{3,}[0-9]{3,}!", "259 ms at 1 000 · over 10 s at 5 000"),
    # An atom that can match nothing between two that cannot — `x*` is a separator only if it
    # happens to match something, and the engine is not obliged to let it.
    (r"\s*x*\s*!", "348 ms at 1 000 · over 10 s at 5 000"),
)


@pytest.mark.parametrize(("pattern", "cost"), MEASURED_AT_SCALE)
def test_a_run_that_only_hurts_at_prompt_scale_is_refused(pattern: str, cost: str) -> None:
    """The cases a thirty-character measurement calls harmless.

    `a*a*b` costs 0.1 ms on thirty characters and over ten seconds on five thousand — and the
    filter reads up to twenty thousand. **A number is not a defect until it moves** (`LESSONS.md`
    §1), and the length it has to move over is the one production supplies.
    """
    assert is_catastrophic(pattern) is True, cost


@pytest.mark.parametrize(("pattern", "cost"), MEASURED_RUNS)
def test_a_repeated_run_over_the_same_characters_is_refused(pattern: str, cost: str) -> None:
    """The hole the nesting rule could not see, because there is no group in any of these.

    `_groups` returns nothing for `a*a*a*…`, so every check in the old detector was skipped and the
    answer was False by falling off the end. It is the same failure mode as the two the previous
    round found — a question asked about the wrong structure — and it was found the same way, by
    timing rather than by reading.
    """
    assert is_catastrophic(pattern) is True, cost
    assert "twice in a row" in (catastrophic_reason(pattern) or "")


@pytest.mark.parametrize(
    "pattern",
    [
        # Different characters: the engine has one way to match, whatever the quantifiers.
        "a+b+",
        "a*b*",
        r"\d+\.\d+",
        # A required literal between them is what makes a built-in safe, and two of them are this
        # shape — `eyJ[…]+\.[…]+\.[…]+` and `aira_[…]{4,16}_[…]{16,}`.
        r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+",
        r"aira_[A-Za-z0-9]{4,16}_[A-Za-z0-9]{16,}",
        r"(?i)authorization\s*:\s*\S+",
        # An alternation is a branch, not a sequence: these two `a*` are never adjacent.
        "a*|a*",
        # `{1}` and `?` repeat at most once, so neither multiplies anything.
        "a{1}a{1}b",
        # **Possessive**: it never gives characters back, so there is nothing to explore. Measured
        # at 0.1 ms flat on 20, 26 and 32 characters, against 130–4 264 ms for the lazy form of the
        # same chain — and it is the rewrite the refusal advises, so refusing it would make the
        # advice useless.
        "a*+a*+a*+a*+a*+a*+a*+a*+b",
        # **A pair where only one side is unbounded.** `{2,4}` offers three choices whatever the
        # input, so the work does not grow with the prompt: measured at 2.5 ms against a thousand
        # digits, where the chain of six `{0,10}` above takes 3.7 s on the same input. An earlier
        # draft refused this, and `redaction.py` raises rather than warns — so a phone number
        # somebody wanted redacted would have been a gateway that did not start.
        r"\+?[0-9]{2,4}[ -]?[0-9]{3,}",
        # Bounded repeats multiply into a constant, and the constant is the question. Each of these
        # is under `MAX_AMBIGUITY` and each was measured against 20 000 characters — the length the
        # filter actually reads: 27 ways / 8 ms, 1 331 ways / 57 ms, 1 024 ways / 108 ms.
        "a{0,10}a{0,10}b",
        r"[0-9]{2,4}[ -]?[0-9]{2,4}[ -]?[0-9]{2,4}",
        "a{0,10}a{0,10}a{0,10}b",
        "a{0,3}a{0,3}a{0,3}a{0,3}a{0,3}b",
        # `{3}` matches exactly three, so there is nothing to redistribute: `a{3}a{3}` **is**
        # `a{6}`, and a rule that counted repeats rather than *ways* would have refused it. Five of
        # them measured 1.3 ms against 20 000 characters — the count says "five repeats in a row",
        # the ways say "one".
        "a{3}a{3}b",
        "a{3}a{3}a{3}a{3}a{3}b",
    ],
)
def test_an_unambiguous_run_is_allowed(pattern: str) -> None:
    """The widening, checked in the other direction.

    `LESSONS.md` §1: *a detector that refuses one of the built-ins is not a fix, it is a gateway
    that will not start.* Two of the shipped redaction patterns put two quantified classes close
    together with a required literal between them, and both must stay allowed.
    """
    assert is_catastrophic(pattern) is False, catastrophic_reason(pattern)


def test_every_built_in_pattern_survives_the_rule() -> None:
    """The check the list above approximates, asked of the real lists.

    A hand-written sample of safe patterns is a hand-written list; these are the ones the product
    actually compiles at start-up, and refusing one of them is a gateway that will not start.
    """
    from aira_gateway.persistence.redaction import BUILTIN_PATTERNS
    from aira_gateway.pipeline.classifiers import BUILTIN_INJECTION_PATTERNS

    refused = [
        pattern
        for pattern in (*BUILTIN_INJECTION_PATTERNS, *BUILTIN_PATTERNS)
        if is_catastrophic(pattern)
    ]

    assert not refused, refused
    assert len(BUILTIN_INJECTION_PATTERNS) + len(BUILTIN_PATTERNS) >= 10, "the lists are not empty"


def test_the_refusal_says_which_shape_it_found() -> None:
    """Two rules, two sentences — because the operator reads the sentence instead of the pattern.

    All three call sites said *"nests a quantifier inside a quantified group"* for as long as that
    was the only rule, which would have sent somebody looking for a group `a*a*a*` does not have.
    """
    assert "already repeat" in (catastrophic_reason("(a+)+") or "")
    assert "two ways to match" in (catastrophic_reason("(a|a)+") or "")
    assert "twice in a row" in (catastrophic_reason("a*a*a*a*b") or "")
    assert catastrophic_reason("ignore previous instructions") is None


@pytest.mark.parametrize(
    "pattern",
    [
        # A **required** atom between two repeats is what really separates them — one character
        # that has to be there, so the engine has nothing to redistribute.
        r"\s*:\s*",
        r"[0-9]+-[0-9]+-[0-9]+",
        r"\w+@\w+\.\w+",
    ],
)
def test_a_required_atom_between_two_repeats_separates_them(pattern: str) -> None:
    """The line the optional rule must not cross.

    Treating *every* atom as transparent would refuse a date, an address and half the built-ins —
    the widening has to be checked in both directions, and this is the direction that breaks a
    working installation rather than the one that hangs it.
    """
    assert is_catastrophic(pattern) is False, catastrophic_reason(pattern)


def test_a_run_inside_a_group_is_found_too() -> None:
    """Naming a fragment does not make it cheaper, and a group is how somebody names one."""
    assert is_catastrophic("(a*a*a*a*a*a*a*a*)b") is True
    assert is_catastrophic("prefix(?:\\s*\\s*\\s*\\s*)suffix") is True


# =================================================================================================
# The two counters the rule is built on, asked directly
# =================================================================================================
#
# Both are private, and both are tested here anyway. The reason is specific rather than a habit:
# `is_catastrophic` cannot see either of them at full resolution — a *required* repeat resets the
# run it belongs to, so `a{3}a{3}a{3}` only ever compares two atoms and the product never grows.
# Breaking `_lengths` for the exact form therefore changed no verdict, which is the survivor that
# says *the property lives one level down* rather than *nothing depends on it*: delete the line and
# the function raises `IndexError` on `{3}`. So the contract is asserted where it is stated.


@pytest.mark.parametrize(
    ("quantifier", "ways"),
    [
        ("", 1),
        ("?", 2),
        ("{3}", 1),  # exactly three: `a{3}a{3}` is `a{6}` and there is nothing to redistribute
        ("{2,4}", 3),
        ("{0,10}", 11),
        ("{4,2}", 1),  # nonsense bounds answer "one way", never a negative count
        ("*", 1),  # unbounded: the count is the input's length, and `_has_no_upper_limit` says so
        ("+", 1),
        ("{3,}", 1),
    ],
)
def test_how_many_ways_a_quantifier_can_match(quantifier: str, ways: int) -> None:
    from aira_common.patterns import _lengths

    assert _lengths(quantifier) == ways


@pytest.mark.parametrize(
    ("quantifier", "unbounded"),
    [
        ("*", True),
        ("+", True),
        ("{3,}", True),
        ("{2,4}", False),
        ("?", False),
        ("{3}", False),
        ("", False),
    ],
)
def test_which_quantifiers_grow_with_the_input(quantifier: str, unbounded: bool) -> None:
    """The distinction the whole threshold rests on: a constant number of ways, or one that grows
    with the prompt."""
    from aira_common.patterns import _has_no_upper_limit

    assert _has_no_upper_limit(quantifier) is unbounded
