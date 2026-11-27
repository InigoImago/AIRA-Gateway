"""Google's REST API by hand, with `httpx`: every credential form the gateway reads.

    AUTH=key python rest_httpx.py   # or: query-key, bearer, service-account, token-as-key
    USE_CASE_VIA=header python rest_httpx.py   # the use case in a header instead of the path

The gateway reads `Authorization: Bearer` first, then `x-goog-api-key`, then `?key=`, and accepts
a Keycloak token wherever it accepts a key.
"""

import json
import os
import sys

import httpx

GATEWAY_URL = os.environ["GATEWAY_URL"]
USE_CASE = os.environ["USE_CASE"]
MODEL = os.environ.get("MODEL", "gemini-2.5-flash")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")
AUTH = os.environ.get("AUTH", "key")


def service_account_token() -> str:
    """Keycloak's client-credentials grant: a confidential client signs in as itself."""
    form = {"grant_type": "client_credentials"}
    form |= {"client_id": os.environ["CLIENT_ID"], "client_secret": os.environ["CLIENT_SECRET"]}
    response = httpx.post(os.environ["TOKEN_URL"], data=form)
    response.raise_for_status()
    return response.json()["access_token"]


headers: dict[str, str] = {}
params: dict[str, str] = {}
if AUTH == "key":  # an AIRA API key, issued in the console: use case -> API keys
    headers["x-goog-api-key"] = os.environ["API_KEY"]
elif AUTH == "query-key":  # Google's curl form; every proxy on the way logs the URL, key included
    params["key"] = os.environ["API_KEY"]
elif AUTH == "bearer":  # a person's Keycloak access token
    headers["Authorization"] = f"Bearer {os.environ['ACCESS_TOKEN']}"
elif AUTH == "service-account":  # a service account's token
    headers["Authorization"] = f"Bearer {service_account_token()}"
elif AUTH == "token-as-key":  # a token in the key's place
    headers["x-goog-api-key"] = os.environ.get("ACCESS_TOKEN") or service_account_token()
else:
    sys.exit(f"unknown AUTH {AUTH!r}")

if os.environ.get("USE_CASE_VIA") == "header":  # for clients that cannot change their path
    base, headers["X-AIRA-Use-Case"] = f"{GATEWAY_URL}/v1beta", USE_CASE
else:
    base = f"{GATEWAY_URL}/uc/{USE_CASE}/v1beta"


def answer_text(response: dict) -> str:
    """The answer's text. Reasoning, where a request asked for it, comes in `thought` parts."""
    parts = response["candidates"][0]["content"].get("parts", [])
    return "".join(part.get("text", "") for part in parts if not part.get("thought"))


ask = {"contents": [{"role": "user", "parts": [{"text": "What is an API gateway?"}]}]}
with httpx.Client(base_url=base, headers=headers, params=params, timeout=120) as http:
    reply = http.post(f"/models/{MODEL}:generateContent", json=ask)
    if reply.is_error:  # Google's error envelope: {"error": {"code", "message", "status", …}}
        sys.exit(f"{reply.status_code}: {reply.json()['error']['message']}")
    print(answer_text(reply.json()))
    print("answered by:", reply.headers.get("X-AIRA-Model"), reply.headers.get("X-AIRA-Request-Id"))

    # `alt=sse` streams server-sent events; without it the stream is one JSON array.
    path = f"/models/{MODEL}:streamGenerateContent"
    with http.stream("POST", path, params={"alt": "sse"}, json=ask) as events:
        events.raise_for_status()
        for line in events.iter_lines():
            if line.startswith("data: "):
                print(answer_text(json.loads(line[6:])), end="", flush=True)
    print()

    text = {"content": {"parts": [{"text": "How high is the Zugspitze?"}]}}
    vector = http.post(f"/models/{EMBEDDING_MODEL}:embedContent", json=text).raise_for_status()
    print("embedding:", len(vector.json()["embedding"]["values"]), "dimensions")
