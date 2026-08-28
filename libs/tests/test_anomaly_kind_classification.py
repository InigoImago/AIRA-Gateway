"""Every rule kind is classified, and each one exactly once.

`RATE_KINDS`, `RATIO_KINDS` and `EVENT_KINDS` are the three answers to *"what does this kind's
threshold mean"*, and they are read by four things: the serializer's bounds (`> 100` is a mistake
for a share and the point of a ratio), `threshold_unit`'s words on a form, `needs_sample`, and the
console's units table.

`EVENT_KINDS` was read by **none** of them. It sat beside the two that are read, as a comment with
a name — the *dead definition* this project has already removed three times (`realm_roles`,
`_injection_verdict`, `ratelimit._capacity`), and the more misleading variety: a reader adding a
fourth kind finds three sets, concludes that classifying their kind into one of them is how the
unit is decided, and it is not. `threshold_unit` falls through to `"occurrences"` and `needs_sample`
to `False` for **anything** the first two do not name, so an unclassified kind gets an event's
answers by accident and nothing anywhere says so.

Asserting the partition is the cheap counterpart `LESSONS.md` §1 asks for — *"apply it, or assert
the two copies are equal"*. It gives the third set a reader, and it makes adding a kind without
deciding what its threshold means fail here, at the point somebody can still answer the question.
"""

from __future__ import annotations

from aira_common.anomalies import EVENT_KINDS, RATE_KINDS, RATIO_KINDS, RuleKind, threshold_unit


def test_the_three_sets_cover_the_whole_vocabulary() -> None:
    classified = RATE_KINDS | RATIO_KINDS | EVENT_KINDS

    assert classified == set(RuleKind), (
        "kinds with no classification: "
        f"{sorted(k.value for k in set(RuleKind) - classified)}\n"
        "kinds classified that the vocabulary does not define: "
        f"{sorted(getattr(k, 'value', k) for k in classified - set(RuleKind))}\n\n"
        "An unclassified kind still gets a unit and a sample rule — the fallthrough ones — so the "
        "form prints 'occurrences' about a share of requests and nothing fails."
    )


def test_no_kind_is_classified_twice() -> None:
    """Two of these decide opposite bounds: a `RATE` threshold above 100 is refused as impossible
    and a `RATIO` threshold at or below 100 is refused as meaningless. A kind in both sets would
    be refused whatever anybody typed."""
    assert not RATE_KINDS & RATIO_KINDS
    assert not RATE_KINDS & EVENT_KINDS
    assert not RATIO_KINDS & EVENT_KINDS


def test_each_classification_gets_its_own_words() -> None:
    """The three sets exist to be told apart on a form, so the three answers have to differ."""
    groups = {"rate": RATE_KINDS, "ratio": RATIO_KINDS, "event": EVENT_KINDS}
    empty = sorted(name for name, group in groups.items() if not group)
    assert not empty, f"a classification with no kinds in it decides nothing: {empty}"

    units = {threshold_unit(sorted(group)[0]) for group in groups.values()}

    assert len(units) == 3, f"two classifications print the same unit: {sorted(units)}"
