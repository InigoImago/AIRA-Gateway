"""The trust store our HTTP clients verify against holds no root whose keys are compromised.

Every upstream model call, every Vault read and the catalog's reference fetch go through httpx with
`verify=True`, which trusts `certifi`'s bundle. Until 2026.10.19 that bundle held `emSign ECC Root
CA - G3`, eight of whose issuing intermediates had their private keys compromised and were revoked
(GHSA-6q48-6c7w-9gw2): whoever holds one can present a certificate for any host and be believed.

Asked of the context httpx builds, because that is what verifies a connection — not of a file name.
"""

from __future__ import annotations

import httpx

#: Roots removed from the bundle because their keys, or their intermediates', are compromised.
COMPROMISED_ROOTS = frozenset({"emSign ECC Root CA - G3"})


def test_no_root_with_compromised_keys_is_trusted() -> None:
    context = httpx.create_ssl_context(trust_env=False)
    trusted = {
        value
        for certificate in context.get_ca_certs()
        for name in certificate.get("subject", ())
        for key, value in name
        if key == "commonName"
    }

    # The guard's own failure mode: a context that loaded nothing would trust nothing listed below.
    assert len(trusted) > 50, "the default context loaded no bundle, so this asserts nothing"
    assert not trusted & COMPROMISED_ROOTS, (
        f"httpx trusts {sorted(trusted & COMPROMISED_ROOTS)}; a certificate issued under a "
        "compromised key would be accepted for any upstream or for Vault"
    )
