"""LlamaIndex through AIRA: a three-document index, embedded and answered by the gateway's models.

    AUTH=key python llamaindex_rag.py   # or: bearer, service-account, token-as-key

Both the LLM and the embedding model are LlamaIndex's `google-genai` integrations, and both take
the same `HttpOptions`: the use case's base URL and, for a token, the bearer header.
"""

import os
import sys

import httpx
from google.genai import types
from llama_index.core import Document, VectorStoreIndex
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

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

http = types.HttpOptions(base_url=f"{GATEWAY_URL}/uc/{USE_CASE}", headers=headers)
llm = GoogleGenAI(model=MODEL, api_key=api_key, http_options=http)
embed_model = GoogleGenAIEmbedding(model_name=EMBEDDING_MODEL, api_key=api_key, http_options=http)

documents = [
    Document(text="The Zugspitze is Germany's highest mountain, 2,962 metres high."),
    Document(text="Berlin is the capital of Germany."),
    Document(text="The Elbe flows through Dresden and Hamburg."),
]
# The documents are embedded as one list, with a task type: the model's catalogue entry must
# declare both.
index = VectorStoreIndex.from_documents(documents, embed_model=embed_model)
answer = index.as_query_engine(llm=llm, similarity_top_k=1).query("How high is the Zugspitze?")

print(answer)
print("retrieved:", answer.source_nodes[0].node.get_content())
