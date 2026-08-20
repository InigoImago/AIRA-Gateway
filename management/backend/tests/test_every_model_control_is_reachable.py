"""Every field that steers a model must be settable from the console.

Written after `numeric_id` — the integer a KIRA client addresses a model by — turned out to be
**displayed and unsettable**: the column existed, the API accepted it, the detail panel printed
*"KIRA id —"*, and no form ever asked. Every model catalogued from the console therefore went in
with `NULL`, which made it approvable, releasable, listed on the Gemini surface, and invisible to
the KIRA one.

A list of findings would have been true for one afternoon. This is the guard: when somebody adds a
writable field to `ModelSerializer` and no control reaches it, this test says so, by name.

**Why it reads the Angular source rather than the rendered DOM.** The question is not "does an
input exist" — an input bound to nothing renders perfectly. It is "does a value leave the console",
and the only place that is decided is where the payload is built. The Angular suite asserts the
other half (that the control renders and that typing into it changes the payload); this asserts
that no writable field is missing a payload key at all. Neither test alone would have caught the
KIRA id: there was no control *and* no key.

The exemptions are not a waiver list. Each one is a field that steers nothing, with the evidence,
and every entry is an invitation to delete the column rather than to keep it unreachable.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from aira_management.apps.catalog.serializers import ModelSerializer

from aira_gateway.catalog import ModelDeclaration

#: The console component that builds what a save sends.
CATALOG_COMPONENT = (
    Path(__file__).resolve().parents[3]
    / "management/frontend/src/app/features/models/model-catalog.ts"
)

#: The two places a catalog payload is assembled. Named rather than searched for across the whole
#: file: a help-text map in this same component has a `thinking:` key, and a guard that a comment
#: can satisfy is a guard that passes while the control is missing.
PAYLOAD_REGIONS = ("protected save(): void {", "private declarations()")

#: Writable fields the console deliberately does not offer, each with the reason it steers nothing.
#:
#: The entry here is read by **nothing**: it is carried to the gateway's read-model by the consumer
#: and dropped on the way into `ModelDeclaration`, which is the object every dispatch decision is
#: made from.
#:
#: So it is not an "unreachable control", it is a column without a reader. Giving it an input would
#: be the same defect wearing the other mask: a control somebody sets that changes nothing. If a
#: reader ever appears, it needs a control in the same change — that is what this dict is for.
#:
#: **`addressing` was here and is not any more.** It gained a reader on 2026-08-19 —
#: `ModelDeclaration.addressing`, which `VertexGeminiAdapter._target` uses to address a catalogued
#: model — and the console gained the Region control in the same change. This comment kept saying
#: neither for a day, and **no assertion could catch it**: the check subtracts the payload keys
#: *and* this dict, so a field that is genuinely offered is simply subtracted twice and the stale
#: claim sits here reading as current. Exactly the shape `LESSONS.md` §6 names — a claim no test can
#: reach is a claim that will be wrong.
STEERS_NOTHING = {
    "underlying_model": (
        "Read by nothing: stored, shipped to the gateway's read-model, and never consulted by any "
        "dispatch decision. `ADR-0011` rule 2 describes what it is for — a price attaching to the "
        "vendor's model when the caller-facing name differs — and nothing implements it."
    ),
}


def _payload_keys() -> set[str]:
    """The object keys the console actually sends, read from the two payload builders.

    Both spellings count. `save()` writes `numeric_id: this.kiraId()`; `declarations()` ends in
    `return { thinking, embedding, attachments }`, which is the same thing in shorthand — and a
    guard that only knew the first form would have reported three declarations as unreachable while
    they were being sent on every save. A false alarm teaches a reader to add exemptions.
    """
    source = CATALOG_COMPONENT.read_text(encoding="utf-8")
    keys: set[str] = set()
    for marker in PAYLOAD_REGIONS:
        start = source.index(marker)
        # To the end of the method: the first line that closes at method indentation.
        region = source[start : source.index("\n  }", start)]
        keys.update(re.findall(r"^\s+([a-z_]+):", region, re.M))
        for literal in re.findall(r"return\s*\{([^{}]*)\}", region):
            keys.update(re.findall(r"\b([a-z_]+)\b", literal))
    return keys


def test_every_writable_catalog_field_leaves_the_console() -> None:
    writable = {name for name, field in ModelSerializer().fields.items() if not field.read_only}
    unreachable = sorted(writable - _payload_keys() - set(STEERS_NOTHING))

    assert not unreachable, (
        "These catalog fields are writable through the API and no console control reaches them: "
        f"{unreachable}. That is how `numeric_id` shipped — a field the detail panel displayed and "
        "no form offered, leaving every console-created model unaddressable on the KIRA surface. "
        "Add the control, or add the field to STEERS_NOTHING with the evidence that nothing reads "
        "it."
    )


def test_every_fact_the_gateway_decides_on_can_be_set_in_the_console() -> None:
    """The same question from the other end, and the one that actually matters.

    `ModelDeclaration` is the object every dispatch decision is made from — whether a model may be
    called, what it may be asked for, what it costs, which integer names it. A field on it that no
    console control can set is a decision taken by a value nobody can enter, which is exactly what
    `numeric_id` was for the whole of the KIRA surface.

    The three excluded names are not catalog fields at all: `name` is the row's key (typed in the
    editor as the model name), and `declared`/`in_catalog` are derived by the gateway from whether
    a row exists and whether anybody wrote capabilities on it.
    """
    derived = {"name", "declared", "in_catalog"}
    decided_on = {field.name for field in dataclasses.fields(ModelDeclaration)} - derived
    unreachable = sorted(decided_on - _payload_keys())

    assert not unreachable, (
        f"The gateway decides on these and the console cannot set them: {unreachable}. Every "
        "value a dispatch decision reads must be enterable by whoever is accountable for it."
    )


def test_the_exemptions_are_still_writable_fields() -> None:
    """A guard on the guard: an exemption for a field that no longer exists hides the next one.

    The same shape as `test_public_repository_hygiene`'s empty-file-list check — a waiver nobody
    revisits silently grows to cover things it was never argued for.
    """
    writable = {name for name, field in ModelSerializer().fields.items() if not field.read_only}
    stale = sorted(set(STEERS_NOTHING) - writable)

    assert not stale, (
        f"STEERS_NOTHING names fields the serializer no longer writes: {stale}. Remove them, or "
        "the next field with that name inherits an exemption argued for something else."
    )
