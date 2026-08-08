"""Credentials do not survive into a stored payload (`FRD-406`, 2026-08-08).

The `Redactor` hook has existed since Phase 1 and did nothing, which was deliberate and was also
the gap `ADR-0007` left open: a stored prompt is a verbatim copy of whatever a caller sent, kept
for as long as retention says and readable by anyone who can read the table. "Here is our API key,
write me a curl command" is not an exotic prompt.

The two halves are equally load-bearing. Credentials must go; **business content must stay**, or
the stored payload becomes useless for the debugging and evidence it exists for and the deployment
switches storage off — strictly worse than storing it.
"""

from __future__ import annotations

import pytest

from aira_gateway.persistence.redaction import (
    PLACEHOLDER,
    PatternRedactor,
    RedactionMisconfigured,
    build_redactor,
)

AIRA_KEY = "aira_abcd1234_00112233445566778899aabbccddeeff"
GOOGLE_KEY = "AIzaSyD-1234567890abcdefghijklmnopqrstu"
OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwx"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZGEifQ.c2lnbmF0dXJl"


def _prompt(text: str) -> dict[str, object]:
    return {"contents": [{"parts": [{"text": text}]}]}


# ---- what must not survive ------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret", [AIRA_KEY, GOOGLE_KEY, OPENAI_KEY, JWT, "Authorization: Bearer abc.def.ghi"]
)
def test_a_credential_pasted_into_a_prompt_is_removed(secret: str) -> None:
    payload = _prompt(f"Write me a curl command using {secret} please")

    stored = build_redactor().redact(payload)

    assert secret not in str(stored)
    assert PLACEHOLDER in str(stored)


def test_a_private_key_block_is_removed_body_and_all() -> None:
    """A partial match here would leave the key material and remove only its label."""
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK\nsecretline\n-----END RSA PRIVATE KEY-----"

    stored = build_redactor().redact(_prompt(f"Deploy with:\n{pem}\nthanks"))

    assert "secretline" not in str(stored)
    assert "MIIEowIBAAK" not in str(stored)


def test_it_looks_everywhere_a_string_can_be() -> None:
    """A credential in the response, in a nested list, or under a key nobody anticipated is the
    same credential."""
    payload = {
        "candidates": [{"content": {"parts": [{"text": f"your key is {GOOGLE_KEY}"}]}}],
        "meta": {"notes": [f"and again {AIRA_KEY}"]},
    }

    stored = build_redactor().redact(payload)

    assert GOOGLE_KEY not in str(stored)
    assert AIRA_KEY not in str(stored)


# ---- what must survive ----------------------------------------------------------------------


def test_ordinary_business_content_is_untouched() -> None:
    """The half that keeps stored payloads worth keeping. Names, numbers and prose are the work."""
    text = "Kundennummer 4711, Frau Müller, Rechnung über 1.234,56 EUR vom 03.08.2026"

    stored = build_redactor().redact(_prompt(text))

    assert stored["contents"][0]["parts"][0]["text"] == text  # type: ignore[index]


def test_the_shape_of_the_payload_is_preserved() -> None:
    """A payload replaced wholesale is one nobody looks at twice — it has to stay readable next to
    the response it produced."""
    payload = _prompt(f"key {AIRA_KEY}")

    stored = build_redactor().redact(payload)

    assert list(stored) == ["contents"]
    assert isinstance(stored["contents"][0]["parts"], list)  # type: ignore[index]


def test_keys_are_structure_and_are_not_rewritten() -> None:
    """Rewriting a key would change the shape and break every reader that indexes into it."""
    stored = PatternRedactor((r"secret",)).redact({"secret": "secret"})

    assert list(stored) == ["secret"]
    assert stored["secret"] == PLACEHOLDER


def test_non_strings_pass_through() -> None:
    stored = build_redactor().redact({"tokens": 42, "ok": True, "nothing": None})

    assert stored == {"tokens": 42, "ok": True, "nothing": None}


# ---- configuration --------------------------------------------------------------------------


def test_a_deployment_can_add_its_own_format() -> None:
    stored = build_redactor("PN-[0-9]{6}").redact(_prompt("employee PN-123456 asked"))

    assert "PN-123456" not in str(stored)


def test_adding_one_does_not_replace_the_built_ins() -> None:
    """A replacing setting would stop redacting Google keys the first time somebody used it."""
    stored = build_redactor("PN-[0-9]{6}").redact(_prompt(f"PN-123456 and {GOOGLE_KEY}"))

    assert GOOGLE_KEY not in str(stored)


def test_an_invalid_pattern_stops_the_gateway() -> None:
    """Silently compiling to nothing would be a redaction rule that appears configured and removes
    nothing — an absent control wearing a present one's badge (`FRD-125`)."""
    with pytest.raises(RedactionMisconfigured):
        build_redactor("([unclosed")


def test_a_nested_quantifier_is_refused() -> None:
    """These run over caller-supplied text on the write path; `ADR-0007` refuses the same shape in
    a pipeline config for the same reason."""
    with pytest.raises(RedactionMisconfigured):
        build_redactor("(a+)+$")
