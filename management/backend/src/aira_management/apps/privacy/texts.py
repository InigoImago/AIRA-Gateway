"""The notice's words, one TOML file per language in `texts/` (`FRD-625` FR-6).

**A language is a file.** Dropping `fr.toml` beside the others offers French; nothing else names the
set. What a file must contain is not left to the translator's care: `load` refuses a file that
lacks a section, an activity or a label the others have, or that has one they do not, and
`test_privacy_notice_texts.py` holds every placeholder to the same set in every language — a
translation that dropped `{controller}` would print a notice naming nobody, in that language only.
"""

from __future__ import annotations

import string
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from aira_management.apps.privacy.activities import ACTIVITY_KEYS, PERSONAL_DATA_PERMISSIONS

TEXTS = Path(__file__).resolve().parent / "texts"

#: Every language this installation has a notice in, by ISO 639-1 code: the files in `texts/`.
LANGUAGES: tuple[str, ...] = tuple(sorted(path.stem for path in TEXTS.glob("*.toml")))

#: The notice's sections, in the order they are read. `activities` carries the register.
SECTIONS: tuple[str, ...] = (
    "controller",
    "scope",
    "activities",
    "no_monitoring",
    "access",
    "recipients",
    "automated_decisions",
    "ai",
    "browser_storage",
    "rights",
    "changes",
)

#: What every activity states — Art. 13(1)(c)–(e) and (2)(a) DSGVO, one field each.
ACTIVITY_FIELDS: tuple[str, ...] = ("data", "purpose", "legal_basis", "retention", "recipients")

#: The console's own words around the notice, so the window is in the notice's language too.
LABELS: tuple[str, ...] = (
    "edition",
    "language",
    "acknowledge",
    "acknowledged",
    "open_notice",
    "load_failed",
    "acknowledge_failed",
    "due_first",
    "due_changed",
    "due_month",
    "due_always",
    *ACTIVITY_FIELDS,
)

#: What a role may do to other people's data, one phrase per permission, and the phrase for a role
#: that may do none of it.
ACCESS: tuple[str, ...] = (*(str(p) for p in PERSONAL_DATA_PERMISSIONS), "none")

#: What `notice.render` fills in from this installation's configuration, and nothing else.
PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "controller",
        "dpo_contact",
        "works_agreement",
        "content_storage",
        "log_retention",
        "default_retention",
        "key_lifetime",
        "key_max_lifetime",
    }
)

#: The words those values are made of, per language: `days` carries `{days}`, and each `unset_*`
#: is what an empty setting prints instead of a blank.
VALUES: tuple[str, ...] = (
    "days",
    "no_limit",
    "content_stored",
    "content_not_stored",
    "unset_controller",
    "unset_dpo_contact",
    "unset_works_agreement",
)


class NoticeTextError(ValueError):
    """A language file that does not say what every other one says."""


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    paragraphs: tuple[str, ...]
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Language:
    """One language's complete notice, before an installation's values are filled in."""

    code: str
    name: str
    title: str
    labels: MappingProxyType[str, str]
    values: MappingProxyType[str, str]
    access: MappingProxyType[str, str]
    sections: MappingProxyType[str, Section]
    #: Activity key → field → text.
    activities: MappingProxyType[str, MappingProxyType[str, str]]


def placeholders(text: str) -> set[str]:
    """The ``{names}`` a text asks to have filled in."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def strings(language: Language) -> dict[str, str]:
    """Every text in ``language`` by a path that is the same in every language."""
    found = {"title": language.title}
    found |= {f"labels.{key}": text for key, text in language.labels.items()}
    found |= {f"values.{key}": text for key, text in language.values.items()}
    found |= {f"access.{key}": text for key, text in language.access.items()}
    for key, section in language.sections.items():
        found[f"sections.{key}.title"] = section.title
        for index, text in enumerate(section.paragraphs):
            found[f"sections.{key}.paragraphs.{index}"] = text
        for index, text in enumerate(section.items):
            found[f"sections.{key}.items.{index}"] = text
    for key, fields in language.activities.items():
        found |= {f"activities.{key}.{name}": text for name, text in fields.items()}
    return found


def _keys(where: str, table: Any, expected: tuple[str, ...], code: str) -> dict[str, Any]:
    if not isinstance(table, dict):
        raise NoticeTextError(f"{code}.toml: [{where}] is missing")
    missing = [key for key in expected if key not in table]
    extra = sorted(set(table) - set(expected))
    if missing or extra:
        raise NoticeTextError(
            f"{code}.toml: [{where}] must have exactly {', '.join(expected)} — "
            f"missing {missing or 'none'}, unknown {extra or 'none'}"
        )
    return table


def _text(where: str, value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NoticeTextError(f"{code}.toml: {where} must be a text that is not empty")
    return value.strip()


def _texts(where: str, value: Any, code: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise NoticeTextError(f"{code}.toml: {where} must be a list of texts")
    return tuple(_text(f"{where}[{index}]", item, code) for index, item in enumerate(value))


def parse(code: str, raw: dict[str, Any]) -> Language:
    """One language from its parsed TOML, refused whole if any part is missing or unknown."""
    meta = _keys("meta", raw.get("meta"), ("name", "title"), code)
    labels = _keys("labels", raw.get("labels"), LABELS, code)
    values = _keys("values", raw.get("values"), VALUES, code)
    access = _keys("access", raw.get("access"), ACCESS, code)
    sections = _keys("sections", raw.get("sections"), SECTIONS, code)
    activities = _keys("activities", raw.get("activities"), ACTIVITY_KEYS, code)
    unknown = sorted(set(raw) - {"meta", "labels", "values", "access", "sections", "activities"})
    if unknown:
        raise NoticeTextError(f"{code}.toml: unknown tables {unknown}")

    parsed_sections: dict[str, Section] = {}
    for key in SECTIONS:
        body = sections[key]
        if not isinstance(body, dict) or set(body) - {"title", "paragraphs", "items"}:
            raise NoticeTextError(f"{code}.toml: [sections.{key}] has title, paragraphs, items")
        section = Section(
            title=_text(f"sections.{key}.title", body.get("title"), code),
            paragraphs=_texts(f"sections.{key}.paragraphs", body.get("paragraphs"), code),
            items=_texts(f"sections.{key}.items", body.get("items"), code),
        )
        if not section.paragraphs:
            raise NoticeTextError(f"{code}.toml: [sections.{key}] says nothing")
        parsed_sections[key] = section

    parsed_activities: dict[str, MappingProxyType[str, str]] = {}
    for key in ACTIVITY_KEYS:
        fields = _keys(f"activities.{key}", activities[key], ("title", *ACTIVITY_FIELDS), code)
        parsed_activities[key] = MappingProxyType(
            {name: _text(f"activities.{key}.{name}", fields[name], code) for name in fields}
        )

    language = Language(
        code=code,
        name=_text("meta.name", meta["name"], code),
        title=_text("meta.title", meta["title"], code),
        labels=MappingProxyType({k: _text(f"labels.{k}", labels[k], code) for k in LABELS}),
        values=MappingProxyType({k: _text(f"values.{k}", values[k], code) for k in VALUES}),
        access=MappingProxyType({k: _text(f"access.{k}", access[k], code) for k in ACCESS}),
        sections=MappingProxyType(parsed_sections),
        activities=MappingProxyType(parsed_activities),
    )
    for where, text in strings(language).items():
        allowed = {"days"} if where == "values.days" else set()
        if where == "values.days" and placeholders(text) != allowed:
            raise NoticeTextError(f"{code}.toml: values.days must contain {{days}}")
        allowed = allowed if where.startswith(("values.", "access.")) else set(PLACEHOLDERS)
        unknown_names = placeholders(text) - allowed
        if unknown_names:
            raise NoticeTextError(
                f"{code}.toml: {where} asks for {sorted(unknown_names)}, which nothing fills in"
            )
    return language


@cache
def load(code: str) -> Language:
    """``code``'s notice. Unknown codes are the caller's to refuse before asking."""
    if code not in LANGUAGES:
        raise KeyError(code)
    with (TEXTS / f"{code}.toml").open("rb") as handle:
        return parse(code, tomllib.load(handle))
