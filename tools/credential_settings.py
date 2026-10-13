"""Which settings are credentials — one answer, read off both planes' settings classes (`FRD-116`).

Four places act on that answer: what `vault_setup.py --from-env` copies into Vault, what
`config_render.py` refuses to write into a `.env`, what the showcase's `vault-init` provisions, and
what every config example lists under `secrets.in_vault`. A hand-written list in each is four lists
that disagree, and the disagreement is silent in both directions: a name no settings class reads is
a credential stored where nothing looks for it, and a real credential missing from the copy list is
left behind in plaintext by an operator who was told the copy was done.

**Recognised by shape, not remembered by name** (`LESSONS.md` §1): a setting whose name ends in a
credential suffix and whose value is text. The text condition is what keeps a switch such as
`AIRA_CORS_ALLOW_CREDENTIALS` — a boolean that only *mentions* credentials — out of Vault. The other
three places are checked against this module by `tools/tests/test_one_list_of_credentials.py`.
"""

from __future__ import annotations

import types
import typing
from functools import cache

#: What a credential's name ends in. `_SECRET_KEY` is Django's signing key, which ends in neither
#: `_SECRET` nor `_API_KEY` and is the one credential every installation has.
#: `_SERVICE_ACCOUNT_JSON` is how a cloud service account's key is named when written from memory.
SUFFIXES = (
    "_API_KEY",
    "_PASSWORD",
    "_SECRET",
    "_SECRET_KEY",
    "_CREDENTIALS",
    "_TOKEN",
    "_SERVICE_ACCOUNT_JSON",
)

#: The two credentials that authenticate *to* Vault, so Vault cannot hold them: the AppRole's
#: secret-id, and the development token `ADR-0007` allows for `local`.
VAULT_CREDENTIALS = frozenset({"VAULT_SECRET_ID", "VAULT_TOKEN"})


def _is_text(annotation: object) -> bool:
    """Whether a settings field holds text — `str`, or an optional `str`."""
    if annotation is str:
        return True
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return str in typing.get_args(annotation)
    return False


@cache
def _fields() -> dict[str, object]:
    """Every `AIRA_*` either plane declares, with the field's type. Imported, never parsed."""
    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    found: dict[str, object] = {}
    for settings in (GatewaySettings, ManagementSettings):
        for name, field in settings.model_fields.items():
            found[f"AIRA_{name.upper()}"] = field.annotation
    return found


def settings_names() -> frozenset[str]:
    """Every `AIRA_*` name a settings class reads, on either plane."""
    return frozenset(_fields())


@cache
def credentials() -> frozenset[str]:
    """Every setting that carries a credential, on either plane: what Vault holds."""
    return frozenset(
        name
        for name, annotation in _fields().items()
        if name.endswith(SUFFIXES) and _is_text(annotation)
    )


def is_credential(name: str) -> bool:
    """Whether a value under `name` must never be written into an environment file.

    A credential setting, a Vault credential, or a credential-shaped name that **no** setting
    declares. The last is refused rather than let through because it reads nothing: a key written
    under a misremembered name is still a key, in plaintext, in a file that is copied around.
    """
    if name in VAULT_CREDENTIALS or name in credentials():
        return True
    return name.endswith(SUFFIXES) and name not in settings_names()
