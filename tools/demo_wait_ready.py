"""Wait until the demo is actually ready to be driven — not merely until it answers.

**The defect this replaces.** `make showcase` waited for `wait-healthy`, which checks that four
HTTP endpoints respond, and then slept six seconds "for the read model to catch up". Neither says
anything about the **seed**, and the seed waits on the model pull (`docker-compose.yml`,
`ollama-pull`). So on a machine that already has the model, the pull returns at once, the seed
finishes inside those six seconds, and everything works. On a machine that does not:

    ollama-pull  starts, downloads ~570 MB
    management-seed  waits for it — no use cases, no API keys yet
    demo_traffic.py  runs anyway  ->  served 0, refused 11, every one a 401

Measured from a completely empty machine on 2026-08-12: eleven 401s, and the models finished
downloading twenty-six seconds *after* the traffic had already failed. It worked for everyone who
had run it before and broke for exactly the person the target is for — which is the failure this
repository has now recorded for a pulled image, a Vault path, and a Keycloak realm.

**One condition, and it is the one that matters.** Rather than waiting on each link — the pull,
then the seed, then the outbox relay, then Kafka, then the gateway's consumer — this asks the
question the traffic is about to ask: *will the gateway accept a demo credential and serve the
demo's model?* That is true only when every one of those links has done its work, and it stays
true if somebody rearranges them.

A timeout that says **which half** is missing, because "not ready" sends the reader to look at
everything at once.
"""

from __future__ import annotations

import os
import sys
import time

import httpx

# Imported, never restated. A third copy of the salt is a third place for it to drift, and the
# failure it produces (401) looks exactly like the one this script exists to wait out.
from demo_traffic import CALLERS, CHAT, GATEWAY, key_for

#: Generous, because on a first run this transitively waits for a model download over a network
#: nobody here controls. Being slow costs a minute; being short costs the demo.
TIMEOUT_SECONDS = float(os.environ.get("AIRA_DEMO_WAIT_SECONDS", "900"))
POLL_SECONDS = 3.0

#: The use case whose key is used to ask. Any seeded one would do; this is the one the walkthrough
#: opens with.
PROBE_USE_CASE = "kundenservice"

#: **Every** credential the traffic will use, not one of them.
#:
#: A key reaches the gateway over Kafka, and one key arriving says nothing about the next: this
#: probed the use case's own key alone, so a second member's key could still be in flight when the
#: wait went green. That was survivable while a 401 only reduced the figures; it is not now that the
#: traffic treats one as a wiring fault and stops (`FRD-130` §4e). A guard that turns a timing
#: window into a red run is worse than the window.
PROBE_KEYS = [key_for(PROBE_USE_CASE, person) for person in CALLERS[PROBE_USE_CASE]]


def _state(client: httpx.Client) -> tuple[bool, bool]:
    """``(credential accepted, model servable)`` — the two halves, reported apart.

    A `GET` spends nothing and needs no use case (`SPENDS_NOTHING`), so it is safe to poll: it
    reaches the model list without making a model call, which is what lets this run every three
    seconds without billing anybody.
    """
    response = None
    for key in PROBE_KEYS:
        try:
            response = client.get(
                f"{GATEWAY}/v1beta/models",
                headers={"x-goog-api-key": key},
                timeout=10.0,
            )
        except httpx.HTTPError:
            return False, False
        # Every one of them, because the traffic uses every one of them. The last answer carries the
        # model list; which one that is does not matter, since they all reach the same registry.
        if response.status_code != httpx.codes.OK:
            return False, False
    if response is None:
        return False, False
    # **`airaDeclared`, not merely present.** The list reports what the *registry* serves — an
    # adapter is configured, so its models appear the moment the gateway starts, whether or not
    # anybody has catalogued them. Since `FRD-307` only a catalogued, approved model may be used,
    # so a condition built on presence alone goes green while every request still answers
    # `400 not in the model catalog`. Measured: the wait passed, the traffic failed eleven times,
    # and the catalog arrived over Kafka a few seconds later.
    declared = {
        str(model.get("name", "")).removeprefix("models/")
        for model in response.json().get("models", [])
        if model.get("airaDeclared")
    }
    return True, CHAT in declared


def main() -> int:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    accepted = servable = False
    announced = False
    with httpx.Client() as client:
        while time.monotonic() < deadline:
            accepted, servable = _state(client)
            if accepted and servable:
                print(f"  the gateway accepts the demo key and serves '{CHAT}'")
                return 0
            if not announced:
                # Said once, and only when there is something to wait for: on a machine that has
                # run this before, the loop exits on its first pass and stays silent.
                print("  waiting for the seed to reach the gateway (first run pulls a model)…")
                announced = True
            time.sleep(POLL_SECONDS)

    print("", file=sys.stderr)
    if not accepted:
        print(
            f"the gateway did not accept the demo API key within {TIMEOUT_SECONDS:.0f}s.\n"
            "The key is created by 'management-seed' and reaches the gateway over Kafka, and the\n"
            "seed waits for the model pull — so on a first run this is usually the download.\n"
            "Check:  docker logs aira-ollama-pull   then   docker logs aira-management-seed",
            file=sys.stderr,
        )
    else:
        print(
            f"the demo key works, but '{CHAT}' is not declared in the model catalog yet.\n"
            "The catalog is seeded and announced over Kafka; a model that is catalogued but not\n"
            "pulled fails every request made against it.\n"
            "Check:  docker logs aira-gateway-consumer\n"
            "        docker exec aira-ollama ollama list",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
