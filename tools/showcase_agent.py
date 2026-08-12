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


def config(model: str, key: str) -> dict:
    """An OpenCode provider pointed at the gateway's Gemini surface.

    Named models only, and only the one the demo actually serves: OpenCode lists whatever the
    config declares, and a menu offering models the gateway will refuse is `FRD-206`'s complaint
    in another client.
    """
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "aira": {
                "npm": "@ai-sdk/google",
                "name": "AIRA Gateway (showcase)",
                "options": {"baseURL": f"{GATEWAY}/v1beta", "apiKey": key},
                "models": {
                    model: {
                        "name": f"{model} via AIRA",
                        "tool_call": True,
                        "limit": {"context": 32768, "output": 4096},
                    }
                },
            }
        },
        "model": f"aira/{model}",
    }


def main() -> None:
    key = demo_key(SLUG)
    target = pathlib.Path(os.environ.get("AIRA_AGENT_CONFIG", "opencode.showcase.json")).resolve()
    target.write_text(json.dumps(config(CHAT_MODEL, key), indent=2) + "\n")

    print(f"  Written: {target}")
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


if __name__ == "__main__":
    main()
