"""Asking for a thinking model's reasoning with `google-genai` through AIRA.

    AUTH=key python google_genai_thinking.py   # or: bearer, service-account, token-as-key

Reasoning comes back only when a request asks for it (`include_thoughts`), as with Google, and
only where the use case allows it: where its administrators have switched reasoning off, asking
is refused by name. It arrives as parts marked `thought`, ahead of the answer.
"""

import os
import sys

import httpx
from google import genai
from google.genai import types

GATEWAY_URL = os.environ["GATEWAY_URL"]
USE_CASE = os.environ["USE_CASE"]
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")
AUTH = os.environ.get("AUTH", "key")


def service_account_token() -> str:
    """Keycloak's client-credentials grant: a confidential client signs in as itself."""
    form = {"grant_type": "client_credentials"}
    form |= {"client_id": os.environ["CLIENT_ID"], "client_secret": os.environ["CLIENT_SECRET"]}
    response = httpx.post(os.environ["TOKEN_URL"], data=form)
    response.raise_for_status()
    return response.json()["access_token"]


headers: dict[str, str] = {}
if AUTH == "key":  # an AIRA API key, issued in the console: use case -> API keys
    api_key = os.environ["API_KEY"]
elif AUTH == "bearer":  # a person's Keycloak access token, as Authorization: Bearer
    api_key, headers["Authorization"] = "unused", f"Bearer {os.environ['ACCESS_TOKEN']}"
elif AUTH == "service-account":  # a service account's token, as Authorization: Bearer
    api_key, headers["Authorization"] = "unused", f"Bearer {service_account_token()}"
elif AUTH == "token-as-key":  # a token in the key's place, for clients without a header setting
    api_key = os.environ.get("ACCESS_TOKEN") or service_account_token()
else:
    sys.exit(f"unknown AUTH {AUTH!r}")

client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(
        base_url=f"{GATEWAY_URL}/uc/{USE_CASE}", api_version="v1beta", headers=headers
    ),
)

config = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(include_thoughts=True))
question = "Is 1,001 a prime number?"
shown = None  # what is being printed: the reasoning (True) or the answer (False)
for chunk in client.models.generate_content_stream(model=MODEL, contents=question, config=config):
    for part in chunk.candidates[0].content.parts:
        if part.text:
            if bool(part.thought) != shown:
                shown = bool(part.thought)
                print("\n--- reasoning ---" if shown else "\n--- answer ---")
            print(part.text, end="", flush=True)
print()
