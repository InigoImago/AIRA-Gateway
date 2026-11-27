"""LangChain's Gemini integration, `langchain-google-genai`, through AIRA: chat, stream, embed.

    AUTH=key python langchain_chat.py   # or: bearer, service-account, token-as-key

The chat model and the embedding model take the same three settings: the use case's base URL, a
credential (`google_api_key`, or a header in `additional_headers`) and a model name.
"""

import os
import sys

import httpx
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

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

# LangChain appends /v1beta/models/<model>:… itself, so the base URL is the use case's address.
settings = {
    "google_api_key": api_key,
    "base_url": f"{GATEWAY_URL}/uc/{USE_CASE}",
    "additional_headers": headers or None,
}
llm = ChatGoogleGenerativeAI(model=MODEL, max_retries=0, **settings)

print(llm.invoke("What is an API gateway? One sentence.").text)
for chunk in llm.stream("Count to five."):
    print(chunk.text, end="", flush=True)
print()

# LangChain sends a task type with every embedding: the catalogue entry must declare task types.
embeddings = GoogleGenerativeAIEmbeddings(model=EMBEDDING_MODEL, **settings)
vector = embeddings.embed_query("How high is the Zugspitze?")
print(f"query embedding: {len(vector)} dimensions")
