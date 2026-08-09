"""Shared setup for the Management tests.

**Roles come from groups (`ADR-0017`)**, so every test that needs one needs the mapping an
installation configures. It is set here once rather than per file: a suite that configures the
service differently from production tests a different service, and thirteen copies of one mapping
is thirteen chances for one of them to drift.
"""

from __future__ import annotations

from typing import Any

import pytest

from aira_common.roles import CONFIGURABLE_ROLES

#: The mapping, written as the string a deployment sets — parsed by the same code, so a test
#: cannot pass against a mapping the parser would have refused.
ROLE_GROUPS = (
    "global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung"
)

GROUP_FOR = {
    "global-admin": "/aira/global-admins",
    "it-security": "/aira/it-security",
    "it-steuerung": "/aira/it-steuerung",
}


@pytest.fixture(autouse=True)
def role_groups(settings: Any) -> str:
    """Configure the group → role mapping for every Management test."""
    settings.AIRA_ROLE_GROUPS = ROLE_GROUPS
    return ROLE_GROUPS


def role_claims(*roles: str) -> dict[str, Any]:
    """The token claims that confer ``roles`` — a `groups` claim, because that is now the only
    thing that can.

    **Refuses `use-case-admin` and `use-case-user` by name.** They are no longer roles: they are a
    group's relationship to *one* use case, held in `UseCaseGroupGrant`. Accepting them here and
    quietly granting nothing would leave a suite full of tests that still run, still pass, and no
    longer exercise the authority they are named after — which is the failure this project has
    recorded four times and would not notice a fifth.
    """
    unknown = [role for role in roles if role not in {str(r) for r in CONFIGURABLE_ROLES}]
    if unknown:
        raise AssertionError(
            f"{unknown} cannot be granted by a group (ADR-0017). An organisation-wide role is one "
            f"of {sorted(str(r) for r in CONFIGURABLE_ROLES)}; administering or belonging to a use "
            "case is an object grant — give the user one instead."
        )
    return {"groups": [GROUP_FOR[role] for role in roles]}
