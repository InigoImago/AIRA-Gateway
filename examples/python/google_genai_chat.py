"""Google's Python SDK, `google-genai`, through AIRA: one answer, then a streamed one.

    AUTH=key python google_genai_chat.py   # or: bearer, service-account, token-as-key

The address is the use case's base URL, `<gateway>/uc/<use-case>`, to which the SDK appends
`/v1beta/models/<model>:generateContent`. The credential is an AIRA API key or a Keycloak token;
the model is one released to the use case.
"""

import os
import sys

import httpx
from google import genai
from google.genai import types

GATEWAY_URL = os.environ["GATEWAY_URL"]  # e.g. http://localhost:8001
USE_CASE = os.environ["USE_CASE"]  # the use case's slug
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

# The SDK requires a key even where a bearer header is sent; the gateway reads the header first.
client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(
        base_url=f"{GATEWAY_URL}/uc/{USE_CASE}", api_version="v1beta", headers=headers
    ),
)

response = client.models.generate_content(model=MODEL, contents="What is an API gateway?")
print(response.text)
print("tokens:", response.usage_metadata.total_token_count if response.usage_metadata else "?")

for chunk in client.models.generate_content_stream(model=MODEL, contents="Count to five."):
    print(chunk.text or "", end="", flush=True)
print()
