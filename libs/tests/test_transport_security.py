"""The one rule that decides whether authentication can be defeated from the network.

Not "does `urlsplit` work" — these assert the *decision*: which configuration a deployment is
allowed to boot with. The loopback carve-out is tested as deliberately as the refusal, because a
rule that refuses a normal sidecar deployment is one an operator escapes by setting
`AIRA_ENVIRONMENT=local`, which switches every other check off too.
"""

from __future__ import annotations

import pytest

from aira_common.transport_security import is_plaintext, plaintext_problems


@pytest.mark.parametrize(
    "url",
    [
        "http://keycloak.example.com/realms/aira",
        "http://10.1.2.3:8080/realms/aira",
        "HTTP://Keycloak.Example.COM/realms/aira",
        "http://vault.internal:8200",
    ],
)
def test_plaintext_to_a_network_host_is_refused(url: str) -> None:
    assert is_plaintext(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://keycloak.example.com/realms/aira",
        "http://localhost:8080/realms/aira",
        "http://127.0.0.1:8200",
        "http://[::1]:8200",
        "HTTP://LocalHost:8080/x",
    ],
)
def test_tls_and_loopback_are_accepted(url: str) -> None:
    """A sidecar on loopback is a normal deployment and its traffic never reaches a network."""
    assert is_plaintext(url) is False


def test_unset_is_not_a_transport_problem() -> None:
    """ "Unset" is a different problem with a different message. Reporting it here would tell an
    operator to add TLS to a setting they never filled in."""
    assert is_plaintext("") is False
    assert is_plaintext("   ") is False
    assert plaintext_problems([("AIRA_OIDC_ISSUER", "")]) == []


def test_a_malformed_value_is_not_reported_as_plaintext() -> None:
    """Same reason: it is a different fault, and claiming it is a TLS one sends the reader to the
    wrong fix."""
    assert is_plaintext("not a url at all") is False


def test_every_plaintext_url_is_reported_at_once_and_named() -> None:
    """A review that reports one problem per attempt is four deployments (`ADR-0015`)."""
    problems = plaintext_problems(
        [
            ("AIRA_OIDC_ISSUER", "http://kc.example/realms/aira"),
            ("VAULT_ADDR", "http://vault.example:8200"),
            ("AIRA_OIDC_JWKS_URI", "https://kc.example/certs"),
        ]
    )

    assert len(problems) == 2
    assert any("AIRA_OIDC_ISSUER" in p for p in problems)
    assert any("VAULT_ADDR" in p for p in problems)
    # The value is echoed so the reader can see *which* of several environments is wrong.
    assert any("http://vault.example:8200" in p for p in problems)


def test_one_setting_naming_several_urls_reports_every_one_of_them() -> None:
    """**Pairs, not a mapping**, and this is the case that distinguishes them.

    `AIRA_OIDC_ISSUERS` (`FRD-118`) configures a realm per entry, so the gateway names
    `AIRA_OIDC_ISSUER` once per realm. Against a `dict` the last one won and the rest were dropped
    before this function saw them — so a plaintext realm listed anywhere but last produced no
    problem at all, on the check this module's docstring calls *the one misconfiguration that
    defeats authentication outright*.

    The plaintext URL is deliberately **first**, because that is the ordering the collapse hid.
    """
    problems = plaintext_problems(
        [
            ("AIRA_OIDC_ISSUER", "http://insecure.example/realms/a"),
            ("AIRA_OIDC_ISSUER", "https://secure.example/realms/b"),
        ]
    )

    assert len(problems) == 1
    assert "http://insecure.example/realms/a" in problems[0]
