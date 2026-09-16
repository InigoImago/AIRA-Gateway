"""Every language says the same things (`FRD-625` FR-6).

A translation is refused whole when it lacks a part the others have, has a part they do not, or
asks for a value nothing fills in. And a placeholder that one language dropped would print a notice
naming no controller in that language only — which no reader of the other language would notice.
"""

from __future__ import annotations

import copy
import tomllib
from typing import Any

import pytest
from aira_management.apps.privacy import notice
from aira_management.apps.privacy.activities import ACTIVITY_KEYS
from aira_management.apps.privacy.texts import (
    ACCESS,
    LABELS,
    LANGUAGES,
    SECTIONS,
    TEXTS,
    VALUES,
    NoticeTextError,
    load,
    parse,
    placeholders,
    strings,
)
from aira_management.apps.usecases.models import UseCase
from django.core.validators import MaxValueValidator, MinValueValidator

#: How each language writes a range, so the check reads the sentence rather than any "3650".
_WORD_FOR_TO = {"de": "bis", "en": "to"}


def _raw(code: str = "en") -> dict[str, Any]:
    with (TEXTS / f"{code}.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_the_notice_exists_in_more_than_one_language() -> None:
    assert {"de", "en"} <= set(LANGUAGES)


@pytest.mark.parametrize("code", LANGUAGES)
def test_every_language_parses_with_every_part(code: str) -> None:
    language = load(code)
    assert tuple(language.sections) == SECTIONS
    assert tuple(language.activities) == ACTIVITY_KEYS
    assert tuple(language.labels) == LABELS
    assert tuple(language.values) == VALUES
    assert tuple(language.access) == ACCESS


def test_every_language_asks_for_the_same_values_in_the_same_places() -> None:
    reference = {path: placeholders(text) for path, text in strings(load(LANGUAGES[0])).items()}
    for code in LANGUAGES[1:]:
        other = {path: placeholders(text) for path, text in strings(load(code)).items()}
        # Paragraphs may be split differently, so compare per part rather than per paragraph index.
        assert _per_part(other) == _per_part(reference), code


def _per_part(found: dict[str, set[str]]) -> dict[str, set[str]]:
    parts: dict[str, set[str]] = {}
    for path, names in found.items():
        part = path.split(".paragraphs.")[0].split(".items.")[0]
        parts.setdefault(part, set()).update(names)
    return parts


def test_a_language_without_a_section_is_refused() -> None:
    raw = _raw()
    del raw["sections"]["rights"]
    with pytest.raises(NoticeTextError, match="rights"):
        parse("xx", raw)


def test_a_language_with_a_section_nobody_else_has_is_refused() -> None:
    raw = _raw()
    raw["sections"]["marketing"] = {"title": "T", "paragraphs": ["P"]}
    with pytest.raises(NoticeTextError, match="marketing"):
        parse("xx", raw)


def test_a_language_without_an_activity_is_refused() -> None:
    raw = _raw()
    del raw["activities"]["content_reads"]
    with pytest.raises(NoticeTextError, match="content_reads"):
        parse("xx", raw)


def test_an_activity_missing_its_legal_basis_is_refused() -> None:
    raw = _raw()
    del raw["activities"]["request_record"]["legal_basis"]
    with pytest.raises(NoticeTextError, match="legal_basis"):
        parse("xx", raw)


def test_an_empty_text_is_refused_rather_than_printed() -> None:
    raw = _raw()
    raw["activities"]["content"]["retention"] = "   "
    with pytest.raises(NoticeTextError, match="retention"):
        parse("xx", raw)


def test_a_placeholder_nothing_fills_in_is_refused() -> None:
    raw = _raw()
    raw["sections"]["controller"]["paragraphs"][0] += " {controler}"
    with pytest.raises(NoticeTextError, match="controler"):
        parse("xx", raw)


def test_days_without_the_number_is_refused() -> None:
    raw = _raw()
    raw["values"]["days"] = "some days"
    with pytest.raises(NoticeTextError, match="days"):
        parse("xx", raw)


def test_a_permission_phrase_missing_is_refused() -> None:
    """A role holding it would otherwise print nothing about what it may read."""
    raw = copy.deepcopy(_raw())
    del raw["access"]["payload.read_any"]
    with pytest.raises(NoticeTextError, match="payload.read_any"):
        parse("xx", raw)


def test_an_unknown_table_is_refused() -> None:
    raw = _raw()
    raw["footer"] = {"text": "x"}
    with pytest.raises(NoticeTextError, match="footer"):
        parse("xx", raw)


@pytest.mark.parametrize("code", LANGUAGES)
def test_the_retention_bounds_the_notice_states_are_the_ones_a_use_case_accepts(code: str) -> None:
    """The notice says "1 to 3650 days"; the model is what enforces it. One is checked by the other,
    so moving the bound without the sentence fails here."""
    validators = UseCase._meta.get_field("retention_days").validators
    low = next(v.limit_value for v in validators if isinstance(v, MinValueValidator))
    high = next(v.limit_value for v in validators if isinstance(v, MaxValueValidator))
    text = (TEXTS / f"{code}.toml").read_text(encoding="utf-8")
    assert low == 1
    assert f"1 {_WORD_FOR_TO[code]} {high} " in text


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("de-DE,de;q=0.9,en;q=0.8", "de"),
        ("en-GB,en;q=0.9", "en"),
        ("fr-FR,fr;q=0.9,en;q=0.5", "en"),
        ("fr;q=1.0, de;q=0.1, en;q=0.5", "en"),
        ("fr-FR", "de"),
        ("", "de"),
        ("de;q=0, en", "en"),
        ("de;q=banana, en", "en"),
    ],
)
def test_the_browser_language_is_negotiated_against_the_files_that_exist(
    header: str, expected: str
) -> None:
    assert notice.negotiate(header, "de") == expected
