"""Hand the showcase's coding assistant over, ready to run (`FRD-130`, `FRD-132`).

`make showcase` seeds a `coding-assistant` use case with function calling on, a model whose
catalog entry declares `tools`, and a key. Everything needed to point a real assistant at it
exists — and until now the last step was a paragraph in a README naming a use case the seed did
not create. A demo that ends one manual step short of working is a demo that gets described rather
than shown.

The key is **re-derived, not read**: the showcase mints a deterministic demo key so that its
examples still work the second time somebody runs it. That is the one thing here that must not be
generalised from — a real key is generated with entropy, shown once and never again, which is what
the console demonstrates. Saying so is the point of this docstring.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "libs" / "src"))

from aira_common.apikeys import NAMESPACE  # noqa: E402

#: Must match `apps/seed/contributions/showcase.py`. Duplicated rather than imported because this
#: runs outside Django, and the seed's own tests compare what it stored against what it reported —
#: so a divergence is caught there rather than showing up here as a key that does not work.
DEMO_KEY_SALT = "aira-showcase-demo-not-a-secret"
SLUG = "coding-assistant"

GATEWAY = os.environ.get("AIRA_GATEWAY_URL", "http://localhost:8001")
CHAT_MODEL = os.environ.get("AIRA_DEMO_CHAT_MODEL", "qwen3:0.6b")


def demo_key(slug: str) -> str:
    digest = hashlib.sha256(f"{DEMO_KEY_SALT}:{slug}".encode()).hexdigest()
    return f"{NAMESPACE}_{digest[:8]}_{digest[8:56]}"


def serves(key: str) -> list[dict]:
    """What the gateway tells this caller it serves, with the AIRA fields on each entry.

    Asked rather than assumed. Empty if it cannot be reached, which the caller reports — a config
    generator that silently invents a menu is the thing this function replaces.
    """
    request = urllib.request.Request(  # noqa: S310 - the URL is this deployment's own gateway
        f"{GATEWAY}/v1beta/models", headers={"x-goog-api-key": key}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - see above
            return list(json.loads(response.read()).get("models") or [])
    except urllib.error.URLError, OSError, ValueError:
        return []


def offers_tool_calling(entry: dict) -> bool:
    """The catalog's half of the question: could this model return a function call at all?

    **Separate from `usable` because it has to be testable without a socket.** It was one function,
    and a test asserting that a tools-less model is not offered passed with the tools check
    *deleted* — because the remaining half then failed on the network the test does not have, and
    `False` for the wrong reason reads exactly like `False` for the right one. Found by removing
    the check on purpose and watching all eleven tests stay green.

    An absent `airaCapabilities` is a model nobody catalogued, and that is not a licence
    (`FRD-114` FR-7). `generateContent` is asked as well: declaring `tools` is not enough on its
    own, because a client's turn is a generation.
    """
    if "tools" not in (entry.get("airaCapabilities") or []):
        return False
    return "generateContent" in (entry.get("supportedGenerationMethods") or [])


def usable(entry: dict, key: str) -> bool:
    """Whether this client could actually use the model — declared *and* callable.

    Two questions, and neither alone is the answer. The **catalog** says whether the model can
    return a function call; a menu entry without that is a model the whole assistant loop breaks
    on, refused by name on the first turn. The **release** says whether this use case may call it
    at all (`FRD-308`), and that is not in the listing — the listing describes the gateway, not the
    caller, which is why `mock-1` appears here and is not released to this use case.

    So the release is not guessed from a field that does not carry it: the model is *asked*, with
    the smallest request there is. A model that answers is one the assistant can use; a model that
    is refused would have been an entry in a menu that fails when chosen, which is `FRD-206`'s
    complaint arriving in somebody else's client.
    """
    if not offers_tool_calling(entry):
        return False
    name = str(entry["name"]).removeprefix("models/")
    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": "ok"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310 - this deployment's own gateway
        f"{GATEWAY}/v1beta/models/{name}:generateContent",
        data=body,
        headers={"x-goog-api-key": key, "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - see above
            return 200 <= response.status < 300
    except urllib.error.URLError, OSError:
        return False


def config(models: list[dict], key: str) -> dict:
    """An OpenCode provider pointed at the gateway's Gemini surface.

    **Every model this use case can use, derived rather than named.** This wrote exactly one model,
    taken from an environment variable, under a comment claiming it listed "only the one the demo
    actually serves" — a rule the code did not have. The two coincided, so nobody noticed until
    somebody released a second model, saw one in the menu and had to ask whether that was intended.
    A comment claiming a rule the code does not implement is this repository's most-repeated defect.

    The rule the comment described is the right one and is implemented now: a menu offering models
    the gateway will refuse is `FRD-206`'s complaint in another client, and for an assistant that
    means both halves — tool calling declared, and this use case released to call it.

    The context and output figures come from the **catalog**, per model. They were hard-coded
    `32768` and `4096`; the second was the invented cap that refused this very client's first
    request, and writing it here as well would have put the same guess in a second place.
    """
    entries = {}
    for entry in models:
        name = str(entry["name"]).removeprefix("models/")
        window = entry.get("airaMaxOutputTokens")
        entries[name] = {
            "name": f"{name} via AIRA",
            "tool_call": True,
            # One number, from the catalog, because this runtime has one: prompt and answer share
            # the context window and there is no separate output limit to declare.
            "limit": {"context": window, "output": window} if window else {},
        }
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "aira": {
                "npm": "@ai-sdk/google",
                "name": "AIRA Gateway (showcase)",
                "options": {"baseURL": f"{GATEWAY}/v1beta", "apiKey": key},
                "models": entries,
            }
        },
        "model": f"aira/{next(iter(entries))}" if entries else "",
    }


def main() -> None:
    key = demo_key(SLUG)
    target = pathlib.Path(os.environ.get("AIRA_AGENT_CONFIG", "opencode.showcase.json")).resolve()

    offered = serves(key)
    if not offered:
        print(f"  The gateway at {GATEWAY} could not be asked what it serves; nothing written.")
        return
    chosen = [entry for entry in offered if usable(entry, key)]
    target.write_text(json.dumps(config(chosen, key), indent=2) + "\n")

    print(f"  Written: {target}")
    print()
    # **Why a model is absent, said out loud.** An assistant menu with one entry beside a use case
    # with two released models reads as a bug, and somebody had to ask whether it was intended. An
    # absent thing that does not explain itself is `FRD-206`'s complaint from the other side: a
    # present control that refuses announces itself, an absent one never does.
    print(f"  Models offered to the assistant: {', '.join(_names(chosen)) or '(none)'}")
    left_out = [entry for entry in offered if entry not in chosen]
    if left_out:
        print("  Left out, and why — a menu entry the gateway refuses breaks the loop it is in:")
        for entry in left_out:
            capabilities = entry.get("airaCapabilities") or []
            reason = (
                "declares no tool calling"
                if "tools" not in capabilities
                else "not released to this use case, or not callable with this key"
            )
            print(f"    · {str(entry['name']).removeprefix('models/'):24} {reason}")
    print()
    # **What the use case is**, not only what to type. The block used to jump straight to the
    # command, so a reader saw a coding assistant work and could not say what about it was
    # *governed* — which is the whole reason this use case is in the demo rather than a second
    # chatbot. Every number below is seeded, so the console can be opened on any claim here.
    print("  What this use case is, and why it is its own:")
    print()
    print("    • **Function calling is on — and it is the only use case here that has it.** A tool")
    print("      declaration is carried to the model and the call comes back to the client; AIRA")
    print("      never runs one. Off by default everywhere else, because a use case that")
    print("      summarises documents has no business declaring functions.")
    print("    • **One human instruction becomes many model calls.** A trivial ask produced three")
    print("      gateway requests when this was measured — served, refused, client_gone — so the")
    print("      limit and the budget are sized for an agent (240 rpm) and not for a chatbot. A")
    print("      limit calibrated for a chatbot trips in the first minute and reads as a broken")
    print("      gateway rather than as a wrong limit.")
    print("    • **Source code and file paths are content.** They end up in stored prompts, so the")
    print("      retention window and who may read a payload are decisions somebody made here.")
    print(
        "    • **Prompt caching is deliberately off.** This runtime reports no cached tokens, and"
    )
    print("      a switch shown as on while doing nothing is an absent control wearing a present")
    print("      one's badge.")
    print()
    print("  Point a coding assistant at the showcase:")
    print()
    print(f"      OPENCODE_CONFIG={target} opencode run 'list the files here'")
    print()
    print("  Everything it does is governed and recorded. In the console:")
    print(f"      {GATEWAY.replace('8001', '4200')}/use-cases/{SLUG}  →  Traces")
    print("  — every turn, which model, how it ended, what it cost, and which functions it asked")
    print("  for. The key names the person accountable for it, not the author of each request.")
    print()
    print("  The key is a *demo* key: derived from a fixed salt so the example still works on the")
    print("  second run. A real one is generated with entropy and shown exactly once.")


def _names(entries: list[dict]) -> list[str]:
    return [str(entry["name"]).removeprefix("models/") for entry in entries]


if __name__ == "__main__":
    main()
