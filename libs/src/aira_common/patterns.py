"""Which operator-supplied regexes are safe to run on the request path.

**Two shapes, not one.** Both make an engine try the same text many ways, and both stall a gateway
worker for as long as they run — Python's `re` has no timeout, so the only defence is not
compiling them.

*A repeated group whose body repeats* — `(a+)+`, `(a*)*`, `(ab|a)+` — is the textbook one, and the
only one this module recognised until 2026-09-08.

*Repeated quantifiers **in a row** over the same characters* — `a*a*a*…`, `\\s*\\s*\\s*…` — is the
other one, and it was measured walking straight through this check:

    a*a*a*a*a*a*a*a*b        62 ms · 363 ms · 1 532 ms   (20, 26, 32 characters)
    \\s*\\s* … \\s*x           229 ms · 1 733 ms · over 5 s
    a*a*b                    152 ms at 1 000 · over 10 s at 5 000

Every one of those was `is_catastrophic → False`. The last is the one that matters most: a *pair*
costs a tenth of a millisecond on thirty characters and does not finish on five thousand, and this
filter is handed up to `MAX_SCANNED_CHARS` — twenty thousand — of a **prompt**. A pattern like it is
as easy to write by accident, by concatenating optional fragments, as on purpose; the author is a
use-case administrator typing into the pipeline builder, and the stall is on an event loop every
use case shares.

**Both planes ask, because only one of them used to.** Management refused such a pattern at
authoring time and the gateway compiled whatever reached its read-model — over Kafka, from a seed,
from a direct database write, or from an older Management that predates the check. The protection
sat at one end of a link and the other end trusted it, which is the shape of three of the four
findings in `ADR-0018`. The check is cheap and the consequence of missing it is a hung worker, so
it is asked twice on purpose.

The detection is a **heuristic and says so**: it recognises the shapes that cause the problem in
practice, not every regex that could backtrack. A precise answer would need to model the engine.
Erring towards refusal is broadly safe here, because a rejected pattern can usually be written
another way and an operator hears about it at the moment they write it.

**Broadly, not always, and the difference decides the thresholds below.** The two callers do
different things with a refusal: the pipeline filter *drops* the pattern and names it in a log
line, so a use case is left with one rule fewer — degraded. `persistence/redaction.py` **raises at
start-up**, so a false positive there is a gateway that will not boot, over a pattern somebody
wrote to protect data. That is why the bound on a *constant* amount of ambiguity is measured
rather than guessed at (`MAX_AMBIGUITY`): `\\+?[0-9]{2,4}[ -]?[0-9]{3,}` is a phone number, it
costs 2.5 ms against a thousand digits, and an earlier draft of this rule refused it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Quantifiers that can repeat a group enough times to matter. `?` is deliberately absent: it
#: repeats at most once, so it cannot multiply the work of what it encloses.
_REPEATING = "*+"


def _quantifier_at(pattern: str, index: int) -> tuple[bool, int]:
    """Whether a repeating quantifier starts at ``index``, and where it ends.

    `{n}`, `{n,}` and `{n,m}` count as repeating when they can run a group more than once — which
    `(a+){20}` does twenty times, at fifty-one seconds on a thirty-character input. The previous
    detector looked for `[+*]` only, so every counted form walked past it.
    """
    if index >= len(pattern):
        return False, index
    if pattern[index] in _REPEATING:
        return True, index + 1
    if pattern[index] != "{":
        return False, index
    close = pattern.find("}", index)
    if close == -1:
        return False, index
    body = pattern[index + 1 : close]
    numbers = [part.strip() for part in body.split(",")]
    if not all(part.isdigit() or part == "" for part in numbers) or not any(numbers):
        return False, index
    # `{1}` and `{0,1}` repeat at most once and are harmless; anything larger is a repetition.
    largest = max((int(part) for part in numbers if part.isdigit()), default=0)
    unbounded = len(numbers) == 2 and numbers[1] == ""
    return (unbounded or largest > 1), close + 1


def _groups(pattern: str) -> list[tuple[int, int]]:
    """Every group's `(start, end)`, honouring escapes and character classes.

    A scanner rather than a regex. The rule being checked is *"a repeating group whose body itself
    repeats"*, which is a question about nesting — and `[^)]*` cannot see past the first `)`, so
    `((a)*)*` slipped through the regex that asked it. A pattern language cannot describe its own
    nesting; that is not a subtlety, it is the reason this function exists.
    """
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    index = 0
    in_class = False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            stack.append(index)
        elif char == ")" and stack:
            spans.append((stack.pop(), index))
        index += 1
    return spans


#: Escapes that consume no character. One of these between two repeated atoms separates nothing,
#: which is exactly what :func:`_ambiguous_run` has to know.
_ZERO_WIDTH = frozenset({r"\b", r"\B", r"\A", r"\Z", "^", "$"})

#: How many ways a run of *bounded* repeats may divide the same text before it is refused.
#:
#: Measured against 20 000 characters — the length `classifiers.MAX_SCANNED_CHARS` actually hands
#: this filter — on 2026-09-08:
#:
#: | run | ways | cost |
#: | --- | --- | --- |
#: | `[0-9]{2,4}` × 3 | 27 | 8 ms |
#: | `a{0,3}` × 5 | 1 024 | 108 ms |
#: | `a{0,10}` × 3 | 1 331 | 57 ms |
#: | `a{0,10}` × 4 | 14 641 | **637 ms** |
#: | `a{0,10}` × 6 | 1 771 561 | did not finish |
#:
#: So the bound sits between the largest figure that is comfortable and the first that is not. It
#: is a number with a derivation on purpose: *a ceiling nobody can account for is a number somebody
#: raises* (`LESSONS.md` §3).
MAX_AMBIGUITY = 10_000


@dataclass(frozen=True, slots=True)
class _Atom:
    """One atom of a pattern, and the two things the adjacency rule asks about it."""

    text: str
    #: Its quantifier can run more than once, so the engine has choices to explore.
    repeats: bool
    #: It can match **nothing** — `?`, `*`, `{0,n}`, or a zero-width assertion. Such an atom does
    #: not separate what is either side of it, however much it looks like it does.
    optional: bool
    #: Its quantifier has no upper limit — `*`, `+`, `{n,}`. **This is the difference between a
    #: constant factor and one that grows with the prompt**: `[0-9]{2,4}` offers the engine three
    #: choices whatever the input, and `[0-9]+` offers as many as there are characters.
    unbounded: bool = False
    #: How many lengths a *bounded* quantifier can match — `{2,4}` is three, `{3}` is one. Multiply
    #: these across a run and you have the number of ways the engine can divide the same text.
    #: Meaningless when :attr:`unbounded`, where the count is the length of the input.
    choices: int = 1


def _atoms(pattern: str) -> list[_Atom]:
    """Every atom of ``pattern`` at this nesting level.

    An *atom* is what a quantifier applies to: an escape, a character class, a whole group, or one
    literal character. `|`, `^` and `$` come out as ordinary atoms, which is all the adjacency rule
    below needs: an atom that neither repeats nor is optional already breaks a run, so `a*|a*` is
    two branches rather than a sequence without anything having to say so. (It *was* said, in a
    branch of its own — removed once breaking it deliberately changed nothing, because a rule the
    code appears to have and does not is worse than an absent one.)

    A scanner, for the reason `_groups` is one: the question is about structure, and a pattern
    language cannot describe its own nesting.
    """
    found: list[_Atom] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "\\":
            atom, index = pattern[index : index + 2], index + 2
        elif char == "[":
            end = index + 1
            if end < length and pattern[end] == "^":
                end += 1
            if end < length and pattern[end] == "]":  # a `]` first in a class is a literal
                end += 1
            while end < length and pattern[end] != "]":
                end += 2 if pattern[end] == "\\" else 1
            atom, index = pattern[index : end + 1], end + 1
        elif char == "(":
            end, depth, in_class = index, 0, False
            while end < length:
                here = pattern[end]
                if here == "\\":
                    end += 2
                    continue
                if in_class:
                    in_class = here != "]"
                elif here == "[":
                    in_class = True
                elif here == "(":
                    depth += 1
                elif here == ")":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            atom, index = pattern[index : end + 1], end + 1
        else:
            atom, index = char, index + 1
        repeats, optional, unbounded, choices, index = _quantifier_run(pattern, index)
        found.append(
            _Atom(
                atom,
                repeats,
                optional or atom in _ZERO_WIDTH or _is_lookaround(atom),
                unbounded,
                choices,
            )
        )
    return found


def _is_lookaround(atom: str) -> bool:
    """A lookahead or lookbehind consumes nothing, so it separates nothing either."""
    return atom.startswith(("(?=", "(?!", "(?<=", "(?<!")) or (
        atom.startswith("(?") and atom.endswith(")") and ":" not in atom and "=" not in atom
    )


def _quantifier_run(pattern: str, index: int) -> tuple[bool, bool, bool, int, int]:
    """:func:`_quantifier_at`, plus the forms that are quantifiers and do not repeat *ambiguously*.

    `?` and the lazy/possessive suffix (`*?`, `+?`, `{2,3}?`) have to be **consumed** here even
    though `?` says nothing about repetition, or the next loop reads them as atoms of their own and
    the adjacency below is computed over a sequence that does not exist. Measured with the suffix
    left unconsumed: `a*?a*?a*?a*?a*?a*?a*?a*?b` was not flagged, and it costs 130 ms · 937 ms ·
    4 264 ms on 20, 26 and 32 characters — lazy backtracks exactly as greedy does.

    A **possessive** quantifier is the one that genuinely cannot: it never gives characters back,
    so two of them in a row have one way to match and nothing to explore. Measured, same chain with
    `*+`: 0.1 ms flat at every length. Reported as not repeating, which matters because it is also
    the rewrite the refusal advises — telling somebody to remove the ambiguity and then refusing
    the pattern that does would make the advice useless.
    """
    repeats, after = _quantifier_at(pattern, index)
    if not repeats and after == index and index < len(pattern) and pattern[index] == "?":
        after = index + 1
    quantifier = pattern[index:after]
    optional = _may_match_nothing(quantifier)
    unbounded = _has_no_upper_limit(quantifier)
    choices = _lengths(quantifier)
    if after > index and after < len(pattern) and pattern[after] in "?+":
        possessive = pattern[after] == "+"
        return (repeats and not possessive), optional, unbounded, choices, after + 1
    return repeats, optional, unbounded, choices, after


def _lengths(quantifier: str) -> int:
    """How many different lengths a bounded ``quantifier`` can match.

    `{2,4}` is three; `{3}` is one, which is why `a{3}a{3}` is simply `a{6}` and nothing to worry
    about. Unbounded forms answer 1 here and are handled by :attr:`_Atom.unbounded` instead — the
    number that matters for them is the length of the prompt.
    """
    if quantifier == "?":
        return 2
    if not quantifier.startswith("{") or not quantifier.endswith("}"):
        return 1
    parts = [part.strip() for part in quantifier[1:-1].split(",")]
    if len(parts) == 1:
        return 1
    low = int(parts[0]) if parts[0].isdigit() else 0
    if not parts[1].isdigit():
        return 1
    return max(1, int(parts[1]) - low + 1)


def _has_no_upper_limit(quantifier: str) -> bool:
    """Whether ``quantifier`` can run as many times as the input allows.

    The quantity the rule below is really about. Two unbounded repeats over the same characters
    give the engine a number of splits that **grows with the prompt**; two bounded ones give a
    constant, however awkward the pattern looks. Measured: `\\+?[0-9]{2,4}[ -]?[0-9]{3,}!` costs
    2.5 ms against a thousand digits — and an earlier draft refused it, which would have stopped a
    gateway from starting over a phone number somebody wanted redacted.
    """
    if quantifier in ("*", "+"):
        return True
    if not quantifier.startswith("{") or not quantifier.endswith("}"):
        return False
    parts = quantifier[1:-1].split(",")
    return len(parts) == 2 and parts[1].strip() == ""


def _may_match_nothing(quantifier: str) -> bool:
    """Whether ``quantifier`` lets its atom match the empty string.

    The half of the rule that `\\s*x?\\s*x?\\s*…` is about. `x?` looks like a separator and is not
    one: it can match nothing, so the two `\\s*` either side of it are adjacent after all.
    Measured on 2026-09-08 — 125 ms · 709 ms · 3 021 ms on 20, 26 and 32 spaces — against a check
    that read the sequence literally and found nothing wrong with it.
    """
    if quantifier in ("?", "*"):
        return True
    if not quantifier.startswith("{") or not quantifier.endswith("}"):
        return False
    first = quantifier[1:-1].split(",")[0].strip()
    return first in ("", "0")


def _overlap(first: str, second: str) -> bool:
    """Whether two adjacent atoms can match the same character — conservatively.

    Identical atoms certainly can, and `.` can match anything. Anything cleverer would mean
    computing the intersection of two character classes, which is a different program: this is a
    heuristic and says so, and the shapes it is written for — a fragment concatenated with itself —
    are textually identical by construction.

    Being *narrow* here is what keeps the check honest in the other direction. `a+b+` and
    `eyJ[A-Za-z0-9\\-_]+\\.[A-Za-z0-9\\-_]+` (a built-in) are unambiguous and must stay allowed —
    a detector that refused one of the built-ins is not a fix, it is a gateway that will not start.
    """
    return first == second or "." in (first, second)


def _ambiguous_run(pattern: str) -> bool:
    """Whether two repeated quantifiers sit side by side over the same characters.

    Checked at every level, because `(a*a*a*a*a*a*a*a*)` costs exactly what the same sequence costs
    outside a group — and the group form is what somebody writes when they are trying to name a
    fragment.
    """
    # Every repeating atom since the last one that **must** consume a character. A list rather
    # than the previous atom alone, because an optional repeat is both a candidate and transparent:
    # in `\s*x{0,3}\s*`, the `x{0,3}` repeats *and* can match nothing, so it neither separates the
    # two `\s*` nor stops being a candidate itself. Written as "the previous one" first, which
    # answered False for exactly that pattern — measured at 601 ms on 32 characters.
    run: list[_Atom] = []
    atoms = _atoms(pattern)
    for atom in atoms:
        if atom.repeats:
            overlapping = [earlier for earlier in run if _overlap(earlier.text, atom.text)]
            # **Two questions, because two quantities grow differently**, and both thresholds are
            # measured rather than chosen. Every figure below is against an input of the length
            # this filter actually reads — `MAX_SCANNED_CHARS`, 20 000 characters of prompt — on
            # 2026-09-08.
            #
            # *Two unbounded repeats* over the same characters divide the text in a number of ways
            # that **grows with the input**: `a*a*b` costs 152 ms against a thousand characters and
            # over ten seconds against five thousand. One is enough; a chain is worse.
            if atom.unbounded and any(earlier.unbounded for earlier in overlapping):
                return True
            # *Bounded repeats* multiply into a constant, and the constant is the whole question.
            # `[0-9]{2,4}` three times over is 27 ways and 8 ms; `a{0,3}` five times is 1 024 and
            # 108 ms; `a{0,10}` three times is 1 331 and 57 ms — all of them fine. `a{0,10}` four
            # times is 14 641 and **637 ms**, six times is 1.8 million and does not finish. So the
            # line is drawn between the largest figure that is comfortable and the first that is
            # not, and a pattern under it is left alone: a phone number written as three groups
            # with optional separators is exactly this shape, and `redaction.py` *raises* — a false
            # positive there is a gateway that will not start.
            ambiguity = atom.choices
            for earlier in overlapping:
                ambiguity *= earlier.choices
            if ambiguity >= MAX_AMBIGUITY:
                return True
            # A repeat that is *required* consumes at least one character, so it separates
            # everything before it from everything after — and is itself the only candidate left.
            run = [*run, atom] if atom.optional else [atom]
        elif not atom.optional:
            # Something that must match at least one character. **That** is what separates two
            # runs — and it is why `eyJ[…]+\.[…]+` is safe while `\s*x?\s*` is not: the `\.` has
            # to be there and the `x?` does not.
            run = []
    return any(
        atom.text.startswith("(") and atom.text.endswith(")") and _ambiguous_run(atom.text[1:-1])
        for atom in atoms
    )


def catastrophic_reason(pattern: str) -> str | None:
    """Why ``pattern`` must not be compiled for use on a request, or ``None`` if it may be.

    **A sentence rather than a flag**, because the three call sites each have to tell somebody what
    to do about it, and until this returned a reason all three said *"nests a quantifier inside a
    quantified group"* — which is one of the two shapes and would have been a wrong explanation for
    the other, in a message the operator reads instead of the pattern.

    A pattern that is not valid regex at all answers ``None``: the gateway matches those literally
    (`classifiers._compile` falls back to `re.escape`), so they cannot backtrack and refusing them
    would reject a plain string somebody wrote with a stray bracket.
    """
    try:
        re.compile(pattern)
    except re.error:
        return None

    for start, end in _groups(pattern):
        repeats, _ = _quantifier_at(pattern, end + 1)
        if not repeats:
            continue
        body = pattern[start + 1 : end]
        # An alternation inside a repeated group is the `(a|a)+` shape: two ways to match the same
        # text, multiplied by the outer repetition.
        if "|" in body:
            return "it repeats a group that offers two ways to match the same text"
        # And a quantifier inside one is `(a+)+`. `?` counts *here* — `(a?)*` blows up like the
        # rest — while it does not count as the outer repetition, which is the asymmetry that
        # makes this two questions rather than one.
        inner = 0
        while inner < len(body):
            if body[inner] == "\\":
                inner += 2
                continue
            if body[inner] in "*+?":
                return "it repeats a group whose contents already repeat"
            repeats_inner, after = _quantifier_at(body, inner)
            if repeats_inner:
                return "it repeats a group whose contents already repeat"
            inner = after if after > inner else inner + 1

    if _ambiguous_run(pattern):
        return "it repeats the same characters twice in a row, which the engine can split many ways"
    return None


def is_catastrophic(pattern: str) -> bool:
    """True if ``pattern`` backtracks catastrophically and must not run on a request.

    The boolean form of :func:`catastrophic_reason`, kept because two of the three call sites only
    need to decide. One owner, so the two can never disagree about which patterns are refused.
    """
    return catastrophic_reason(pattern) is not None
