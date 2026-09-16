"""The notice as a reader receives it: one language, this installation's values filled in.

**The version covers every language as rendered here**, not only the one being read. A reader who
acknowledged the German notice has acknowledged the notice, so switching to English asks nothing
new — but a changed controller, retention or text in any language is a new version for everybody.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from aira_common.permissions import RoleDefinition
from aira_management.apps.privacy.activities import ACTIVITIES, PERSONAL_DATA_PERMISSIONS
from aira_management.apps.privacy.edition import EDITION
from aira_management.apps.privacy.texts import (
    ACTIVITY_FIELDS,
    LANGUAGES,
    SECTIONS,
    Language,
    load,
)
from aira_management.config.app_settings import ManagementSettings


@dataclass(frozen=True, slots=True)
class RoleAccess:
    """A role by its name, and which of `PERSONAL_DATA_PERMISSIONS` it holds, in that order."""

    label: str
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Installation:
    """The configuration a notice states. Read from settings and roles, never from a request."""

    controller: str
    dpo_contact: str
    works_agreement: str
    store_payloads: bool
    log_retention_days: int
    default_retention_days: int
    api_key_default_days: int
    api_key_max_days: int
    roles: tuple[RoleAccess, ...]

    @classmethod
    def of(cls, settings: ManagementSettings, roles: tuple[RoleDefinition, ...]) -> Installation:
        return cls(
            controller=settings.privacy_controller.strip(),
            dpo_contact=settings.privacy_dpo_contact.strip(),
            works_agreement=settings.privacy_works_agreement.strip(),
            store_payloads=settings.store_payloads,
            log_retention_days=settings.log_retention_days,
            default_retention_days=settings.default_retention_days,
            api_key_default_days=settings.api_key_default_days,
            api_key_max_days=settings.api_key_max_days,
            roles=tuple(
                RoleAccess(
                    role.label,
                    tuple(str(p) for p in PERSONAL_DATA_PERMISSIONS if p in role.permissions),
                )
                for role in roles
            ),
        )


def _days(language: Language, days: int) -> str:
    """``days`` in words; zero or less is no limit, as the retention sweep reads it."""
    if days <= 0:
        return language.values["no_limit"]
    return language.values["days"].format(days=days)


def values(language: Language, installation: Installation) -> dict[str, str]:
    """Every placeholder's text in ``language``. An empty setting prints its `unset_*` sentence."""
    configured = {
        "controller": installation.controller,
        "dpo_contact": installation.dpo_contact,
        "works_agreement": installation.works_agreement,
    }
    filled = {name: text or language.values[f"unset_{name}"] for name, text in configured.items()}
    filled["content_storage"] = language.values[
        "content_stored" if installation.store_payloads else "content_not_stored"
    ]
    filled["log_retention"] = _days(language, installation.log_retention_days)
    filled["default_retention"] = _days(language, installation.default_retention_days)
    filled["key_lifetime"] = language.values["days"].format(days=installation.api_key_default_days)
    filled["key_max_lifetime"] = language.values["days"].format(days=installation.api_key_max_days)
    return filled


def access(language: Language, installation: Installation) -> list[str]:
    """One line per role: its name, and what it may do to other people's data."""
    return [
        f"{role.label}: "
        + "; ".join(language.access[name] for name in role.permissions or ("none",))
        for role in installation.roles
    ]


def render(code: str, installation: Installation) -> dict[str, Any]:
    """``code``'s notice, filled in. The shape the console renders, without the reader's state."""
    language = load(code)
    fill = values(language, installation)

    def text(raw: str) -> str:
        # `format_map` over a closed set: `texts.parse` refused any other name at load.
        return raw.format_map(fill)

    sections = []
    for key in SECTIONS:
        section = language.sections[key]
        rendered: dict[str, Any] = {
            "key": key,
            "title": section.title,
            "paragraphs": [text(p) for p in section.paragraphs],
            "items": [
                *(access(language, installation) if key == "access" else ()),
                *(text(i) for i in section.items),
            ],
        }
        if key == "activities":
            rendered["activities"] = [
                {
                    "key": activity.key,
                    "title": language.activities[activity.key]["title"],
                    "fields": [
                        {
                            "key": field,
                            "label": language.labels[field],
                            "text": text(language.activities[activity.key][field]),
                        }
                        for field in ACTIVITY_FIELDS
                    ],
                }
                for activity in ACTIVITIES
            ]
        sections.append(rendered)
    return {
        "language": code,
        "title": language.title,
        "labels": dict(language.labels),
        "sections": sections,
    }


def version(installation: Installation) -> str:
    """``<edition>.<digest>`` over every language as this installation renders it."""
    rendered = {code: render(code, installation) for code in LANGUAGES}
    canonical = json.dumps(rendered, sort_keys=True, ensure_ascii=False).encode()
    return f"{EDITION}.{hashlib.sha256(canonical).hexdigest()[:16]}"


def languages() -> list[dict[str, str]]:
    """Each language by code and by its own name — `Deutsch`, not `German`."""
    return [{"code": code, "name": load(code).name} for code in LANGUAGES]


def negotiate(accept_language: str, default: str) -> str:
    """The best language this installation has for an ``Accept-Language`` header.

    Primary subtags only (`de-AT` reads German), in the order of their ``q``; anything
    unparseable is skipped rather than refused, because a browser's header is not the caller's
    choice.
    """
    ranked: list[tuple[float, int, str]] = []
    for position, part in enumerate(accept_language.split(",")):
        tag, _, params = part.strip().partition(";")
        weight = 1.0
        if params.strip().startswith("q="):
            try:
                weight = float(params.strip()[2:])
            except ValueError:
                continue
        primary = tag.strip().split("-")[0].lower()
        if primary and weight > 0:
            ranked.append((-weight, position, primary))
    for _, _, primary in sorted(ranked):
        if primary in LANGUAGES:
            return primary
    return default
