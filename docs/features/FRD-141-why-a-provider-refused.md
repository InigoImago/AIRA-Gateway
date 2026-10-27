# FRD-141 — Why a provider refused, said so that it can be fixed

> Phase: 3 (catalog) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner set up a standalone installation against an external Keycloak and a Vertex
> service account, and could not catalogue a single model by the end of the day: every check
> answered *Not reachable (UpstreamError).* The status Google sent, its reason word, and the
> certificate a proxy presented had all reached the gateway and been thrown away.
> Related: `FRD-506` (the model check), `FRD-117` (the background probe), `FRD-115` (Vertex),
> `FRD-628` (what a caller is told), `FRD-142` (reaching Google without a key).

## 1. Summary

Every platform this gateway reaches states the cause of a refusal in a structured form — Google's
`error.status` and a `google.rpc.ErrorInfo` with a `reason` from a published list, an OAuth token
endpoint's `error`, Azure's and the OpenAI dialect's `error.code` — and a failed connection carries
its cause in the exception chain. The gateway read none of it: `FRD-506` FR-3 kept the provider's
text out of the answer (rightly: it can quote a URL with a key), and with it went everything else.

This feature reads the structured fields only, explains them in our own words, and shows the
explanation where a model is set up.

## 2. Goals & Non-Goals

**Goals**
- Whoever checks a model learns which of the usual causes it is and what to change: an API not
  enabled, a missing role, a model not offered in the region or not enabled in Model Garden, a key
  Google no longer accepts, a clock that is off, a proxy that inspects TLS.
- A caller whose request fails for one of these reasons can tell that it is the installation's
  fault, and which.
- A service-account key can be supplied as a file and rotated without a restart.

**Non-Goals**
- Repeating any provider's free text. The explanation is built from words of a closed list and
  identifiers of a bounded shape; `FRD-506` FR-3 stands.
- Changing any status code.
- Telling a caller the installation's project number or its permissions: the reason word goes to
  the caller, the explanation to the operator.

## 4. Functional Requirements

- **FR-1 — Google's cause.** A refusal carrying a `google.rpc.ErrorInfo` is read for its `reason`
  (letters, digits, underscores only) and for the metadata `service`, `consumer`, `permission`,
  `resource`, `quota_metric`, `quota_limit` and `location` (identifier-shaped values only). Without
  one, Google's `error.status` (`NOT_FOUND`, `PERMISSION_DENIED`, …) is the cause. Each known word
  has our explanation; an unknown one falls back to what its status means. Anthropic's own envelope,
  which its models on Vertex answer with, states its cause in `error.type` (`overloaded_error`,
  `request_too_large`, …): each is explained by who has to act, and a bare `413` or `529` is named
  (`REQUEST_TOO_LARGE`, `OVERLOADED`). A `413`'s stated reason is carried to the caller like a
  `400`'s: both are about the request.
- **FR-2 — The connection's cause.** An untrusted certificate (`CERTIFICATE_NOT_TRUSTED`, naming the
  host and `SSL_CERT_FILE`), a refused proxy, an unresolvable name, a connect timeout, a failed
  connection and a read timeout are each named. The host is named; the path and the query never.
  A read timeout is this gateway's own time limit running out, and says so — *AIRA stopped
  waiting*, after how long, and which setting holds the limit — and a connection pool that had
  nothing free in time is `POOL_EXHAUSTED`.
- **FR-3 — The token exchange's cause.** A refused service-account exchange is matched against
  Google's descriptions — an invalid signature (`KEY_NOT_ACCEPTED`), an account not found, a time
  window refused (`CLOCK_SKEW`) — and otherwise named by its `error` code. The description is
  matched, never repeated: it can echo the assertion. A token endpoint that cannot be reached says
  why (FR-2). The cause survives to the request that could get no token.
- **FR-4 — Where it is shown.** The model check (`GET /v1beta/models/{model}:check`) adds `reason`
  and `explanation`, overall and per region, and its `detail` names them. `/readyz`'s upstream
  verdicts and every place that reports through `describe_failure` say `status REASON: explanation`.
  A caller's error message gains one sentence, `Reason: WORD.`, and begins with the provider's
  name — `Google Vertex AI`, `Google AI Studio`, `Microsoft Foundry`, the name an installation gave
  its own server — so that it reads as the provider's answer. The console's model editor shows the
  causes as a block, one line per cause with the regions that gave it — not in a tooltip.
- **FR-5 — The key as a file.** `AIRA_VERTEX_CREDENTIALS_FILE` names a file holding the key's JSON.
  It is read at start and again at every token exchange when its modification time or size changed;
  a changed file that cannot be used keeps the last key that worked. A file missing or unusable at
  start stops the start. Exclusive with `AIRA_VERTEX_CREDENTIALS`.

## 8. Security & Privacy

Nothing new is stored. The reason word joins the caller's message, which the audit row already keeps
(`FRD-628` FR-4); it is a word from a closed list and names no person. The explanation can name the
installation's project and a permission and is shown only where `operations.diagnose` reaches: the
model check, `/readyz`'s diagnosis, the log.

## 10. Testing & Acceptance Criteria

`gateway/tests/test_provider_diagnosis.py`: each FR against an `httpx.MockTransport` standing in for
Google, with a marker string in every provider message that must appear nowhere; the key file's
rotation and its broken rotation. `model-catalog.spec.ts`: the causes block in the rendered editor.
Mutations `PD-1`–`PD-23`.
