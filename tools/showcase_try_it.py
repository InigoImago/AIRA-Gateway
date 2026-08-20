"""Print a *runnable* first request for each surface, against the demo that is already up.

`make showcase` named the four administration steps and linked the migration guides, and a reader
running KIRA today still had nothing to paste. They do not need the four steps to *try* it: the
demo has already created the use cases, released the models, granted the people and issued the
keys. What was missing was one command that works.

So this reads the running system rather than restating it — the numeric model id from the KIRA
catalog, the key from the same derivation the seed uses — and prints commands with those values
in them. A block written by hand would have carried an id from whenever it was written, and
`FRD-114` FR-6a is the reason that matters: ids are assigned in the catalog and a stale one names
a model nobody has.

Read-only. It sends one `GET` to the catalog and changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

import stack_addresses

GATEWAY = os.environ.get("AIRA_GATEWAY_URL") or stack_addresses.url("gateway")
CONSOLE = os.environ.get("AIRA_CONSOLE_URL") or stack_addresses.url("console")
SLUG = "kundenservice"

#: Must match `apps/seed/contributions/showcase.py`, as `tools/demo_traffic.py` does. Duplicated
#: across a process boundary rather than imported, because this runs outside Django — and a
#: mismatch is loud (401), not silent, which is the only reason that is acceptable.
DEMO_KEY_SALT = "aira-showcase-demo-not-a-secret"


def key_for(slug: str) -> str:
    digest = hashlib.sha256(f"{DEMO_KEY_SALT}:{slug}".encode()).hexdigest()
    return f"aira_{digest[:8]}_{digest[8:56]}"


#: The demo's own chat model — the one the seed declares, releases to the demo use cases, and
#: drives its traffic through. Read from the same variable the seed reads, so a demo started with
#: a different local model prints a command about *that* one.
DEMO_CHAT_MODEL = os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", "qwen3:0.6b")


def chat_model(key: str) -> tuple[str, int] | None:
    """The demo's chat model and the integer id a KIRA client addresses it by.

    Asked of the **KIRA** catalog rather than the Gemini one, because the integer id is the
    predecessor's addressing and that endpoint is where it is published. `None` when the gateway
    cannot be reached or has no chat model — in which case this block says nothing rather than
    printing a command that cannot work.

    **The demo's own model first, not merely the first model.** This took whichever chat model the
    catalog listed first, which on an installation that has cloud credentials configured is a cloud
    model — so `make showcase` printed a `model_id` for `gemini-2.5-flash`, a model the demo never
    seeded, never released and never sent a request to. It answered `200` with an **empty** body:
    24 output tokens, all of them spent thinking. A first command that works is the entire purpose
    of this file, and "works" has to mean "answers with something".
    """
    request = urllib.request.Request(  # noqa: S310 — a fixed localhost URL
        f"{GATEWAY}/kira/api/external/models", headers={"x-goog-api-key": key}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            models = json.loads(response.read())
    except urllib.error.URLError, TimeoutError, ValueError, OSError:
        return None
    chats = [model for model in models if "CHAT" in (model.get("capabilities") or [])]
    for model in chats:
        if str(model.get("name")) == DEMO_CHAT_MODEL:
            return str(model["name"]), int(model["id"])
    # Nothing local in the catalog — the pull failed, or somebody is running the showcase against
    # an installation of their own. The first chat model is then the best available answer, and it
    # is still read from the running system rather than written down here.
    for model in chats:
        return str(model["name"]), int(model["id"])
    return None


def main() -> int:
    key = key_for(SLUG)
    found = chat_model(key)
    if found is None:
        print("  (the gateway did not answer, so there is nothing to paste yet)")
        return 0
    model, numeric_id = found

    print("  The demo has already done the four steps above for its own use cases, so a KIRA")
    print("  client can be pointed at it **right now** — no set-up, one base URL:")
    print()
    print(f"      curl -s {GATEWAY}/kira/api/external/chat \\")
    print(f"        -H 'x-goog-api-key: {key}' \\")
    print("        -H 'content-type: application/json' \\")
    print(
        f'        -d \'{{"request":{{"parts":[{{"text":"Sag OK."}}]}},'
        f'"model_id":{numeric_id},"maxTokens":24}}\''
    )
    print()
    print(f"  `model_id: {numeric_id}` is what the catalog assigned to '{model}'. If your clients")
    print("  send the predecessor's ids, an administrator can give AIRA's models exactly those")
    print("  numbers — then the client's model ids do not change either. The same key streams:")
    print()
    print(f"      curl -N {GATEWAY}/kira/api/external/streaming-chat …")
    print("      # the predecessor's own SSE: status: update, update, … then one completed")
    print()
    print("  And the same request on the Gemini surface, for a client that speaks Google's API:")
    print()
    print(f"      curl -s {GATEWAY}/v1beta/models/{model}:generateContent \\")
    print(f"        -H 'x-goog-api-key: {key}' -H 'content-type: application/json' \\")
    print('        -d \'{"contents":[{"role":"user","parts":[{"text":"Say OK."}]}]}\'')
    print()
    print("  Both land in the same audit trail, under different API names. Watch either arrive:")
    print(f"      {CONSOLE}/use-cases/{SLUG}  →  Traces")
    print()
    print("  This key is bound to one use case, so it needs no header. A caller who belongs to")
    print("  several names one with 'X-AIRA-Use-Case: <slug>' — which chooses among what they")
    print("  already have, and never grants anything: naming another use case answers 403.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
