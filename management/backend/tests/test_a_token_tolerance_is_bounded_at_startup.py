"""Both planes refuse a token tolerance out of bounds at startup (`FRD-134` FR-2, FR-3).

The bound lived in `JwtVerifier`, which is built only where OIDC is configured. With OIDC off the
console's backend started on any value, and refused to start the day somebody switched OIDC on.
The gateway's settings ask the same question (`gateway/tests/test_config.py`), so the two planes
cannot disagree about a token.
"""

from __future__ import annotations

import pytest
from aira_management.config.app_settings import ManagementSettings
from pydantic import ValidationError


@pytest.mark.parametrize("setting", ["oidc_clock_skew_seconds", "oidc_expiry_leeway_seconds"])
@pytest.mark.parametrize("value", [400.0, -1.0, float("nan")], ids=str)
def test_a_token_tolerance_out_of_bounds_refuses_to_start(setting: str, value: float) -> None:
    with pytest.raises(ValidationError, match=setting.upper()):
        ManagementSettings(oidc_issuer="", **{setting: value})


def test_the_defaults_start() -> None:
    settings = ManagementSettings()

    assert settings.oidc_clock_skew_seconds >= 0
    assert settings.oidc_expiry_leeway_seconds >= 0
