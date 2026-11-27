#!/usr/bin/env bash
# curl against AIRA: every credential form the gateway reads, and both ways to name the use case.
# Each call is complete as written. It runs every form whose variables are set; see ../README.md.
#
#   ./gemini.sh
#
# Needs curl and jq. The gateway reads `Authorization: Bearer` first, then `x-goog-api-key`, then
# `?key=`, and accepts a Keycloak token wherever it accepts a key.
set -euo pipefail

: "${GATEWAY_URL:?set GATEWAY_URL, e.g. http://localhost:8001}" "${USE_CASE:?set USE_CASE}"
if [ -z "${API_KEY:-}${ACCESS_TOKEN:-}${CLIENT_ID:-}" ]; then
  echo "no credential: set API_KEY, ACCESS_TOKEN, or CLIENT_ID and CLIENT_SECRET with TOKEN_URL" >&2
  exit 2
fi
MODEL=${MODEL:-gemini-2.5-flash}
EMBEDDING_MODEL=${EMBEDDING_MODEL:-gemini-embedding-001}
ADDRESS="$GATEWAY_URL/uc/$USE_CASE/v1beta" # the use case's address: /uc/<use-case>
ASK='{"contents": [{"role": "user", "parts": [{"text": "What is an API gateway?"}]}]}'
# The answer's text, or the gateway's error (Google's envelope) and a non-zero exit. Reasoning comes
# back only when a request asks for it (`thinkingConfig.includeThoughts`), in `thought` parts.
TEXT='if .error then "\(.error.code) \(.error.message)\n" | halt_error(1)
  else [.candidates[0].content.parts[] | select(.thought | not) | .text] | join("") end'
JSON='content-type: application/json'

if [ -n "${API_KEY:-}" ]; then
  echo "== an AIRA API key, in Google's header"
  curl -sS "$ADDRESS/models/$MODEL:generateContent" -H "x-goog-api-key: $API_KEY" \
    -H "$JSON" -d "$ASK" | jq -r "$TEXT"

  echo "== the same key in the URL: Google's curl form, written into every proxy's log"
  curl -sS "$ADDRESS/models/$MODEL:generateContent?key=$API_KEY" \
    -H "$JSON" -d "$ASK" | jq -r "$TEXT"

  echo "== the use case in a header, for a client that cannot change its path"
  curl -sS "$GATEWAY_URL/v1beta/models/$MODEL:generateContent" -H "X-AIRA-Use-Case: $USE_CASE" \
    -H "x-goog-api-key: $API_KEY" -H "$JSON" -d "$ASK" | jq -r "$TEXT"

  echo "== streamed, as server-sent events"
  curl -sS -N "$ADDRESS/models/$MODEL:streamGenerateContent?alt=sse" -H "x-goog-api-key: $API_KEY" \
    -H "$JSON" -d "$ASK" | sed 's/^data: //' | jq -rj "$TEXT"
  echo

  echo "== an embedding: the number of dimensions"
  curl -sS "$ADDRESS/models/$EMBEDDING_MODEL:embedContent" -H "x-goog-api-key: $API_KEY" \
    -H "$JSON" -d '{"content": {"parts": [{"text": "How high is the Zugspitze?"}]}}' |
    jq -e '.embedding.values | length'
fi

if [ -n "${ACCESS_TOKEN:-}" ]; then
  echo "== a person's Keycloak access token, as a bearer token"
  curl -sS "$ADDRESS/models/$MODEL:generateContent" -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "$JSON" -d "$ASK" | jq -r "$TEXT"

  echo "== the same token where a key goes"
  curl -sS "$ADDRESS/models/$MODEL:generateContent" -H "x-goog-api-key: $ACCESS_TOKEN" \
    -H "$JSON" -d "$ASK" | jq -r "$TEXT"
fi

if [ -n "${CLIENT_ID:-}" ]; then
  echo "== a service account's token, from Keycloak's client-credentials grant"
  TOKEN=$(curl -sS --fail "${TOKEN_URL:?set TOKEN_URL}" -d grant_type=client_credentials \
    --data-urlencode "client_id=$CLIENT_ID" --data-urlencode "client_secret=$CLIENT_SECRET" |
    jq -er .access_token)
  curl -sS "$ADDRESS/models/$MODEL:generateContent" -H "Authorization: Bearer $TOKEN" \
    -H "$JSON" -d "$ASK" | jq -r "$TEXT"
fi
