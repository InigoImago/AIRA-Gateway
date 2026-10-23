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
    assert "refers back to what a group matched" in (catastrophic_reason(r"(.*)\1!") or "")
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
# `is_catastrophic` cannot see either of them at full resolution — a run of *exact* repeats
# multiplies ones, so `a{3}a{3}a{3}` never moves the product whatever `_ways` answers for `{3}`.
# A broken counter there changes no verdict, which is the survivor that says *the property lives
# one level down* rather than *nothing depends on it*. So the contract is asserted where it is
# stated — over the bounds `re` itself parses each quantifier into, which are what the rule reads.


def _bounds(quantifier: str) -> tuple[int, int]:
    """The repeat bounds `re` parses ``quantifier`` into; no quantifier is exactly once."""
    from aira_common.patterns import _parser

    if not quantifier:
        return 1, 1
    [(_, (low, high, _body))] = list(_parser.parse("a" + quantifier, 0))
    return int(low), int(high)


@pytest.mark.parametrize(
    ("quantifier", "ways"),
    [
        ("", 1),
        ("?", 2),
        ("{3}", 1),  # exactly three: `a{3}a{3}` is `a{6}` and there is nothing to redistribute
        ("{2,4}", 3),
        ("{0,10}", 11),
        ("*", 1),  # unbounded: the count is the input's length, and `_grows_with_the_text` says so
        ("+", 1),
        ("{3,}", 1),
    ],
)
def test_how_many_ways_a_quantifier_can_match(quantifier: str, ways: int) -> None:
    from aira_common.patterns import _ways

    assert _ways(*_bounds(quantifier)) == ways


def test_bounds_that_make_no_sense_answer_one_way() -> None:
    """`re` refuses `{4,2}` itself, so this never comes from a parse — but a count of ways must
    never be negative, or a product of them could pass the bound by changing sign."""
    from aira_common.patterns import _ways

    assert _ways(4, 2) == 1


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
    from aira_common.patterns import _grows_with_the_text

    assert _grows_with_the_text(_bounds(quantifier)[1]) is unbounded


# =================================================================================================
# Two repeats that trade characters, however the pattern spells them
# =================================================================================================
#
# The rule above was read off the pattern's *text*: two repeats were compared by spelling, a group
# hid what it wrapped, and verbose mode changed what a space meant without telling the reader. Each
# row below was accepted that way and is refused now, because the rule walks what `re` parses and
# compares the characters each repeat can consume — as sets, through the engine itself.

#: Each shape, with what `re.search` cost on it — compiled case-insensitively, as the filter runs
#: it — over inputs of 250 / 500 / 1 000 / 2 000 characters that do not match.
MEASURED_TRADES = (
    # Two classes spelled differently that share most of their members.
    ("[a-z]*[a-y]*!", "33 ms · 243 ms · 1 957 ms · 15 s"),
    (r"\w*\d*\w*!", "791 ms · 11.6 s, and not finished at 1 000"),
    (r"\S*\w*@", "6 ms · 48 ms · 371 ms · 3 088 ms"),
    (r"[\s\S]*[\S\s]*!", "2 ms · 10 ms · 76 ms · 559 ms"),
    # The same characters under another name.
    (r"[0-9]+\d+!", "6 ms · 50 ms · 370 ms · 2 949 ms"),
    (r"\w+\d+!", "6 ms · 49 ms · 387 ms · 2 984 ms"),
    # A repeat wrapped in a group: the parse sees through it, the text did not.
    (r"(\d+)\d+X", "23 ms · 188 ms · 1 416 ms · 11 s"),
    # One letter in two cases — the filter compiles case-insensitively — and the Kelvin sign, which
    # folds to `k`.
    ("a+A+!", "8 ms · 62 ms · 484 ms · 3 840 ms"),
    ("k*K*!", "8 ms · 62 ms · 499 ms · 3 872 ms"),
    # A character both repeats can consume does not separate them, however required it is.
    (".*a.*!", "128 ms at 1 000 · 14 s at 5 000"),
    ("[ab]*ab[ab]*!", "149 ms · 1 040 ms · 7.5 s at 500 / 1 000 / 2 000"),
    # A repeat inside a lookahead runs at every position the repeat before it can stop at.
    (r"\d+(?=\d*!)", "49 ms · 420 ms · 3.6 s at 500 / 1 000 / 2 000"),
    (".*(?=.*!)", "151 ms · 1.2 s · 9.8 s at 1 000 / 2 000 / 4 000"),
    # A repeat that gives nothing back is still run again for every split of the one before it.
    (".*a*+!", "53 ms · 434 ms · 3.4 s at 500 / 1 000 / 2 000"),
    (".*(?>a*)!", "56 ms · 503 ms · 3.6 s at 500 / 1 000 / 2 000"),
)


@pytest.mark.parametrize(("pattern", "cost"), MEASURED_TRADES)
def test_two_repeats_that_can_trade_characters_are_refused(pattern: str, cost: str) -> None:
    """The dry run let any member post `[a-z]*[a-y]*[a-x]*!` with a sample of 8 000 letters, and
    the instance it reached stopped serving every use case: a single worker, one event loop."""
    assert "twice in a row" in (catastrophic_reason(pattern) or ""), cost


@pytest.mark.parametrize(
    "pattern",
    [
        # Three classes in a row: the reported worst case, quartic in the text.
        "[a-z]*[a-y]*[a-x]*!",
        # Every spelling of one repeat wrapped in a group, on either side.
        r"(?:\d+)\d+!",
        r"\d+(\d+)",
        r"(\d+)(\d+)X",
        # Two ranges that meet only in a script the fixed probes do not cover: found through their
        # own endpoints, which the check adds to what it tries.
        r"[一-鿿]*[丁-鿾]*!",
        r"[\U0001F600-\U0001F64F]*[\U0001F601-\U0001F64E]*!",
        r"\d*[༐-ཀ]*!",
        # A group repeated twice in a row is the same characters twice in a row.
        "(?:ab)+(?:ab)+!",
    ],
)
def test_every_spelling_of_the_shape_is_refused(pattern: str) -> None:
    assert "twice in a row" in (catastrophic_reason(pattern) or "")


@pytest.mark.parametrize(
    ("pattern", "cost"),
    [
        # Verbose mode ignores the space, so this is `(a+)+$`. Measured on 18 / 22 / 26 characters
        # and `!`: 0.02 s · 0.30 s · 4.87 s, doubling per character.
        (r"(?x)(a+) +$", "doubles per character"),
        # Scoped to a group, and with a comment between the group and its quantifier.
        (r"(?x:(a+) +)$", "the same, scoped"),
        ("(?x)(a+)#c\n+$", "the same, behind a comment"),
    ],
)
def test_verbose_mode_is_read_the_way_re_reads_it(pattern: str, cost: str) -> None:
    """Not refused for being verbose: analysed as what it is. A rule that refused the flag would
    keep the next syntax that changes what the text means unexamined."""
    assert catastrophic_reason(pattern) == catastrophic_reason("(a+)+$"), cost
    assert catastrophic_reason(pattern) is not None


@pytest.mark.parametrize(
    "pattern",
    [
        # `re` reads `a|b` as the class `[ab]`: one way to match, and a class repeated is linear.
        "(a|b)+",
        # A tempered dot: the lookahead consumes nothing and divides nothing, even with a branch.
        r"(?:(?!x).)*!",
        r"(?:(?!ab|cd).)*",
        # Sets that cannot share a character — decided member by member, and by category where a
        # set is too large to list.
        r"[A-Za-zÄÖÜäöüß]+\s+[A-Za-zÄÖÜäöüß]+",
        r"[À-ÿ]+\s+[À-ÿ]+",
        r"[一-鿿]+\s+[一-鿿]+",
        r"\S+\s+\S+",
        r"[^\s]+\s+[^\s]+",
        r"(?a)\w+\s+\w+",
        r"\d+\s+\d+",
        # A possessive repeat gives nothing back, so nothing after it can trade with it.
        r"a*+a*!",
    ],
)
def test_sets_that_cannot_share_a_character_are_not_a_run(pattern: str) -> None:
    """The other direction, for the widening this rule took: comparing sets rather than spellings
    refuses more, and every case here is one it must not — `redaction.py` refuses to start on a
    false positive."""
    assert catastrophic_reason(pattern) is None


def test_the_category_relations_the_rule_relies_on_hold_for_every_character() -> None:
    """`_DISJOINT_CATEGORIES` and `_CATEGORY_WITHIN` are claims about Unicode, and a wrong entry is
    a refusal that silently does not happen. Each is checked against every code point there is, in
    both of the engine's modes — one search over all of them, for a counterexample."""
    import re

    from aira_common.patterns import _CATEGORY_WITHIN, _DISJOINT_CATEGORIES

    everything = "".join(map(chr, range(0x110000)))
    for flags in (0, re.ASCII):
        for pair in _DISJOINT_CATEGORIES:
            first, second = sorted(pair)
            both = re.compile(rf"(?=\{first})\{second}", flags)
            assert both.search(everything) is None, (pair, flags)
        for inner, outer in _CATEGORY_WITHIN:
            outside = re.compile(rf"(?=\{inner})\{outer.upper()}", flags)
            assert outside.search(everything) is None, (inner, outer, flags)
    assert ("w", "d") not in _CATEGORY_WITHIN, "a word character is not always a digit"


def test_the_parse_the_rule_reads_has_the_shape_it_expects() -> None:
    """`re._parser` is private, so an interpreter may reshape it. The rule reads opcodes by
    identity and bounds by position; if either moved, every pattern would walk into
    `UNKNOWN_CONSTRUCT` — loudly, but at a gateway's start-up rather than here."""
    from aira_common.patterns import _op, _parser

    [(repeat, (low, high, body))] = list(_parser.parse(r"(?x)(a+) +", 0))
    assert repeat is _op.MAX_REPEAT and (low, high) == (1, _op.MAXREPEAT)
    [(group, (_, added, removed, inner))] = list(body)
    assert group is _op.SUBPATTERN and (added, removed) == (0, 0)
    [(nested, (nested_low, nested_high, letters))] = list(inner)
    assert nested is _op.MAX_REPEAT and (nested_low, nested_high) == (1, _op.MAXREPEAT)
    assert list(letters) == [(_op.LITERAL, ord("a"))]
    [(charset, items)] = list(_parser.parse(r"[^a-z\d]", 0))
    assert charset is _op.IN
    assert items == [(_op.NEGATE, None), (_op.RANGE, (97, 122)), (_op.CATEGORY, _op.CATEGORY_DIGIT)]


def test_no_pattern_this_suite_knows_reaches_a_construct_the_rule_cannot_read() -> None:
    """The fallback for an opcode the walk does not know is a refusal, which is safe and would
    also hide a gap: every pattern exercised here must be decided by a rule, not by that."""
    from aira_common.patterns import UNKNOWN_CONSTRUCT

    corpus = [
        *(pattern for pattern, _ in MEASURED_TRADES),
        *(pattern for pattern, _ in MEASURED_RUNS),
        *(pattern for pattern, _ in MEASURED_AT_SCALE),
        *(pattern for pattern, _ in MEASURED_DISASTERS),
        r"(?P<n>x)(?P=n)",
        r"(a)?(?(1)b|c)",
        r"(?>ab|a)c",
        r"(?<!a)b(?<=b)",
        r"\Aa\Z",
    ]
    assert [p for p in corpus if catastrophic_reason(p) == UNKNOWN_CONSTRUCT] == []


def test_the_check_bounds_its_own_work() -> None:
    """The gateway asks this on the request path. Sets too large to compare within the check's
    budget are refused rather than compared for as long as it takes — and quickly."""
    import time

    from aira_common.patterns import TOO_COMPLEX

    # Sixteen disjoint astral ranges of 60 000 characters each, every one unbounded and adjacent to
    # the next: deciding that no two of them meet means scanning 120 pairs of them — seven million
    # characters, past the check's budget.
    def ranges(count: int) -> str:
        return "".join(
            f"[\\U{0x20000 + i * 60_000:08x}-\\U{0x20000 + i * 60_000 + 59_999:08x}]*"
            for i in range(count)
        )

    started = time.perf_counter()
    assert catastrophic_reason(ranges(16) + "!") == TOO_COMPLEX
    assert time.perf_counter() - started < 2
    # Twelve of them fit the budget, and are decided rather than refused: they are disjoint.
    assert catastrophic_reason(ranges(12) + "!") is None


# =================================================================================================
# A choice is a repeat too: optional steps, and bounded repeats beside an unbounded one
# =================================================================================================


def test_a_row_of_optional_steps_over_the_same_characters_is_refused() -> None:
    """`x?` is `x{0,1}` — two ways — and a row of them doubles the work per step. `a?` twenty-two
    times took 2.8 s on forty characters; neither rule above counted a `?` at all. Under the bound
    the product stays a constant: thirteen are 8 192 ways, fourteen are 16 384."""
    assert catastrophic_reason("a?" * 14 + "!") is not None
    assert catastrophic_reason("(?:ab)?" * 14 + "!") is not None
    assert catastrophic_reason("a?" * 13 + "!") is None


def test_each_loop_is_counted_once_however_many_paths_left_it_open() -> None:
    """An optional step leaves two paths — through it and around it — and both keep the loops
    before it open. Counting a loop once per path would square the product."""
    assert catastrophic_reason("a?" * 13 + "!") is None


@pytest.mark.parametrize(
    ("pattern", "refused"),
    [
        # Sixteen ways multiplying a search already quadratic in the text: 1 900 ms on 4 000.
        ("[ab]+[ab]{0,3}[ab]{0,3}!", True),
        # Ninety-nine squared ways: under `MAX_AMBIGUITY`, and catastrophic beside the loop.
        ("[ab]+[ab]{0,98}[ab]{0,98}!", True),
        ("[ab]+[ab]?[ab]?!", True),
        # Three ways is the most the loop-side bound admits: 349 ms where `[ab]+!` took 85.
        ("[ab]+[ab]{0,2}!", False),
        ("[ab]+[ab]?!", False),
        (r"\+?[0-9]{2,4}[ -]?[0-9]{3,}", False),
        (r"[0-9]+[.,]?[0-9]{0,2}", False),
    ],
)
def test_bounded_repeats_beside_an_unbounded_one_have_a_bound_of_their_own(
    pattern: str, refused: bool
) -> None:
    assert (catastrophic_reason(pattern) is not None) is refused, catastrophic_reason(pattern)


def test_the_gateway_takes_the_pattern_bounds_from_the_rule_rather_than_restating_them() -> None:
    """Two copies of `64` agree until one is raised, and then Management accepts patterns the
    gateway drops — a filter that shows as configured while matching less. An identity test cannot
    tell two small integers apart, so this one reads the source: the gateway imports the bounds and
    assigns no number of its own to them."""
    import ast
    import inspect

    from aira_gateway.pipeline.classifiers import injection

    tree = ast.parse(inspect.getsource(injection))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "aira_common.patterns"
        for alias in node.names
    }
    restated = [
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name) and "PATTERN" in target.id
    ]

    assert {"MAX_PATTERNS", "MAX_PATTERN_LENGTH"} <= imported
    assert restated == []


# =================================================================================================
# A reference back to what a group matched: its cost is the text's to decide
# =================================================================================================

#: Shapes every rule above accepted, with what each was **measured** to cost (`re.search` over a
#: run of `a`, which none of them matches). The first is cubic: 0.35 s on 1 000 characters, 2.8 s on
#: 2 000, 22.7 s on 4 000 — and the filter reads twenty thousand.
REFERS_BACK_SHAPES = (
    (r"(.*)\1!", "2.8 s on 2 000 characters"),
    (r"(a|b)*\1c", "accepted, and slow for the same reason"),
    (r"(?P<x>.*)(?P=x)!", "the named spelling of the first"),
    (r"(?P<x>a+)(?P=x)+!", "a named reference, repeated"),
    (r"(a)?(?(1)a*|b*)a*!", "a conditional on what a group matched"),
    # Refused before as two repeats trading characters, because the walk read a reference as any
    # one character; it is a reference, and refused as one.
    (r"(a+)\1+b", "108 ms · 814 ms · 7.6 s at 500 / 1 000 / 2 000"),
    # Shapes the earlier rule let through as one character, repeated: refused all the same, because
    # the rule cannot tell a reference to one character from a reference to the whole prompt.
    (r"(.)\1{3,}!", "a reference to one character, repeated"),
    (r"(\w+)\s*=\s*\1", "an assignment repeating its own name"),
)


@pytest.mark.parametrize(("pattern", "cost"), REFERS_BACK_SHAPES)
def test_a_reference_to_what_a_group_matched_is_refused(pattern: str, cost: str) -> None:
    """A back-reference matches whatever its group captured, and a conditional branches on whether
    it did — so what either costs is decided by the text, and the analysis this module makes
    (sets, repeats, the ways to divide a text) has nothing to measure. The walk used to treat a
    reference as consuming nothing, which let `(.*)\\1!` through as harmless: a use-case
    administrator's pattern that holds the only event loop for seconds per prompt."""
    from aira_common.patterns import REFERS_BACK

    assert catastrophic_reason(pattern) == REFERS_BACK, f"{pattern} ({cost}) would be compiled"


@pytest.mark.parametrize(
    "pattern",
    [
        # Inside the body of a repeat, which the walk judges without descending into…
        r"(?:(a)\1)+",
        # …inside an assertion, and inside a group inside an alternative.
        r"(?=(a+))\1",
        r"x|(?:y(z)\1)",
    ],
)
def test_a_reference_is_found_wherever_it_sits(pattern: str) -> None:
    """Asked of every node of the parse rather than of the shapes the walk follows: a check written
    in the walk's terms answers *safe* for whatever the walk never reaches."""
    from aira_common.patterns import REFERS_BACK

    assert catastrophic_reason(pattern) == REFERS_BACK


@pytest.mark.parametrize("pattern", [r"[\x0b-\x0c]+x", r"[\x0c-\x0e]x", r"\x0b\x0c"])
def test_a_character_whose_code_equals_an_opcode_is_not_a_reference(pattern: str) -> None:
    """The parse is made of integers, opcodes among them: `[\\x0c-\\x0e]` is the pair `(12, 14)`,
    and 12 is the value of `GROUPREF_EXISTS`. Compared by value, a vertical tab or a form feed in a
    character class would refuse a pattern that refers to nothing."""
    assert catastrophic_reason(pattern) is None
