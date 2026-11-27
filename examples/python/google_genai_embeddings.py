"""Embeddings with `google-genai` through AIRA: which document answers a question best?

    AUTH=key python google_genai_embeddings.py   # or: bearer, service-account, token-as-key

One call embeds several texts. A list of texts, a task type and a dimension are read only where
the model's catalogue entry declares them; where it does not, the refusal names what is missing.
"""

import math
import os
import sys

import httpx
from google import genai
from google.genai import types

GATEWAY_URL = os.environ["GATEWAY_URL"]
USE_CASE = os.environ["USE_CASE"]
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

documents = ["The Zugspitze is 2,962 metres high.", "Berlin is the capital of Germany."]
question = "How high is Germany's highest mountain?"
result = client.models.embed_content(
    model=EMBEDDING_MODEL,
    contents=[question, *documents],
    config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY", output_dimensionality=768),
)
vectors = [embedding.values or [] for embedding in result.embeddings or []]
query, *candidates = vectors


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.hypot(*a) * math.hypot(*b))


scores = [cosine(query, candidate) for candidate in candidates]
print(f"{len(vectors)} vectors of {len(query)} dimensions")
for score, document in sorted(zip(scores, documents, strict=True), reverse=True):
    print(f"{score:.3f}  {document}")
