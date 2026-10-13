"""The showcase's API keys, re-derived the way its seed mints them (`FRD-130` FR-4).

The demo mints **deterministic** keys so its printed examples still work the second time somebody
runs it — the one thing here not to generalise from: a real key is generated with entropy and shown
once. Every script that calls the demo derives them here — `demo_traffic`, `showcase_try_it`,
`showcase_agent` — so there is one derivation outside Django to keep equal to the seed's
(`apps/seed/contributions/showcase.py`), and `management/backend/tests/test_showcase_seed.py`
compares it with what the seed stored. A copy per script would be a copy that test does not see.
"""

from __future__ import annotations

import hashlib

from aira_common.apikeys import NAMESPACE

#: Not a secret: it derives keys for a demonstration whose passwords are published, and it is
#: *stated* to be public so nobody promotes it by accident.
DEMO_KEY_SALT = "aira-showcase-demo-not-a-secret"


def key_for(slug: str, person: str | None = None) -> str:
    """The demo key a use case is called with — its own, or a named member's.

    ``None`` is the use case's **own** key, derived from the slug alone: the one the printed
    examples and the hand-over scripts use, which belongs to the use case's first member. A name is
    the second key the seed issues to that person, so more than one person appears in the demo's
    per-person figures.
    """
    material = f"{DEMO_KEY_SALT}:{slug}" if person is None else f"{DEMO_KEY_SALT}:{slug}:{person}"
    digest = hashlib.sha256(material.encode()).hexdigest()
    return f"{NAMESPACE}_{digest[:8]}_{digest[8:56]}"
