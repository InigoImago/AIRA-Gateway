"""Which operator-supplied regexes are safe to run on the request path.

Nested quantifiers — `(a+)+`, `(a*)*`, `(ab|a)+` — are the classic trigger for catastrophic
backtracking: one of them against a long prompt takes exponential time and stalls a gateway worker
for as long as it runs. Python's `re` has no timeout, so the only defence is not compiling them.

**Both planes ask, because only one of them used to.** Management refused such a pattern at
authoring time and the gateway compiled whatever reached its read-model — over Kafka, from a seed,
from a direct database write, or from an older Management that predates the check. The protection
sat at one end of a link and the other end trusted it, which is the shape of three of the four
findings in `ADR-0018`. The check is cheap and the consequence of missing it is a hung worker, so
it is asked twice on purpose.

The detection is a **heuristic and says so**: it recognises the shapes that cause the problem in
practice, not every regex that could backtrack. A precise answer would need to model the engine.
Erring towards refusal is safe here because a rejected pattern can always be written another way,
and an operator hears about it at the moment they write it.
"""

from __future__ import annotations

import re

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


def is_catastrophic(pattern: str) -> bool:
    """True if ``pattern`` nests quantifiers and must not be compiled for use on a request.

    A pattern that is not valid regex at all answers **False**: the gateway matches those
    literally (`_compile` falls back to `re.escape`), so they cannot backtrack and refusing them
    would reject a plain string somebody wrote with a stray bracket.
    """
    try:
        re.compile(pattern)
    except re.error:
        return False

    for start, end in _groups(pattern):
        repeats, _ = _quantifier_at(pattern, end + 1)
        if not repeats:
            continue
        body = pattern[start + 1 : end]
        # An alternation inside a repeated group is the `(a|a)+` shape: two ways to match the same
        # text, multiplied by the outer repetition.
        if "|" in body:
            return True
        # And a quantifier inside one is `(a+)+`. `?` counts *here* — `(a?)*` blows up like the
        # rest — while it does not count as the outer repetition, which is the asymmetry that
        # makes this two questions rather than one.
        inner = 0
        while inner < len(body):
            if body[inner] == "\\":
                inner += 2
                continue
            if body[inner] in "*+?":
                return True
            repeats_inner, after = _quantifier_at(body, inner)
            if repeats_inner:
                return True
            inner = after if after > inner else inner + 1
    return False
