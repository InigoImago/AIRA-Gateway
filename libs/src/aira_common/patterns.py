"""Which operator-supplied regexes are safe to run on the request path.

Python's `re` has no timeout, so a catastrophically backtracking pattern stalls a gateway worker —
on an event loop every use case shares — for as long as it runs. The only defence is not compiling
it. Two shapes are refused:

- *a repeated group whose body repeats or branches* — `(a+)+`, `(a*)*`, `(ab|a)+`;
- *two repeats that can trade characters* — `a*a*b`, `[a-z]*[a-y]*!`, `.*a.*!`, `\\d+(?=\\d*!)`: the
  engine can divide the same text between them in a number of ways that grows with the prompt, so
  every character added multiplies the work instead of adding to it.

**The pattern is analysed as `re` parses it, not as it is spelled.** The check walks the tree `re`'s
own parser builds, so verbose mode (`(?x)(a+) +$` *is* `(a+)+$`), a repeat wrapped in a group
(`(\\d+)\\d+`) and every other spelling of a shape reach it as the shape. **Characters are compared
as sets**, and membership is the engine's own answer — case folding included — so `[0-9]+\\d+` and
`\\w+\\d+` are found although no two atoms are spelled alike. Every pattern is read
case-insensitively, as the injection filter compiles it: there `a+A+` is one run.

Two repeats trade characters when nothing *between* them stops it: a character that must be
consumed separates them only if one of the two cannot consume it — `\\s*:\\s*` is safe, `.*a.*!` is
cubic. A repeat that never gives characters back (possessive, atomic) trades with nothing after it,
but is still re-run for every split of a repeat before it.

**Both planes ask.** Management refuses such a pattern at authoring time, and the gateway checks
again whatever reaches its read-model (over Kafka, from a seed, a direct write or an older
Management): the check is cheap and missing it is a hung worker (`ADR-0018`). The bounds on how
many patterns and how long each (`MAX_PATTERNS`, `MAX_PATTERN_LENGTH`) are shared for the same
reason.

The detection is a **heuristic** about the *degree* of backtracking: one unbounded repeat still
costs a search time proportional to the square of the text on hostile input, which is why the text
a filter scans is bounded (`classifiers.MAX_SCANNED_CHARS`). Erring towards refusal is broadly safe,
but not always — the pipeline filter drops a refused pattern, while `persistence/redaction.py`
**raises at start-up**, so a false positive there is a gateway that will not boot. Hence a measured
bound on bounded ambiguity (`MAX_AMBIGUITY`): `\\+?[0-9]{2,4}[ -]?[0-9]{3,}` is a phone number and
must pass.
"""

from __future__ import annotations

import functools
import importlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

#: How many custom patterns one filter may carry, and how long each may be (`ADR-0007`). Shared by
#: both planes: Management refuses more at authoring time, and the gateway drops — with a log line
#: — whatever reaches it beyond them, so neither can accept a pattern the other ignores.
MAX_PATTERNS = 64
MAX_PATTERN_LENGTH = 256

#: How many ways a run of *bounded* repeats may divide the same text before it is refused.
#:
#: Measured against 20 000 characters, the length `classifiers.MAX_SCANNED_CHARS` hands the filter:
#: `[0-9]{2,4}` × 3 is 27 ways and 8 ms, `a{0,10}` × 3 is 1 331 and 57 ms, `a{0,10}` × 4 is 14 641
#: and 637 ms, and × 6 does not finish. The bound sits between the largest comfortable figure and
#: the first that is not — a ceiling with a derivation, so nobody raises it blindly (`LESSONS.md`
#: §3).
MAX_AMBIGUITY = 10_000

#: The same bound where one of the repeats has no upper limit. Each way then multiplies a search
#: whose cost already grows with the square of the text: at 4 000 characters `[ab]+!` took 85 ms,
#: `[ab]+[ab]{0,2}!` (three ways) 349 ms and `[ab]+[ab]{0,3}[ab]{0,3}!` (sixteen) 1 900 ms. Three
#: ways is a phone number's `[0-9]{2,4}` ahead of its `[0-9]{3,}`, and the most it admits.
MAX_AMBIGUITY_BESIDE_A_LOOP = 4

#: The refusals, one sentence per shape: an operator reads the sentence instead of the pattern.
REPEATS_A_REPEAT = "it repeats a group whose contents already repeat"
REPEATS_A_CHOICE = "it repeats a group that offers two ways to match the same text"
TRADES_CHARACTERS = (
    "it repeats the same characters twice in a row, which the engine can split many ways"
)
UNKNOWN_CONSTRUCT = "it uses a construct this check cannot analyse, so it is refused, not run"
TOO_COMPLEX = "it is too complex to check within the check's own bound, so it is refused"

#: Every pattern is read the way the injection filter compiles it. Case folding only ever adds
#: members to a set, so reading a case-sensitive pattern this way can refuse more, never less.
_ASSUMED_FLAGS = re.IGNORECASE

#: How many characters a set may hold for its overlap with another to be decided member by member.
#: The whole Basic Multilingual Plane: only an astral range or `[\x00-\U0010ffff]` exceeds it, and
#: such a set is taken to overlap — the refusing direction.
_LISTABLE = 0x10000

#: How many characters one check may scan while comparing sets before it gives up and refuses.
#: The check runs on the request path as well, and must not become the slow thing it guards
#: against: this is about sixty scans of the largest listable set, a few tens of milliseconds.
_BUDGET = 4_000_000

#: `re`'s own parser and opcodes: what the engine runs, which is the thing to analyse. Private
#: modules without stubs, hence untyped; `test_patterns.py` pins the shape this module reads, so
#: an interpreter that changes it fails there rather than here, quietly.
_parser: Any = importlib.import_module("re._parser")
_op: Any = importlib.import_module("re._constants")
_casefix: Any = importlib.import_module("re._casefix")

_CONSUMES_ONE = (_op.LITERAL, _op.NOT_LITERAL, _op.ANY, _op.IN)
_REPEATS = (_op.MAX_REPEAT, _op.MIN_REPEAT, _op.POSSESSIVE_REPEAT)
_ASSERTIONS = (_op.ASSERT, _op.ASSERT_NOT)

#: A category escape, as the one-character pattern that renders it and as the category it names —
#: the second with whether it is the complement.
_CATEGORIES: dict[Any, tuple[str, str, bool]] = {
    _op.CATEGORY_DIGIT: (r"\d", "d", False),
    _op.CATEGORY_NOT_DIGIT: (r"\D", "d", True),
    _op.CATEGORY_SPACE: (r"\s", "s", False),
    _op.CATEGORY_NOT_SPACE: (r"\S", "s", True),
    _op.CATEGORY_WORD: (r"\w", "w", False),
    _op.CATEGORY_NOT_WORD: (r"\W", "w", True),
}

#: Category pairs no character belongs to both of, and pairs where every member of the first is a
#: member of the second — in Unicode and in ASCII mode alike. `test_patterns.py` checks both claims
#: against every code point, because a wrong entry here is a refusal that does not happen.
_DISJOINT_CATEGORIES = frozenset({frozenset({"s", "w"}), frozenset({"s", "d"})})
_CATEGORY_WITHIN = frozenset({("d", "d"), ("s", "s"), ("w", "w"), ("d", "w")})

#: Characters that answer "do these two sets meet?" for sets too large to list: every ASCII
#: character, and a representative of each kind the categories reach beyond it — letters and
#: digits of other scripts, the Unicode spaces, marks, symbols, case-folding oddities, a
#: private-use character and astral ones. A witness found here proves an overlap; finding none
#: proves nothing, which is why a proof of disjointness is asked for separately.
# fmt: off
_PROBES: frozenset[int] = frozenset(range(0x80)) | frozenset(
    {
        # Latin-1 and Latin Extended: NEL and NBSP are spaces, ª and µ letters, ² a digit that is
        # not decimal, ß and ÿ change length or script when case-folded.
        0x85, 0xA0, 0xAA, 0xB2, 0xB5, 0xBA, 0xC0, 0xC9, 0xDF, 0xE9, 0xFF,
        0x130, 0x131, 0x17F, 0x1C5,
        # Letters of other scripts: Greek, Cyrillic, Hebrew, Arabic, Devanagari, Kana, CJK, Hangul.
        0x3A9, 0x3C9, 0x416, 0x436, 0x5D0, 0x627, 0x915, 0x3042, 0x4E2D, 0xAC00,
        # Decimal digits that are not ASCII: Arabic-Indic, Devanagari, fullwidth, astral.
        0x660, 0x6F0, 0x966, 0xFF10, 0x104A0, 0x1D7CE,
        # Spaces that are not ASCII.
        0x1680, 0x2000, 0x2007, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
        # A combining mark, symbols, a letter-like number, the Kelvin sign, private use, the BOM,
        # the replacement character, an emoji, a lone surrogate and the last code point.
        0x300, 0x20AC, 0x2122, 0x2160, 0x212A, 0xE000, 0xFEFF, 0xFFFD, 0x1F600, 0xD800, 0x10FFFF,
    }
)
# fmt: on


class _Refused(Exception):
    """A rule fired; carries the sentence the operator is shown."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Class:
    """A set of characters one step of the pattern consumes from, as the engine will test it."""

    #: A one-character pattern for the set, rendered from the parse, so membership is the engine's
    #: own answer — case folding included — rather than a re-implementation of it.
    pattern: str
    flags: int
    #: Its members, when it is literals and ranges few enough to list.
    members: frozenset[int] | None
    #: For any set the disjointness rules can read: whether it is the complement of what follows,
    #: the categories, and the listed members beside them. ``None`` for a set mixing a category
    #: and a complemented one, or too large to list.
    form: tuple[bool, frozenset[str], frozenset[int]] | None


#: A union of sets: what a repeat of more than one step, or a group, can consume.
type _Chars = tuple[_Class, ...]


@dataclass(frozen=True, slots=True, eq=False)
class _Loop:
    """A repeat the engine can split text with: over what, and in how many ways."""

    #: Every character one pass of it can consume.
    chars: _Chars
    #: No upper limit: the ways to divide a text grow with the text.
    unbounded: bool
    #: How many lengths a bounded repeat can take — `{2,4}` is three, `{3}` one, an optional step
    #: two. Multiplied across a run of repeats that trade characters, the number of ways to divide
    #: the same text.
    ways: int
    #: What one pass **must** consume, step by step. An open loop can take over a pass only if it
    #: can consume every one of them: `[a-z]*(?:ab)+` trades, `[a-z]*(?:a1)+` cannot.
    steps: tuple[_Chars, ...] = ()


@dataclass(frozen=True, slots=True)
class _Open:
    """A loop that can still trade characters with a later one, and what was consumed since."""

    loop: _Loop
    since: tuple[_Chars, ...] = ()


#: The loops open at one point of the pattern — on any path that reaches it.
type _Run = tuple[_Open, ...]


@functools.lru_cache(maxsize=1 << 14)
def _compiled(pattern: str, flags: int) -> re.Pattern[str]:
    """One set's one-character pattern, compiled once per process."""
    return re.compile(pattern, flags)


def _finder(chars: _Class) -> re.Pattern[str]:
    return _compiled(chars.pattern, chars.flags)


def _contains(chars: _Class, code: int) -> bool:
    return _finder(chars).fullmatch(chr(code)) is not None


@functools.lru_cache(maxsize=1 << 14)
def _variants(code: int) -> frozenset[int]:
    """``code`` and every character case folding can match it with."""
    char = chr(code)
    found = {code}
    for other in (char.lower(), char.upper(), char.casefold(), char.title(), char.swapcase()):
        if len(other) == 1:
            found.add(ord(other))
    for known in tuple(found):
        found.update(_casefix._EXTRA_CASES.get(known, ()))
    return frozenset(found)


@functools.lru_cache(maxsize=256)
def _spelled(members: frozenset[int], folded: bool) -> str:
    """``members`` as one string the engine can scan in a single pass.

    ``folded`` adds every case variant, for a set matched case-insensitively and compared with one
    that is not: case-insensitive matching treats a character and its variants alike, so between
    two case-insensitive sets the members alone already decide. A superset — characters the set
    does not match are filtered out by asking it.
    """
    text = "".join(map(chr, sorted(members)))
    if not folded:
        return text
    # Word by word, so `title` capitalises every character rather than the first of the string.
    variants = text + text.lower() + text.upper() + text.casefold() + text.swapcase()
    variants += " ".join(text).title()
    present = set(map(ord, variants))
    return variants + "".join(
        chr(other)
        for code, others in _casefix._EXTRA_CASES.items()
        if code in present
        for other in others
    )


def _escape(code: int) -> str:
    return f"\\U{code:08x}"


def _grows_with_the_text(high: int) -> bool:
    """Whether a repeat whose upper bound is ``high`` can run as many times as the text allows.

    Two such repeats over the same characters split the text in a number of ways that grows with
    the prompt; two bounded ones give a constant (see `MAX_AMBIGUITY`).
    """
    return high == int(_op.MAXREPEAT)


def _ways(low: int, high: int) -> int:
    """How many lengths a repeat from ``low`` to ``high`` can take.

    `{2,4}` is three and `{3}` one, so `a{3}a{3}` is simply `a{6}`. An unbounded repeat answers 1:
    the number that matters for it is the text's length (:func:`_grows_with_the_text`). Bounds that
    make no sense answer one way, never a negative count.
    """
    return 1 if _grows_with_the_text(high) else max(1, high - low + 1)


def _joined(runs: Iterable[_Run]) -> _Run:
    """The loops open after any of several paths, each once."""
    return tuple(dict.fromkeys(opened for run in runs for opened in run))


class _Check:
    """One pattern's analysis: the sets it builds, the overlaps it decides and its budget."""

    def __init__(self, probes: frozenset[int]) -> None:
        self._probes = _spelled(probes, False)
        self._budget = _BUDGET
        self._decided: dict[tuple[_Class, _Class], bool] = {}

    # -- the walk ---------------------------------------------------------------------------------

    def walk(self, items: Sequence[tuple[Any, Any]], flags: int, run: _Run) -> _Run:
        """Follow ``items`` from the loops ``run`` holds open; refuse where two can trade."""
        for op, av in items:
            if op in _CONSUMES_ONE:
                run = self._consumed(run, (self._class(op, av, flags),))
            elif op is _op.AT or op is _op.GROUPREF:
                # An anchor consumes nothing, and a back-reference matches whatever its group
                # matched: neither can be shown to separate two loops, so neither does.
                continue
            elif op in _REPEATS:
                run = self._repeat(op, av, flags, run)
            elif op is _op.SUBPATTERN:
                _, added, removed, body = av
                run = self.walk(body, (flags | added) & ~removed, run)
            elif op is _op.BRANCH:
                run = _joined(self.walk(branch, flags, run) for branch in av[1])
            elif op is _op.GROUPREF_EXISTS:
                _, yes, no = av
                run = _joined((self.walk(yes, flags, run), self.walk(no or (), flags, run)))
            elif op in _ASSERTIONS:
                # Consumes nothing, but runs at every position the open loops can stop at: a
                # repeat inside it meets them exactly as one outside would.
                self.walk(av[1], flags, run)
            elif op is _op.ATOMIC_GROUP:
                # Backtracks freely inside until it matches, then never gives anything back: its
                # loops meet the open ones, and nothing after it can trade with them.
                self.walk(av, flags, run)
                run = run if av.getwidth()[0] == 0 else self._consumed(run, self._chars(av, flags))
            else:
                raise _Refused(UNKNOWN_CONSTRUCT)
        return run

    def _repeat(self, op: Any, av: Any, flags: int, run: _Run) -> _Run:
        low, high, body = int(av[0]), int(av[1]), av[2]
        if high <= 1:
            # At most once — `?`, `{0,1}`, `{1}`: a path through the body, and one around it.
            through = self.walk(body, flags, run)
            if low > 0 or body.getwidth()[0] == 0:
                return through if low > 0 else _joined((run, through))
            # **An optional step is a choice of two**, and a row of them over the same characters
            # doubles the work per step: `a?` twenty-two times took 2.8 s on forty characters.
            choice = _Loop(self._chars(body, flags), False, 2, self._steps(body, flags))
            self._meet(run, choice)
            return (*_joined((run, through)), _Open(choice))
        reason = _inside_a_repeat(body, in_assertion=False)
        if reason is not None:
            raise _Refused(reason)
        possessive = op is _op.POSSESSIVE_REPEAT
        loop = _Loop(
            chars=self._chars(body, flags),
            unbounded=_grows_with_the_text(high),
            # A possessive repeat takes the longest match and keeps it: one way, whatever its range.
            ways=1 if possessive else _ways(low, high),
            steps=self._steps(body, flags),
        )
        self._meet(run, loop)
        consumes = low > 0 and body.getwidth()[0] > 0
        if possessive:
            # Never gives a character back, so no later loop can trade with it; it still separates
            # the open loops from what follows wherever it must consume something.
            return self._consumed(run, loop.chars) if consumes else run
        return (*(self._consumed(run, loop.chars) if consumes else run), _Open(loop))

    def _meet(self, run: _Run, loop: _Loop) -> None:
        """Refuse ``loop`` if it can trade characters with an open loop beyond the bounds."""
        trading = [
            opened
            for opened in run
            if all(self.overlaps(opened.loop.chars, step) for step in loop.steps)
            and self.overlaps(opened.loop.chars, loop.chars)
            and all(self.overlaps(between, loop.chars) for between in opened.since)
        ]
        # Two unbounded repeats: the ways to divide the text grow with the text.
        beside_a_loop = loop.unbounded or any(opened.loop.unbounded for opened in trading)
        if loop.unbounded and any(opened.loop.unbounded for opened in trading):
            raise _Refused(TRADES_CHARACTERS)
        # Bounded repeats multiply into a constant, refused only above `MAX_AMBIGUITY` — a phone
        # number written as three groups with optional separators is this shape. Beside an
        # unbounded repeat the constant multiplies a cost that already grows with the square of the
        # text, so the bound there is `MAX_AMBIGUITY_BESIDE_A_LOOP`.
        ways = loop.ways
        # Each loop once, though two paths may have left it open twice.
        for earlier in {id(opened.loop): opened.loop for opened in trading}.values():
            ways *= earlier.ways
        if ways >= (MAX_AMBIGUITY_BESIDE_A_LOOP if beside_a_loop else MAX_AMBIGUITY):
            raise _Refused(TRADES_CHARACTERS)

    def _consumed(self, run: _Run, chars: _Chars) -> _Run:
        """The open loops after a step that must consume one of ``chars``.

        A loop that cannot consume the character is separated from everything after it; one that
        can stays open, remembering the character, because a later loop must be able to consume it
        too for the two to trade (`[ab]*ab[ab]*` trades, `[ab]*a[b]*` does not).
        """
        return tuple(
            _Open(opened.loop, (*opened.since, chars))
            for opened in run
            if self.overlaps(opened.loop.chars, chars)
        )

    # -- the sets ---------------------------------------------------------------------------------

    def _chars(self, items: Sequence[tuple[Any, Any]], flags: int) -> _Chars:
        """Every set ``items`` can consume from: what a repeat of them, or a group, can take."""
        found: list[_Class] = []
        for op, av in items:
            if op in _CONSUMES_ONE:
                found.append(self._class(op, av, flags))
            elif op in _REPEATS:
                found.extend(self._chars(av[2], flags))
            elif op is _op.SUBPATTERN:
                found.extend(self._chars(av[3], (flags | av[1]) & ~av[2]))
            elif op is _op.BRANCH:
                for branch in av[1]:
                    found.extend(self._chars(branch, flags))
            elif op is _op.GROUPREF_EXISTS:
                found.extend(self._chars(av[1], flags))
                found.extend(self._chars(av[2] or (), flags))
            elif op is _op.ATOMIC_GROUP:
                found.extend(self._chars(av, flags))
            elif op is _op.GROUPREF:
                # Whatever its group matched — any character at all.
                found.append(self._class(_op.ANY, None, flags | re.DOTALL))
        return tuple(dict.fromkeys(found))

    def _steps(self, items: Sequence[tuple[Any, Any]], flags: int) -> tuple[_Chars, ...]:
        """What ``items`` must consume, step by step — each step as the set it consumes from.

        A step that may be skipped is left out: it need not be absorbed for two repeats to trade.
        A required group or repeat is one step over everything it can consume — coarser, and in
        the refusing direction.
        """
        found: list[_Chars] = []
        for op, av in items:
            if op in _CONSUMES_ONE:
                found.append((self._class(op, av, flags),))
            elif op is _op.SUBPATTERN:
                found.extend(self._steps(av[3], (flags | av[1]) & ~av[2]))
            elif op in _REPEATS and int(av[0]) > 0 and av[2].getwidth()[0] > 0:
                found.append(self._chars(av[2], flags))
            elif op is _op.ATOMIC_GROUP and av.getwidth()[0] > 0:
                found.append(self._chars(av, flags))
            elif _alternatives_all_consume(op, av):
                found.append(self._chars([(op, av)], flags))
        return tuple(found)

    @staticmethod
    def _class(op: Any, av: Any, flags: int) -> _Class:
        if op is _op.LITERAL:
            only = frozenset({int(av)})
            return _Class(f"[{_escape(av)}]", flags, only, (False, frozenset(), only))
        if op is _op.NOT_LITERAL:
            return _Class(f"[^{_escape(av)}]", flags, None, (True, frozenset(), frozenset({av})))
        if op is _op.ANY:
            # The complement of a newline, or of nothing at all under DOTALL.
            beyond = frozenset() if flags & re.DOTALL else frozenset({ord("\n")})
            return _Class(".", flags, None, (True, frozenset(), beyond))
        return _set(av, flags)

    # -- overlap ----------------------------------------------------------------------------------

    def overlaps(self, first: _Chars, second: _Chars) -> bool:
        return any(self._pair(a, b) for a in first for b in second)

    def _pair(self, a: _Class, b: _Class) -> bool:
        key = (a, b)
        if key not in self._decided:
            self._decided[key] = self._decided[(b, a)] = self._meets(a, b)
        return self._decided[key]

    def _meets(self, a: _Class, b: _Class) -> bool:
        listed = [chars for chars in (a, b) if chars.members is not None]
        if listed:
            owner = min(listed, key=lambda chars: len(chars.members or ()))
            return self._shared(owner, b if owner is a else a, owner.members or frozenset())
        # Neither can be listed: a witness among the probes proves an overlap, and without one the
        # two still overlap unless their categories prove they cannot.
        self._spend(len(self._probes))
        if any(_contains(b, ord(found.group())) for found in _finder(a).finditer(self._probes)):
            return True
        return not self._disjoint(a, b)

    def _shared(self, owner: _Class, other: _Class, members: frozenset[int]) -> bool:
        """Whether ``other`` matches a character ``owner`` matches through ``members`` — one of
        them, or where only ``owner`` folds case, a case variant of one. One scan, in the engine."""
        folded = bool(owner.flags & re.IGNORECASE) and not other.flags & re.IGNORECASE
        text = _spelled(members, folded)
        self._spend(len(text))
        if not folded:
            return _finder(other).search(text) is not None
        return any(_contains(owner, ord(found.group())) for found in _finder(other).finditer(text))

    def _disjoint(self, a: _Class, b: _Class) -> bool:
        """Whether the categories prove that no character is in both sets."""
        if a.form is None or b.form is None or (a.flags ^ b.flags) & re.ASCII:
            return False
        if a.form[0]:
            a, b = b, a
        assert a.form is not None and b.form is not None
        (a_complement, a_categories, a_members) = a.form
        (b_complement, b_categories, b_members) = b.form
        if a_complement:
            # Both complements: what lies outside two sets is almost everything.
            return False
        if b_complement:
            # `b` is everything outside its categories and members: `a` must lie inside them.
            if not all(
                any((mine, theirs) in _CATEGORY_WITHIN for theirs in b_categories)
                for mine in a_categories
            ):
                return False
            return not self._shared(a, b, a_members)
        if any(
            frozenset({mine, theirs}) not in _DISJOINT_CATEGORIES
            for mine in a_categories
            for theirs in b_categories
        ):
            return False
        return not (self._shared(a, b, a_members) or self._shared(b, a, b_members))

    def _spend(self, characters: int) -> None:
        self._budget -= characters
        if self._budget < 0:
            raise _Refused(TOO_COMPLEX)


def _set(items: Sequence[tuple[Any, Any]], flags: int) -> _Class:
    """A character class (`IN`): its one-character pattern, its members and its form."""
    complement = False
    rendered: list[str] = []
    members: set[int] = set()
    categories: set[str] = set()
    complemented: set[str] = set()
    for op, av in items:
        if op is _op.NEGATE:
            complement = True
        elif op is _op.LITERAL:
            rendered.append(_escape(av))
            members.add(av)
        elif op is _op.RANGE:
            low, high = av
            rendered.append(f"{_escape(low)}-{_escape(high)}")
            if len(members) + high - low < _LISTABLE:
                members.update(range(low, high + 1))
            else:
                members.add(-1)  # marks "too many to list" without listing them
        elif op is _op.CATEGORY and av in _CATEGORIES:
            escape, name, negated = _CATEGORIES[av]
            rendered.append(escape)
            (complemented if negated else categories).add(name)
        else:
            raise _Refused(UNKNOWN_CONSTRUCT)
    pattern = "[" + ("^" if complement else "") + "".join(rendered) + "]"
    if -1 in members:
        return _Class(pattern, flags, None, None)
    listed = frozenset(members)
    if complemented:
        # `\S` alone is the complement of `\s`, and `[^\S]` is `\s` itself; anything mixing a
        # complemented category with other members has no form the rules can read.
        if categories or members or len(complemented) > 1:
            return _Class(pattern, flags, None, None)
        return _Class(pattern, flags, None, (not complement, frozenset(complemented), frozenset()))
    plain = not complement and not categories
    return _Class(
        pattern, flags, listed if plain else None, (complement, frozenset(categories), listed)
    )


def _alternatives_all_consume(op: Any, av: Any) -> bool:
    """Whether every alternative of a branch or a conditional must consume a character."""
    if op is _op.BRANCH:
        return all(branch.getwidth()[0] > 0 for branch in av[1])
    if op is _op.GROUPREF_EXISTS:
        return bool(av[2]) and min(av[1].getwidth()[0], av[2].getwidth()[0]) > 0
    return False


def _inside_a_repeat(items: Sequence[tuple[Any, Any]], *, in_assertion: bool) -> str | None:
    """Why the body of a repeat makes the repeat catastrophic, or ``None``.

    A repeat inside it is `(a+)+` — `?` counts here, `(a?)*` blows up too — and that holds inside
    an assertion as well, which runs again on every iteration. An alternative is `(a|a)+`: two ways
    to match the same text, multiplied by the outer repetition; inside an assertion it consumes
    nothing and divides nothing, so `(?:(?!ab|cd).)*` is allowed.
    """
    for op, av in items:
        found: str | None = None
        if op in _REPEATS:
            if (int(av[0]), int(av[1])) != (1, 1):
                return REPEATS_A_REPEAT
            found = _inside_a_repeat(av[2], in_assertion=in_assertion)
        elif op is _op.BRANCH or op is _op.GROUPREF_EXISTS:
            if not in_assertion:
                return REPEATS_A_CHOICE
            branches = av[1] if op is _op.BRANCH else (av[1], av[2] or ())
            found = next(
                (
                    reason
                    for branch in branches
                    if (reason := _inside_a_repeat(branch, in_assertion=True)) is not None
                ),
                None,
            )
        elif op is _op.SUBPATTERN:
            found = _inside_a_repeat(av[3], in_assertion=in_assertion)
        elif op is _op.ATOMIC_GROUP:
            found = _inside_a_repeat(av, in_assertion=in_assertion)
        elif op in _ASSERTIONS:
            found = _inside_a_repeat(av[1], in_assertion=True)
        if found is not None:
            return found
    return None


def _probes(items: Sequence[tuple[Any, Any]]) -> frozenset[int]:
    """The fixed probes, and every character the pattern names, its neighbours and case variants.

    Two ranges that intersect share an endpoint of one of them, and a range that leaves a gap in a
    complemented set leaves it beside an endpoint — so these find the overlap of any two sets the
    pattern spells with literals and ranges.
    """
    named: set[int] = set()

    def collect(nodes: Sequence[tuple[Any, Any]]) -> None:
        for op, av in nodes:
            if op is _op.LITERAL or op is _op.NOT_LITERAL:
                named.add(av)
            elif op is _op.RANGE:
                named.update((av[0] - 1, av[0], av[1], av[1] + 1))
            elif op is _op.IN:
                collect(av)
            elif op in _REPEATS:
                collect(av[2])
            elif op is _op.SUBPATTERN:
                collect(av[3])
            elif op is _op.BRANCH:
                for branch in av[1]:
                    collect(branch)
            elif op is _op.GROUPREF_EXISTS:
                collect(av[1])
                collect(av[2] or ())
            elif op is _op.ATOMIC_GROUP:
                collect(av)
            elif op in _ASSERTIONS:
                collect(av[1])

    collect(items)
    within = {code for code in named if 0 <= code <= 0x10FFFF}
    return _PROBES | frozenset(variant for code in within for variant in _variants(code))


@functools.lru_cache(maxsize=4096)
def catastrophic_reason(pattern: str) -> str | None:
    """Why ``pattern`` must not be compiled for use on a request, or ``None`` if it may be.

    A sentence rather than a flag: every call site tells an operator what to do about it, and the
    shapes need different explanations. A pattern that is not valid regex answers ``None`` — the
    gateway matches those literally (`classifiers._compile` falls back to `re.escape`), so they
    cannot backtrack. Cached: the gateway asks on every request that carries custom patterns, and
    the answer is a function of the text alone.
    """
    try:
        re.compile(pattern)
        parsed = _parser.parse(pattern, 0)
    except re.error:
        return None
    check = _Check(_probes(parsed))
    try:
        check.walk(parsed, int(parsed.state.flags) | _ASSUMED_FLAGS, ())
    except _Refused as refusal:
        return refusal.reason
    return None


def is_catastrophic(pattern: str) -> bool:
    """True if ``pattern`` backtracks catastrophically and must not run on a request.

    The boolean form of :func:`catastrophic_reason`, so the two can never disagree.
    """
    return catastrophic_reason(pattern) is not None
