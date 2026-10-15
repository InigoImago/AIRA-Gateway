"""Shared setup for the Management tests.

**Roles come from groups (`ADR-0017`)**, so every test that needs one needs the mapping an
installation configures. It is set here once rather than per file: a suite that configures the
service differently from production tests a different service, and thirteen copies of one mapping
is thirteen chances for one of them to drift.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from aira_management.config.runtime import get_settings
from django.core.cache import cache

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
def _a_fresh_cache() -> Iterator[None]:
    """Every test starts with an empty Django cache, which is where DRF counts each caller's
    requests. SQLite hands the first user of every test the same primary key after the rollback,
    so without this a suite running faster than the throttle's window shares one allowance across
    tests — and answers `429` to whichever test happens to come six hundred requests later."""
    cache.clear()
    yield
    cache.clear()


@contextmanager
def setting(**values: Any) -> Iterator[None]:
    """Change values on the process's settings object for a block, and put them back after."""
    settings = get_settings()
    previous = {key: getattr(settings, key) for key in values}
    for key, value in values.items():
        object.__setattr__(settings, key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            object.__setattr__(settings, key, value)


@pytest.fixture(autouse=True)
def strict_data_on() -> Iterator[None]:
    """Every Management test runs as a fresh installation does: strict data mode on (`FRD-627`).

    Pinned rather than read from the environment, so a developer's `.env` with the mode off cannot
    turn the suite into one about a different installation.
    """
    with setting(strict_data_mode=True):
        yield


@pytest.fixture
def strict_data_off(strict_data_on: None) -> Iterator[None]:
    """The installation that turned strict data mode off, where each use case's own setting and
    every stored grant decide (`ADR-0036`). Asked for by name by every test about that world."""
    with setting(strict_data_mode=False):
        yield


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
