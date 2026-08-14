"""The console's copies of a closed vocabulary, held to the vocabulary.

`aira_common.anomalies` defines seven rule kinds, three targets and three actions, and calls itself
closed. Django derives its choices from the enum, so it cannot drift. The **console** cannot import
Python, so it restates the list — and it had drifted in both directions at once:

- it offered `token_spike`, which does not exist. Picking *"Token use jumped against the previous
  window"* and pressing **Create rule** answered `kind: "token_spike" is not a valid choice.`
- it omitted `blocked_prompt_rate`, which does exist, is implemented by the gateway's evaluator, is
  seeded by the showcase, and is **listed on the very screen** whose form cannot create it.

Three copies were wrong — the dropdown, the units table and the sentence writer — and the fourth
was a test named *"every kind has words"* that iterated a hand-written list containing the same
ghost and the same omission. It asserted completeness against a list that was itself incomplete: a
guard agreeing with the thing it guards, which is the failure mode this repository names most
often and had not yet seen in this shape.

So the comparison lives here, in the one language that can read both sides, and it runs **in both
directions**: a name the console offers must exist, and a name the vocabulary defines must be
offered. One direction would have caught `token_spike` and left `blocked_prompt_rate` missing for
as long as nobody asked for it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "src"))

from aira_common.anomalies import RuleAction, RuleKind, RuleTarget  # noqa: E402

SECURITY = ROOT / "management" / "frontend" / "src" / "app" / "features" / "security"
FORM = SECURITY / "rule-form.ts"
LANGUAGE = SECURITY / "rule-language.ts"
LANGUAGE_SPEC = SECURITY / "rule-language.spec.ts"


def _values(enum: type) -> set[str]:
    return {member.value for member in enum}


def _picker(source: str, const: str) -> set[str]:
    """The `value:` entries of a `const NAME = [...]` list of options."""
    block = re.search(rf"{const}[^=]*= \[(.*?)\n\];", source, re.S)
    assert block, f"{const} is no longer a list literal — move this check with it"
    return set(re.findall(r"value: '([a-z_]+)'", block.group(1)))


def _keys(source: str, const: str) -> set[str]:
    """The keys of a `const NAME: Record<string, string> = {...}` table."""
    block = re.search(rf"const {const}[^=]*= \{{(.*?)\n\}};", source, re.S)
    assert block, f"{const} is no longer an object literal — move this check with it"
    return set(re.findall(r"^\s+([a-z_]+):", block.group(1), re.M))


def _cases(source: str, function: str) -> set[str]:
    """The `case '…':` labels inside one function."""
    block = re.search(rf"function {function}\(.*?\n\}}", source, re.S)
    assert block, f"{function} is gone — move this check with it"
    return set(re.findall(r"case '([a-z_]+)':", block.group(0)))


def _list(source: str, const: str) -> set[str]:
    block = re.search(rf"const {const} = \[(.*?)\n  \];", source, re.S)
    assert block, f"{const} is no longer a list literal — move this check with it"
    return set(re.findall(r"'([a-z_]+)'", block.group(1)))


def test_the_vocabulary_is_what_this_check_thinks_it_is() -> None:
    """A guard on the guard. Every assertion below compares two parsed sets, and a parser that
    quietly returns nothing makes all of them pass by comparing nothing."""
    assert len(_values(RuleKind)) == 7, sorted(_values(RuleKind))
    assert "blocked_prompt_rate" in _values(RuleKind)
    assert _picker(FORM.read_text(), "RULE_KINDS"), "the dropdown parsed as empty"


def test_the_dropdown_offers_exactly_the_kinds_that_exist() -> None:
    offered = _picker(FORM.read_text(), "RULE_KINDS")
    known = _values(RuleKind)

    assert offered == known, (
        f"the console offers kinds that do not exist: {sorted(offered - known)}\n"
        f"and does not offer kinds that do: {sorted(known - offered)}\n\n"
        "`RULE_KINDS` in rule-form.ts restates a closed vocabulary the console cannot import. "
        "Both directions matter: an extra name is a form that answers 'not a valid choice', a "
        "missing one is a rule nobody can create from the screen that lists it."
    )


def test_every_kind_has_a_unit_and_a_sentence() -> None:
    """The two tables that turn a rule into words. A kind missing from either prints its own slug
    at whoever is deciding, at eleven at night, whether an alert matters."""
    language = LANGUAGE.read_text()
    known = _values(RuleKind)

    assert _keys(language, "UNITS") == known, sorted(known ^ _keys(language, "UNITS"))
    assert _cases(language, "subject") == known, sorted(known ^ _cases(language, "subject"))


def test_the_spec_that_checks_completeness_is_itself_complete() -> None:
    """The copy that hid the gap: a test iterating a hand-written list, asserting that every kind
    in it has words. It had the ghost and not the omission, so it passed while both were true."""
    listed = _list(LANGUAGE_SPEC.read_text(), "KINDS")

    assert listed == _values(RuleKind), sorted(listed ^ _values(RuleKind))


def _options(source: str, values: set[str]) -> set[str]:
    """`<option value="…">` labels present in the template, restricted to one vocabulary.

    Read from the template rather than from a constant, because targets and actions are written
    inline there — which is *why* they need checking from outside: there is no list to look at.
    """
    return set(re.findall(r'<option value="([a-z_]+)"', source)) & values


def test_the_other_two_vocabularies_line_up_as_well() -> None:
    """Targets and actions are closed for the same reason and restated in the same component —
    inline in its template, with no list anybody could read. They are right today; nothing was
    checking that they stay right, which is exactly how the kinds got here."""
    form = FORM.read_text()

    assert _options(form, _values(RuleTarget)) == _values(RuleTarget)
    assert _options(form, _values(RuleAction)) == _values(RuleAction)
    assert _cases(LANGUAGE.read_text(), "about") == _values(RuleTarget)
