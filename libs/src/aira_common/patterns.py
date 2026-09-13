"""Which operator-supplied regexes are safe to run on the request path.

Python's `re` has no timeout, so a catastrophically backtracking pattern stalls a gateway worker —
on an event loop every use case shares — for as long as it runs. The only defence is not compiling
it. Two shapes are refused:

- *a repeated group whose body repeats* — `(a+)+`, `(a*)*`, `(ab|a)+`;
- *repeated quantifiers in a row over the same characters* — `a*a*b`, `\\s*\\s*…` — as easy to
  write by accident, by concatenating optional fragments, as on purpose.

**Both planes ask.** Management refuses such a pattern at authoring time, and the gateway checks
again whatever reaches its read-model (over Kafka, from a seed, a direct write or an older
Management): the check is cheap and missing it is a hung worker (`ADR-0018`).

The detection is a **heuristic**: it recognises the shapes that cause the problem in practice, not
every regex that could backtrack. Erring towards refusal is broadly safe, but not always — the
pipeline filter drops a refused pattern, while `persistence/redaction.py` **raises at start-up**,
so a false positive there is a gateway that will not boot. Hence a measured bound on bounded
ambiguity (`MAX_AMBIGUITY`): `\\+?[0-9]{2,4}[ -]?[0-9]{3,}` is a phone number and must pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Quantifiers that can repeat a group enough times to matter. `?` repeats at most once, so it
#: cannot multiply the work of what it encloses.
_REPEATING = "*+"

#: Escapes and anchors that consume no character, and therefore separate nothing.
_ZERO_WIDTH = frozenset({r"\b", r"\B", r"\A", r"\Z", "^", "$"})

#: How many ways a run of *bounded* repeats may divide the same text before it is refused.
#:
#: Measured against 20 000 characters, the length `classifiers.MAX_SCANNED_CHARS` hands the filter:
#: `[0-9]{2,4}` × 3 is 27 ways and 8 ms, `a{0,10}` × 3 is 1 331 and 57 ms, `a{0,10}` × 4 is 14 641
#: and 637 ms, and × 6 does not finish. The bound sits between the largest comfortable figure and
#: the first that is not — a ceiling with a derivation, so nobody raises it blindly (`LESSONS.md`
#: §3).
MAX_AMBIGUITY = 10_000


@dataclass(frozen=True, slots=True)
class _Atom:
    """One atom of a pattern, and what the adjacency rule asks about it."""

    text: str
    #: Its quantifier can run more than once, so the engine has choices to explore.
    repeats: bool
    #: It can match **nothing** — `?`, `*`, `{0,n}`, or a zero-width assertion — so it does not
    #: separate what is either side of it.
    optional: bool
    #: Its quantifier has no upper limit (`*`, `+`, `{n,}`): the engine's choices grow with the
    #: input rather than being a constant factor, as `[0-9]{2,4}`'s three are.
    unbounded: bool = False
    #: How many lengths a *bounded* quantifier can match — `{2,4}` is three, `{3}` is one.
    #: Multiplied across a run, the number of ways to divide the same text. Meaningless when
    #: :attr:`unbounded`.
    choices: int = 1


def _quantifier_at(pattern: str, index: int) -> tuple[bool, int]:
    """Whether a repeating quantifier starts at ``index``, and where it ends.

    `{n}`, `{n,}` and `{n,m}` count when they can run a group more than once: `(a+){20}` is as
    catastrophic as `(a+)+`.
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

    A scanner rather than a regex: the question is about nesting, which a pattern language cannot
    describe (`[^)]*` cannot see past the first `)`).
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


def _atoms(pattern: str) -> list[_Atom]:
    """Every atom of ``pattern`` at this level: an escape, a class, a group or one character.

    `|`, `^` and `$` come out as ordinary atoms. An atom that neither repeats nor is optional
    already breaks a run, so `a*|a*` is two branches without a rule of its own. A scanner, for the
    reason `_groups` is one.
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
    """:func:`_quantifier_at`, plus the quantifier forms that do not repeat *ambiguously*.

    `?` and the lazy/possessive suffixes (`*?`, `+?`, `{2,3}?`) must be **consumed** here, or the
    next loop reads them as atoms and the adjacency is computed over a sequence that does not
    exist; a lazy quantifier backtracks exactly as a greedy one does. A **possessive** one never
    gives characters back, so it is reported as not repeating — it is also the rewrite the refusal
    advises, and refusing it would make the advice useless.
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

    `{2,4}` is three and `{3}` one, so `a{3}a{3}` is simply `a{6}`. Unbounded forms answer 1: the
    number that matters for them is the input's length (:attr:`_Atom.unbounded`).
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

    Two unbounded repeats over the same characters split the text in a number of ways that grows
    with the prompt; two bounded ones give a constant (see `MAX_AMBIGUITY`).
    """
    if quantifier in ("*", "+"):
        return True
    if not quantifier.startswith("{") or not quantifier.endswith("}"):
        return False
    parts = quantifier[1:-1].split(",")
    return len(parts) == 2 and parts[1].strip() == ""


def _may_match_nothing(quantifier: str) -> bool:
    """Whether ``quantifier`` lets its atom match the empty string.

    `x?` looks like a separator and is not one: in `\\s*x?\\s*` the two `\\s*` are adjacent.
    """
    if quantifier in ("?", "*"):
        return True
    if not quantifier.startswith("{") or not quantifier.endswith("}"):
        return False
    first = quantifier[1:-1].split(",")[0].strip()
    return first in ("", "0")


def _overlap(first: str, second: str) -> bool:
    """Whether two adjacent atoms can match the same character — conservatively.

    Identical atoms can, and `.` matches anything; intersecting character classes would be a
    different program. Staying narrow keeps unambiguous patterns allowed — `a+b+`, and the
    built-in `eyJ[A-Za-z0-9\\-_]+\\.[A-Za-z0-9\\-_]+`, whose refusal would stop the gateway.
    """
    return first == second or "." in (first, second)


def _ambiguous_run(pattern: str) -> bool:
    """Whether two repeated quantifiers sit side by side over the same characters.

    Checked at every nesting level: `(a*a*a*)` costs what the same sequence costs outside a group.
    """
    # Every repeating atom since the last one that **must** consume a character. A list rather than
    # the previous atom: an optional repeat (`x{0,3}` in `\s*x{0,3}\s*`) is both a candidate and
    # transparent.
    run: list[_Atom] = []
    atoms = _atoms(pattern)
    for atom in atoms:
        if atom.repeats:
            overlapping = [earlier for earlier in run if _overlap(earlier.text, atom.text)]
            # Two unbounded repeats: the ways to divide the text grow with the input.
            if atom.unbounded and any(earlier.unbounded for earlier in overlapping):
                return True
            # Bounded repeats multiply into a constant, refused only above `MAX_AMBIGUITY` — a
            # phone number written as three groups with optional separators is this shape.
            ambiguity = atom.choices
            for earlier in overlapping:
                ambiguity *= earlier.choices
            if ambiguity >= MAX_AMBIGUITY:
                return True
            # A required repeat consumes a character, so it separates everything before it and
            # is the only candidate left.
            run = [*run, atom] if atom.optional else [atom]
        elif not atom.optional:
            # Something that must match a character separates two runs: why `eyJ[…]+\.[…]+` is
            # safe and `\s*x?\s*` is not.
            run = []
    return any(
        atom.text.startswith("(") and atom.text.endswith(")") and _ambiguous_run(atom.text[1:-1])
        for atom in atoms
    )


def catastrophic_reason(pattern: str) -> str | None:
    """Why ``pattern`` must not be compiled for use on a request, or ``None`` if it may be.

    A sentence rather than a flag: every call site tells an operator what to do about it, and the
    two shapes need different explanations. A pattern that is not valid regex answers ``None`` —
    the gateway matches those literally (`classifiers._compile` falls back to `re.escape`), so they
    cannot backtrack.
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
        # An alternation inside a repeated group is `(a|a)+`: two ways to match the same text,
        # multiplied by the outer repetition.
        if "|" in body:
            return "it repeats a group that offers two ways to match the same text"
        # A quantifier inside one is `(a+)+`. `?` counts *here* — `(a?)*` blows up too — while it
        # does not count as the outer repetition.
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

    The boolean form of :func:`catastrophic_reason`, so the two can never disagree.
    """
    return catastrophic_reason(pattern) is not None
