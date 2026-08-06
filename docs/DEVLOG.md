# AIRA Gateway — Development Log

A running, dated log of meaningful changes and decisions. Newest entries on top.
Keep entries short; link to ADRs/FRDs/commits for detail.

---

## 2026-08-06 — fallback, limits, retention and KIRA, against the running thing
Eleven more live cases (`tests/integration/test_controls_live.py`), and the fixture for the first
group is worth stating: **two named servers against one endpoint**, `gpu-a` offering a model that
is not pulled and `gpu-b` offering one that is. `gpu-a` therefore returns a real 404 over a real
socket, so the chain crosses two adapters and two transports to reach an answer. It is the closest
a single machine gets to a second one, and it exercises the part that matters.

What that showed: a dead candidate is passed over and the next one answers; the audit keeps
`requested_model` beside `model` with `model_selection = fallback:1`, and the **provenance follows
the model that answered** — `gpu-b`, not the server that failed. Without that pair, "why did the
spend on that box triple" has no answer in any report.

Retention runs as what it is — a `docker exec` into the separate process, not an in-thread call —
and the property is the one the feature turns on: the **content** expires and the **evidence**
does not. The payloads become NULL, the row and its token counts stay. The other half is tested
too, because a pruner that cannot tell "expired" from "recent" deletes everything the first time it
runs and nothing about the run says so.

The KIRA surface reaches the same real model through an integer id, in the predecessor's shape,
with `Deprecation` on the response — and `test_both_surfaces_record_the_same_request_the_same_way`
sends one request through each and compares what the audit kept. Same outcome, model, provider,
tokens, use case, credential. That is the only way to know the shared controls were *run* rather
than merely present. A KIRA caller meets the same budget and gets the 429 in the predecessor's
vocabulary (`EXTERNAL_KI_API_TOO_MANY_REQUEST`), which is exactly what a compatibility surface
should do: same control, its own words.

**Two mistakes of mine, each made more than once, worth recording because they are the failure
modes of this *kind* of test rather than of this system.**

A helper asked `/v1beta/models` without a credential, got a 401, read it as "nothing is
registered", and skipped the suite — silently. A skip that fires for the wrong reason is worse than
a failure, because the summary line reads the same whether the system works or the test never
looked. Both helpers assert on the status now instead of shrugging at it.

And three separate times I read the audit table too early. The write is deliberately off the
request path, so a test that sends two requests and then queries once will sometimes see one row.
Every one of those failures reads exactly like a **lost audit row** — one of the most serious
things this system could do — which is precisely why the imitation is intolerable. The helper now
waits for the number of rows the test actually expects, which is the only version that cannot lie
in either direction.

A third, smaller one: a test asserted `provider == "ollama"`. It went red the moment the servers
were renamed for the fallback fixture, because it was asserting somebody's `.env` rather than the
system's behaviour. What matters is *that* a machine is identified.

---

## 2026-08-06 — the first real requests, and three defects
Ollama attached as **systems, plural** — `AIRA_OPENAI_SERVERS` takes a list of named servers, each
with its own URL, models and region, because a self-hosted fleet is several machines and "which box
served this request" is exactly what an audit exists to answer. Every server's name reaches the
audit row as the provider; with one endpoint setting they would all have logged as `ollama`.

Then a live suite (`tests/integration/test_governed_path.py`): a real API key bound to a real use
case, real HTTP through the deployed gateway, and the database read afterwards. Fourteen cases —
served-and-stored, payloads-off, budgets, budget exhaustion, refusals recorded, the tenant
boundary, revocation, and concurrency. It found three things.

**1. A model name may contain a colon.** `model:method` was split at the *first* one, which was
correct for as long as Google was the only vendor. A self-hosted model is called `qwen3:0.6b`, so
the split produced the model `qwen3` and the method `0.6b:generateContent`, and the answer was
**"Model 'qwen3' not found"** — a message naming a model nobody asked for, pointing at the catalog
instead of at the parser. The verb never contains a colon and the model may, so it splits from the
right.

**2. A comment claimed a rule the system did not have.** `build_openai_upstreams` said a locally
declared region was "recorded, not checked" — and the first real request came back *"runs in
'on-premises', and this request may only be processed in [...]"*, because `RegionAllowed` quite
correctly checks every model that declares one. The comment described an intention; the code had a
rule; the rule was right. So a server now declares **no** region unless the operator names one —
no claim, nothing to enforce, a laptop keeps working — and naming one opts in to both the evidence
and the check, which happens **at startup** rather than as a 400 on every request.

**3. The budget counter was racy in two ways, and one of them was silent.** Twenty concurrent
requests against a fresh budget produced two **500s**: `record` read the counter, inserted it when
absent, and committed, so two requests arriving as the *first* of a period both inserted and one
lost on the primary key — a 500 for a request that had already been served and charged for.

The quieter half has no error at all. `record.tokens += n` reads the loaded value and writes an
**absolute** one, so two overlapping writes discard an increment. The counter that is supposed to
be the system of record drifts *below* the truth, in the direction that spends money, under exactly
the load that makes a budget matter. Both are closed by moving the arithmetic into an upsert, where
the row is locked for the statement — dialect-dispatched, because `ON CONFLICT` is spelled the same
by Postgres and SQLite and by nobody else.

Two hermetic tests were written for it and **both were shown to fail against the old code** before
the fix went in; `B8` is the mutation. Worth noting what this says about the layers: 955 hermetic
tests, 164 mutations and a 96% coverage gate all passed over this defect for months, because a
single-threaded SQLite suite cannot express "two requests at once" and the mock never produced one.

Still open: the model blobs come from `*.r2.cloudflarestorage.com`, which the sandbox denies, so
`FRD-111` FR-6 and `FRD-112` FR-6 remain unanswered against a real model. Everything up to the
upstream call is now exercised end to end.

---

## 2026-08-06 — a real model in the stack (FRD-123)
The mock agrees with us by construction: it reports the token counts we tell it to, truncates when
we say so, and produces documents matching the schema because the same person wrote both sides. A
green suite against it proves the gateway is *self-consistent* — which is the failure the mutation
harness exists to warn about, one level up.

So Ollama joins the stack behind a `verify` Compose profile. **Built as the OpenAI dialect, not
against Ollama's native API**, and that is the whole reason it was worth doing now: `ADR-0011`
already said the OpenAI wire format arrives regardless of `FRD-106`, because `FRD-120` (Azure
OpenAI) needs it. Building against the native API would have been a fourth dialect serving only us.
This way `FRD-120` shrinks to a transport, and the deferred OpenAI *surface* gets cheaper too.

The dialect turned out to have its own version of a trap the other two already taught us. Anthropic
splits usage across two events, so a last-event-wins mapper reported zero input tokens for every
stream. Here, **usage arrives in a final chunk with an empty `choices` array** — a mapper indexing
`choices[0]` loses it — and the vendor reports no usage on a stream *at all* unless
`stream_options.include_usage` is sent. A stream that reports no usage is *released* rather than
settled (`FRD-405`), so forgetting that one field would have made every streamed request silently
free. Both are pinned.

`FRD-111` §5.2 predicted the other one before this dialect existed: the vendor takes an abstract
`reasoning_effort` and **no token budget at all**, so a `limited` request has no faithful mapping.
It is refused rather than rounded — rounding 20 000 tokens to "high" spends a different amount than
was asked for and nothing about the answer would show it.

### The architecture assertion did its job

`test_no_code_above_the_adapters_knows_the_vendor` failed, because the new dialect imported
`to_json_schema` from the Anthropic one. The lazy fix is to widen the test's allow-list. The right
one is that the translation was never Anthropic-specific — it is canonical → JSON Schema, two of
the three dialects want it, and it now lives in `core/schema.py`. A dialect importing from another
dialect is exactly how "the canonical core is provider-agnostic" quietly stops being true.

### What is *not* verified yet, and why that is written here

The container runs; the model registry (`registry.ollama.ai`) is denied by this sandbox's default
network policy, and so is the Hugging Face fallback. So the adapter is complete and hermetically
tested (38 tests) and **the two questions it exists to answer are still open**: whether thinking
and structured output are reachable through the compatibility surface, and where thinking tokens
are counted (`FRD-111` FR-6). The catalog seed therefore declares **neither capability** — absence
of information is not permission, and declaring one on a guess is the single thing `FRD-114` says
the catalog must never do.

Five integration tests are written and skip with a reason naming `make verify-up`. The first is the
one that motivated this: send a request with a marker in it, then assert the marker is in the
stored `request_payload`. `FRD-103` has always claimed the prompts are stored, and every test that
checked it compared our own bytes with our own bytes.

Prices for local models are **invented, and say so in their own display name** — a local model
costs nothing, an invented price is what makes `FRD-403` demonstrable end to end, and the
distinction has to survive being pasted into a report, so it lives in the data rather than in a
comment.

---

## 2026-08-06 — Stufe 5+6: thinking, structured output, embedding options
`FRD-111`, `FRD-112`, `FRD-113` — and, in the same change, `FRD-107` **Stage B**, because building
a capability and then continuing to refuse it at the compatibility surface helps nobody. The KIRA
wire format did not move; the fields Stage A refused by name are simply served.

**Thinking** is the one with money in it. Budgets reach 32 768 tokens, billed as output, which is
an order of magnitude more than a typical answer — so the resolution and the reservation have to
produce the *same number*, and they do: resolved after routing against the model that will serve
the request, then handed to `enforce_pre_dispatch` as `extra_tokens`. `None` and `disabled` stayed
distinct on purpose: the first means the model was never going to think, the second means it
*would have* and this request is switching it off, and collapsing them lets a declared default
quietly win over a caller who asked for none.

**Structured output** turned out to be the clearest case of `ADR-0011` rule 3. One flag,
`structured_output`, over three unrelated mechanisms: Gemini has a schema parameter, Anthropic has
none and needs a forced tool call read back out of a `tool_use` block, Azure has a third. The flag
says *whether*; the dialect owns *how*. The schema itself is parsed rather than passed through, so
an unknown field is an error **naming the field** — and then forwarded, never executed, because
re-validating would mean running caller-supplied regexes over provider output on the hot path,
which is the exposure `ADR-0007` already refused by a different door.

§5.3 is the part that justifies the design and it is the test that had to be written to fail first:
with a fallback chain, checking the capability against the model the *caller named* protects
nothing. The primary declares it, the primary fails, the fallback answers in prose, and a caller
calls `JSON.parse` on it — surfacing days later as a bug in somebody else's code.

**Embedding** carried a control bypass. `FRD-405`'s bucket took one token per request, so a batch
of 500 admitted as one request would have turned a limit of 10 per minute into 5 000 texts per
minute: intact on paper, gone in practice. The bucket now takes a `cost`, in the same all-or-
nothing Lua pass, and the budget books n requests. A batch too large for the bucket's *capacity* is
refused with a message naming which of the two said no, rather than a `Retry-After` that would
still be wrong an hour later.

### Three things the tests found

The suite caught a **regression in my own design**: the plan had the predecessor's default task
type filled in by the mapper, which meant every embedding against a model nobody had declared task
types for was refused as though the caller had asked for something impossible. The default is a
*surface's*, applied only where the model declares it — and an explicit undeclared type is still
refused. Naming a type we cannot verify is a request; naming none is not.

`check_declaration` compared `method == "embedContent"`, so the new batch verb demanded the
*generation* capability — refusing every batch against an embedding-only model and accepting one
against a model that cannot embed at all. The same shape as the `:embedContent` bypass, one verb
later, and now a `frozenset` for exactly that reason.

The mock never truncated a schema-constrained document, so FR-6 — refuse an incomplete document
rather than return it as data — was exercised by nothing. A mock that always finishes cleanly is a
mock that makes a check look tested.

Eleven test doubles implemented the old `embed(model, text)` signature. They were widened rather
than left permissive: a stand-in more permissive than the thing it replaces is how a real defect
hid behind a green suite here before.

### And two the mutation harness produced

Fourteen mutations survived the first run. Nine were **anchors that had moved with the refactor** —
a mutation whose anchor no longer applies protects nothing, which is why the harness reports one
rather than skipping it. Repairing them is not bookkeeping: `M1`, `M2` and `M7` all describe the
rate limiter, and all three had quietly stopped being checked the moment the bucket learned to take
a cost.

Two were real, and both are worth stating:

**`C4` survived because the rule was enforced twice.** "A model that declares no embedding refuses
one before dispatch" lived in `check_declaration` *and* in `embedding.validate`, so removing either
changed nothing observable. That is what redundancy looks like from the outside, and it is a defect
in the making — two places deciding one rule drift, and the one that drifts is whichever is not
under test. The duplicate is gone; `validate` owns it.

**Two survived only because their test selection was too narrow** (`T10`, `E8`). The harness's own
docstring already warns about this and it has now cost time twice, so the warning has earned a
second sentence.

Mutations **T5–T10, S1–S7, E1–E8** — 21 new, 164 total, all defended. 896 hermetic tests, 96% coverage.

**Owed, and said rather than assumed:** `FRD-112`'s audit digest (the function exists and is
tested; the column needs a migration) and `FRD-111` FR-6's verification against a real upstream —
whether the provider folds thinking into reported output tokens or reports it apart is not
knowable hermetically, and the recorded cost is understated if we guessed.

---

## 2026-08-06 — Stufe 4: the predecessor's contract, served by AIRA
`FRD-107` Stage A. `/kira/api/external` with `chat`, `streaming-chat`, `embed`, `models`, `health`,
`version-info` and `ki-usage`; the predecessor's error envelope and codes; integer model ids;
attribution; deprecation headers on every response including the refusals.

**Stage A carries documents.** The plan had attachments in Stage B, `FRD-110` landed first, and
refusing a capability we have would be silly. Only `thinking` and `responseSchema` are refused —
plus one case the FRD singled out that turned out to matter: **a model whose catalog declares a
non-`disabled` default thinking mode is refused**, because the predecessor *applies* that default.
Serving such a model with no thinking at all would answer differently for a reason nobody could
see, which is the same failure as a dropped attachment one level up. A model with no thinking
declaration, or one whose default is `disabled`, is unaffected — sending nothing is what it asked
for.

`/embed` refuses a list and a `task_type` by name rather than approximating: embedding a batch one
at a time would silently cost N requests of quota against a limit of one, and the wrong task type
produces vectors that retrieve measurably worse with nothing in the response to show it.

**The real work was the extraction.** §5.1 says the surface shares the pre-dispatch gate, the
pipeline, the dispatch chain and the audit writer. Sharing them means extracting them, so they now
live in `api/serving.py` outside any surface and both routers use it. The alternative — a second
copy — is the `:embedContent` failure in a larger costume: a control that lives inside one branch
instead of on the path every branch takes, except the branch is now a whole API.

What holds that is one test: send a request through each surface, compare the audit rows. Same
outcome, same model, same tokens, same latency recorded, same degradation snapshot. It is the only
way to be sure no step was *skipped* rather than merely present.

Seven existing mutation anchors followed their functions into the new module and were repaired.
A mutation whose anchor has moved protects nothing, which is exactly why the harness reports a
missing anchor instead of skipping it — one of them (`M23`, "every verb passes the pre-dispatch
controls") also had to have its *text* corrected, and it briefly survived until it did.

Not in Stage A, and said rather than approximated: `ki-usage` reports per **user** with a model id
of `0`. The predecessor keys usage by (user, model); `FRD-601` aggregates the two separately, and
inventing a cross-tabulation would be a fabricated figure.

143/143 mutations, 766 hermetic tests, 78 integration, 46 browser.

---

## 2026-08-06 — Stufe 3: documents, and the rule that a refusal beats a fluent wrong answer
`FRD-110`. `CanonicalMessage` carries ordered parts; the Gemini surface takes `inlineData`; both
dialects map it; the mock sees it; the reservation counts it; the audit row keeps a description.

The owner stated the requirement in one sentence and it is the one everything here serves:
**if the model cannot read the document, throw an error — do not try anyway, or the model
hallucinates and the user thinks something else is broken.** That is exactly right, and it is worth
spelling out why it is not merely tidy: a dropped attachment produces *no error*. It produces a
fluent, confident answer about a document the model never saw, returned with a 200, and the caller
reports that "the model is hallucinating" and looks for the fault everywhere except where it is.

So a model that cannot read what was sent is refused **by name**, with the types it lacks, and the
message distinguishes *undeclared* (a catalog gap somebody closes in a minute) from *declares no
attachment support* (a fact about the model). Checked after routing at every hop, on the mechanism
built for exactly this last commit.

Four decisions worth keeping visible:

- **`text=` still constructs and `.text` still reads.** The whole existing suite passed unmodified
  against the reshaped model — which is what turned a change that "reaches everything" into a
  change to one file. The care needed is elsewhere: `.text` was total and is now **lossy**, so the
  injection filter and the routing classifier see the prompt and not the document. That blind spot
  is a property with a test rather than a comment.
- **Stripping is not redaction.** Attachment bytes are removed before the redactor runs, and
  unconditionally, because a deployment that swaps the redactor must not be able to turn it off.
- **The mock sees attachments.** One that ignored them would let every hermetic test pass while the
  real path was broken, and the feature would be exercised only against a cloud nobody has in CI.
- **Embedding refuses an attachment** rather than embedding the prompt without it — the same rule
  one level down (`FRD-113`: chunking a document is the consumer's decision).

**And then the integration layer earned its keep again.** Running the suite repeatedly to check for
flakiness turned up a failure at roughly one in eight: a client dropping the socket mid-stream
sometimes **vanished from the audit log**.

Nothing to do with documents. Closing a generator from inside the process raises `GeneratorExit`,
and awaits in a `finally` run normally — which is why the hermetic disconnect test passes
deterministically and has since the day it was written. A real socket dropping **cancels the
response task**, and a bare `await` in that `finally` re-raises `CancelledError` at its first
suspension point: the settle and the row were simply lost. `FRD-405` B4 promised this path is
accounted for. It was — in-process only.

Shielded now, and verified over 15 consecutive runs after rebuilding the container (the first
"fix" appeared not to work because the container was still on the old image, which is its own
small lesson). Deliberately given **no** mutation entry: no hermetic test can distinguish the
shielded version from the unshielded one, so an entry would be a false claim, and a harness that
makes one is worse than no harness. `tools/mutation_check.py` now says that in its own docstring,
and the integration test carries the explanation so nobody re-runs the flake away.

135/135 mutations, 738 hermetic tests, 73 integration, 46 browser.

---

## 2026-08-06 — The fallback chain learns to say no
A question from the owner — *is the region set for the whole gateway, or can it be bound to a use
case?* — turned into a smaller and more urgent finding than the one it asked about.

**The honest answer to the question:** deployment-wide. The allow-list is global and the region is
a property of the model; a use case can only influence it indirectly, through `allow_check`, which
it configures itself and can therefore widen. That is self-service, not governance. Per-use-case
residency is a real requirement and a **governance extension** — it is not built, and it should be
its own FRD rather than smuggled in here.

**What was actually broken** is one level down: `dispatch_with_fallback` had no notion of a
condition at all. It tried candidates in order and returned the first success. Nothing could
express "not that one", so nothing could enforce it — not residency, and not the attachment rule
`ADR-0012` §3 already states for documents.

So the mechanism is built now, before the feature that needs it: the chain takes conditions, a
candidate that fails one is **skipped with its reason kept**, and an exhausted chain fails. The
reasons are on the audit row, which is what somebody actually needs when they ask why an answer
came from the model it did.

Two present-day defects fell out of it:

- **A model no provider serves was a silent `continue`.** A typo in a fallback chain was
  invisible: the chain simply behaved as though the entry were not there, and nothing said so.
- **An exhausted chain raised `UpstreamError`**, which the route mapped to a **502**. So a
  configuration mistake read as "the provider is down" and sent whoever looked at it to the wrong
  place. It is now `NoCapableModel` → **400 FAILED_PRECONDITION**, naming each candidate and why it
  was excluded. `Outcome.NO_CAPABLE_MODEL` — declared in `FRD-122` and until now unreachable —
  finally has a producer.

Residency is the first condition. It cannot refuse anything a correctly configured gateway offers
today, since every model was already checked against the allow-list at startup — and it is built
anyway, because the *per-hop* check is the part that would otherwise be got wrong when residency
becomes a per-use-case property. Media types (`FRD-110`) and the schema capability (`FRD-112`) plug
into the same mechanism.

One existing test had to change, and the change is the point: it asserted that an exhausted chain
raises `UpstreamError` — it encoded the misleading behaviour. It now pins both halves: a chain with
nothing to offer is not an outage, and an upstream that *was* tried and failed still is.

**A follow-up question found the real version of the same mistake.** *"Wird es auch für Azure
`westeurope` funktionieren?"* — and the honest answer was: the mechanism yes, the configuration no.
`RegionAllowed` was always generic (it reads whatever the adapter declares), but the allow-list sat
behind a **`vertex_`-named setting with Google-only defaults**. The first Azure model would have
failed a check named after Google, and an operator widening `AIRA_VERTEX_ALLOWED_REGIONS` to admit
`westeurope` would have had a setting named after one cloud governing two.

`ADR-0012` §6 had already decided "one allowed-region list across every transport". The
implementation had quietly not done that. Moved to `aira_gateway.residency` with
`AIRA_ALLOWED_REGIONS`, and the default now covers the EU regions of **both** clouds — Azure's
listed before Foundry exists, on purpose, because the alternative is learning that a policy list was
written for one cloud by watching the first model of the other be refused.

The names stay flat (`eu`, `europe-west1`, `westeurope`) rather than qualified per provider: they do
not collide, and an operator thinks in "which EU regions may we use", not in a matrix.

124/124 mutations, 714 hermetic tests, 69 integration, 46 browser.

---

## 2026-08-06 — Stufe 2: the EU, and the first vendor that does not speak Google
`FRD-115` and `FRD-119`. One `VertexTransport` — URL, OAuth, region, error mapping — with two
dialects above it: Gemini bodies unchanged from `FRD-304`, and the Anthropic Messages API.

**Residency is enforced rather than intended.** A configuration that *can* express a non-EU region
is a configuration in which somebody eventually adds one, because that is where a preview model
launched, and nothing objects. So the allowed regions are a list, a model outside it makes the
gateway **refuse to start**, and provider/publisher/region land on every audit row. "The
configuration says EU" is a claim; "this request went to `eu`" is evidence, and `FRD-601` can now
break spend down by it.

**An ambiguous routing table is a startup failure.** `ProviderRegistry` assigned by iteration, so
the last provider registered silently won. With one adapter that was harmless. With three —
Generative Language, Vertex Gemini, Vertex Anthropic — it becomes a silent decision about which
region and which credential handled a request, invisible in every log and every report.

**The token holder is shared, not Google's.** Cache, refresh at 80% of lifetime, collapse
concurrent refreshes into one, keep serving a valid token through a failed refresh, back off rather
than retry every request. Identical for Vertex, Foundry and a static key; only the acquisition
differs (`ADR-0011` rule 1). Written once because getting that race right per platform means
getting it wrong on the second one.

Anthropic's differences are each a mapping: `max_tokens` **required** (always sent, from the
catalog's per-model default), the system prompt as a top-level parameter with several messages
concatenated rather than reduced to the last, cache tokens counted as input because they *were*
input, streamed usage **accumulated** across `message_start` and `message_delta` where Gemini puts
everything in the last chunk — and **thinking blocks dropped**, which with Gemini was free (we
simply never ask) and here is an active obligation.

**The architecture assertion is now a test.** `FRD-100` has claimed since Phase 1 that the
canonical core is provider-agnostic, and until today "two upstreams" meant two spellings of
Google's format — the claim had never been tested.
`test_no_code_above_the_adapters_knows_the_vendor` parses every module outside `upstreams/vertex/`,
strips docstrings, and fails if a vendor name appears in code. **It passes.** What did change
outside `upstreams/` changed for FR-6 and FR-10 — refusing an ambiguous table, recording where each
request went — which are platform requirements, not the dialect leaking.

**One mutation survived, and it is the one worth writing down.** `V4` guards "the model's reasoning
never reaches the caller". The test put the reasoning in the vendor's own `thinking` field — so
removing the block-type filter changed nothing, because the *field name* differed too. It passed
for a reason that had nothing to do with the property it was named after. Rewritten to put the
reasoning in a `text` field, which is what actually holds the selection to being by **block type**;
a second test pins that an unknown future block type is dropped too.

The integration layer also caught a race it had always had: the test asserted that *its own* relay
published the event, and on a full stack the `management-relay` container usually wins. It now
asserts the outcome — the row reaches the gateway — rather than who delivered it.

Verified: 699 hermetic tests (97.1%), **116/116 mutations caught**, 69 integration tests, 239
frontend and 46 browser tests. Not built here, deliberately: the thinking, structured-output and
attachment mappings, because the canonical core does not carry those fields yet and a mapper for a
field that does not exist is a guess.

---

## 2026-08-06 — Stufe 1: the model catalog becomes a runtime authority
`FRD-114`. The catalog held prices; it now holds what a model may be *asked to do*, and the gateway
decides from it. One shared vocabulary in `aira_common.models` — two copies of "which capabilities
exist" would drift, and the drift would surface in whichever plane was not tested.

The rule everything turns on is **undeclared means the baseline and nothing more.** The tempting
default is the opposite: let an undeclared model accept everything and let the provider complain.
That is wrong for the same reason "unpriced is not free" is wrong — absence of information is not
permission, and an undeclared model would otherwise accept a 32 768-token thinking budget the
pre-dispatch reservation has nothing to estimate against.

Management validates a declaration **where it is written**, because the catalog is a runtime
authority and a self-contradictory declaration would otherwise be discovered as a vendor error on
every request against that model. The rule with teeth: a thinking maximum at or above the output
cap describes a model that could never answer, since Anthropic draws thinking tokens from
`max_tokens`. A PATCH is merged over the row before validating, or a change to `max_output_tokens`
alone would be checked against a thinking block it cannot see — each half valid, the row not.

Enforced today: the output cap, the per-model **default** cap (which sharpens the reservation for
every vendor, not just the one that requires it), `generate`/`embed`, and a deprecation `Warning`
header. Deprecation **warns**; revocation blocks. Conflating the two removes the ability to
announce a retirement before performing one.

**`model_prices` is now `model_catalog`.** A table that decides whether a thinking budget is
accepted must not be called *prices*. That cost four raw-SQL integration tests an update, which is
what a rename costs — and it turned up something that had nothing to do with this FRD:

> During the rolling rebuild, the **consumer** container was still on the old image, and its
> `create_all` **recreated `model_prices`** — then failed every model event against a table Alembic
> had renamed. Nothing crashed. The declarations simply never arrived, which presents as "the
> feature does not work".

`create_all` alongside Alembic means a partially-deployed stack can undo a migration, silently.
Written up in `DEPLOYMENT.md` §6a with the upgrade procedure; the durable fix — stop calling
`create_all` outside tests — needs the demo and CI paths to build from migrations and is on the
backlog rather than smuggled into this release.

The frontend edits the flat fields with real controls and leaves the nested thinking/embedding/
attachment blocks to the FRDs that give them meaning. A bespoke editor for a feature that does not
exist yet is a guess about what it will need.

Verified: 663 hermetic tests (98.4%), **104/104 mutations caught**, 64 integration tests — including
the declaration travelling the real outbox → relay → Kafka → consumer route into the migrated
schema — 239 frontend tests and 46 browser tests. The browser suite found one of its own
assertions had become positional: it picked "the first warning badge", and there are now two.

---

## 2026-08-06 — Stufe 0: the audit trail now records what was refused
First stage of the delivery order, and the one that makes every later stage testable. `FRD-122`
implemented: `aira_gateway/audit.py` (closed `Outcome` vocabulary + the `AuditTrail` a route fills
in as it goes), migration `0012` (six nullable columns, indexed on `outcome` and `credential`), and
the recording site.

**Refusals are written at the route's exception boundary, once.** The obvious alternative is a
`record_request` beside each `return _error(...)`; there were half a dozen of those and the next
verb would add more. That is not a hypothetical concern — it is exactly how `:embedContent` came to
bypass the pre-dispatch gate, because the gate lived inside one branch instead of on the path every
branch takes. So the branches now **raise** and the boundary records.

Two things the work found that the plan did not have:

- **A full writer queue turned a correct 429 into a 500.** The test for FR-7 was written expecting
  to pass; it failed. The audit write was propagating out of the refusal path, so a client that hit
  a rate limit got a server error — and would have retried straight into the limit it had just hit.
  Guarded now, and deliberately **only** on the refusal path: on the success path a failed write
  means a *served* request went unrecorded, and failing loudly is the defensible answer to that.
- **A refusal was naming the model the caller typed, not the one attempted.** A request routed
  elsewhere by the pipeline and then refused blamed a model that was never called. Found by a
  mutation surviving (`T3`), which is the harness doing precisely what it exists for: the property
  looked covered and was not.

Also repaired the `M23` anchor — the pre-dispatch gate lost its `try/except` when refusals began
raising, so the mutation that guards "every verb passes the controls" no longer applied. A mutation
whose anchor is gone protects nothing, which is why the harness reports missing anchors rather than
skipping them.

One design point worth keeping visible: pipeline decisions are persisted through an **allow-list**,
not a deny-list. A step that starts recording the classifier's explanation would otherwise begin
persisting model output about a caller's prompt the day it is added — silently, in a column
redaction cannot process.

Verified at four layers: 620 hermetic tests (99.2% coverage), **96/96 mutations caught**, 60
integration tests against the migrated Postgres schema (asserted separately, because the hermetic
suite builds its schema with `create_all` and would pass with an empty migration), 232 frontend
tests and 46 browser tests. The Reporting screen now shows refusals beside successes, so a use case
grinding against its budget wall is a figure rather than a log search.

---

## 2026-08-06 — A delivery order, and the one place the priorities fight the dependencies
The owner set the priority: **KIRA compatibility first, then the Google and Microsoft model
connections (*easily extensible*), then document handling, then the review findings** — with the
instruction to record my findings as features so they are not forgotten. PRD gains §1.2 (seven
additional features from the code review) and §1.3 (the priority).

**Priority 1 depends on priority 3.** `FRD-107` §5.2 is explicit: a KIRA surface built before the
capabilities exist would accept fields it silently ignores, and a caller cannot tell that their
document or their thinking budget was dropped. That is worse than a refusal.

The resolution is a stage boundary rather than a wait. **`FRD-107` Stage A** ships the text contract
— chat, streaming, embed, models, health, version-info, ki-usage, the error vocabulary, the integer
model ids, attribution, the deprecation headers — and **refuses**, in the predecessor's own error
vocabulary, any request carrying a field it cannot yet honour. **Stage B** moves those fields from
refused to served with no change to the contract, because refusing was always the correct behaviour
for a field we could not serve. Every consumer sending plain text — the majority — migrates months
before the ones sending PDFs, and nobody is misled in between. The one thing Stage A must not do is
*approximate*: KIRA applies a model's default thinking when the caller sends none, so Stage A either
applies the real default or refuses; quietly sending no thinking at all would make answers differ
for reasons nobody can see.

The full order is now in ROADMAP Phase 8. Two deviations from a naive reading of the priority list,
both written down so they can be overruled rather than discovered:

- **`FRD-122` (audit) goes first, not last.** It is one of "my points" and it is also the cheapest
  item in the programme — additive columns, one recording site, no request-path change. Every stage
  after it produces traffic that ought to be evidenced, and retrofitting the audit once four vendors
  and two API surfaces are live is strictly harder than doing it while there is one of each. It also
  changes what every later test can assert.
- **Documents come after the EU connection**, not before. Without a document-capable model reachable
  in the EU, document support could only be exercised against the mock — which is not the capability
  that was asked for.

One feature named at the owner's own emphasis and worth repeating: **extensibility, as a measurable
property.** "So dass es einfach erweiterbar wäre" is a claim until something checks it, so it has a
test rather than an intention — adding a model family must not change anything above `upstreams/`
(`FRD-115` §10). If a diff does, the canonical core is vendor-shaped, and the core is what gets
fixed rather than the adapter. `FRD-120` (Foundry) is where that gets proved, which is part of why
it sits after the first two vendors rather than being deferred indefinitely.

---

## 2026-08-06 — The feature list, and what it makes visible
The owner restated what AIRA Gateway *is*, as seventeen features. It now sits in **PRD §1.1** with an
honest status column, because a list like that is only useful if the gaps are in it too.

Three things the table makes visible that prose was hiding:

**The governance features are largely built; the evidence features are not.** Budgets, limits,
routing, fallback, self-service pipelines, roles — done. Auditability, incident response, anomaly
detection, model smoke tests — missing. Those four are what make a governed system *defensible after
the fact*, which is the half you need on the day something goes wrong.

**Feature 5 is more specific than "store requests and responses".** *"Welches System wann was womit
aufgerufen hat"* — and checked against the code, the **system** is exactly the part we cannot
answer. An API key has a `prefix` (its identity) and a `subject` (the person who issued it); the
audit row records the subject and never the prefix. Five keys issued for one use case by one
administrator are one identity in the log. The consequence lands precisely where it hurts most: a
leaked key can be revoked, but the blast radius cannot be assessed — which requests came from it,
what they asked, over what period, none of it separable from its siblings' traffic. Added to
`FRD-122` as FR-5.

**Feature 3 settles `ADR-0010`.** Naming KIRA-API compatibility as a *central feature* is the
decision that ADR was waiting for. Accepted as Option C — the compatibility surface is built, with a
sunset date and its usage visible in reporting, because that is the half that keeps a compatibility
layer from becoming permanent. `FRD-107` is unblocked.

New: **`FRD-504` — model smoke tests and jailbreak batteries.** IT Security's question is not "was
this request allowed" but "does the model we approved for the whole organisation still refuse what
it should". Two design points I want to keep visible:

- **A rate, not a verdict.** Models are sampled: the same jailbreak prompt can be refused nine times
  and answered on the tenth, and *that is the finding*. A single-run boolean would show green nine
  times out of ten and the tenth would look like a flake to re-run away. So each case runs *n* times
  and the result is "3 of 20", never "failed".
- **Two modes, because the pipeline would block the test.** `FRD-300`'s injection filter exists to
  block exactly the prompts a jailbreak battery sends — run through it, most cases never reach a
  model and the run says nothing about the model. So: *through the pipeline* (does our filter catch
  it — the first honest measurement of whether that filter earns its place) and *direct to the
  model* (does the model resist it), reported side by side. The interesting cell is the one where
  the filter misses **and** the model answers.

Runs go through the gateway's own path under a dedicated internal use case, so their spend is
attributed and bounded rather than exempt — and the result page states that it is one battery on one
day and not a safety statement, for the same reason unpriced traffic is counted apart: a figure that
reads as complete when it is not causes worse decisions than an absent one.

---

## 2026-08-06 — "Auditierbares Hirn": a scope sentence, and four places we do not earn it yet
Direct model access confirmed — the Vertex publisher and endpoint APIs, not the platform's agent
surface. That closes the last open question in `FRD-115` §11 and `FRD-119` §11.

What came with it is worth more than the answer: *the gateway's job is to provide **auditable
brains** for AI use cases.* That is a scope sentence, and it settles questions that had not been
asked yet. `ADR-0013` records it with a test for future requests — **does this make model access
better governed and better evidenced, or does it make the gateway think for the use case?** The
second kind always arrives disguised as the first ("just let the gateway keep conversation history,
every team is reimplementing it"), individually reasonable and collectively turning a control point
into an application platform.

Out, explicitly: agent surfaces, retrieval and vector storage, conversation state, tool execution,
workflow orchestration, content understanding. `FRD-121`'s document conversion sits on the far side
of that line and is now marked as such — which is the reason its own recommendation is to not build
it first.

**Then I took the word "auditable" literally and reviewed against it.** Four gaps, and the first is
not small:

1. **A refused request leaves no record at all.** Rate-limited, over budget, unknown model, failed
   validation — the route returns before `record_request` is ever reached. `request_logs` therefore
   contains **what was served, not what was asked**. So "who was throttled, how often, starting
   when, and was that why the application misbehaved on Tuesday" is unanswerable from the audit
   trail, and `FRD-601`'s `failed_requests` can only ever show upstream failures — a use case
   hitting its budget wall all day reports as perfectly healthy. A control that leaves no trace
   when it fires is a control nobody can review.
2. **Only the served model is recorded, never the requested one.** With cross-vendor fallback
   (`ADR-0012`) a request asking for Gemini can be answered by Claude and nothing durable says a
   substitution happened. "Why did the Anthropic spend triple" has no answer in the data.
3. **Pipeline decisions live on a span, not on the row.** `aira.pipeline.model` is a span
   attribute — and spans are **sampled**. So *why* a model was chosen, for the one component that
   makes a judgement about a caller's prompt, is durably recorded nowhere.
4. **Degradation is global, not per-request.** `DegradationLog` says what is broken *now*; an audit
   needs what was broken *then*. A request budgeted on the racy Postgres fallback is
   indistinguishable from one with the atomic guarantee.

`FRD-122` closes all four: one recording site at the route's exception boundary (not one per
`return _error(...)` — that is the shape that let `:embedContent` bypass the pre-dispatch gate);
`requested_model` alongside `model` so existing reports and indexes keep their meaning; decisions
but **never the classifier's reasoning text**, which is model output about a caller's prompt and
inherits every question the prompt has; and the degraded set frozen onto the row.

One thing decided rather than deferred: recording refusals means a caller in a retry loop writes a
row per attempt. That is the right increase — a retry storm is *precisely* the event the audit
trail should show. If a deployment finds it excessive, the answer is a shorter retention for
refusal rows, not recording nothing.

Until `FRD-122` ships, "auditable" is a claim the data does not fully support. Worth saying plainly
in the DEVLOG rather than only in an FRD.

---

## 2026-08-06 — Four model families, and the one thing that does not generalise
Bringing Gemini Enterprise / Model Garden and Microsoft Foundry together over Gemini, Claude, GPT and
Nemotron. Two findings while thinking it through, and the second is the one that matters.

**The transport × dialect grid is a matrix, not a diagonal.** Model Garden is two things under one
name: publisher-managed models (Gemini, Claude) addressed as `publishers/{vendor}/models/{model}`,
and **self-deployed** models — NIM containers such as Nemotron — running on our own capacity,
addressed by a **numeric endpoint id** and speaking an **OpenAI-compatible** API. So the OpenAI
dialect is needed on the *Vertex* transport, not only on Foundry. `ADR-0011`'s separation starts
paying for itself before the third platform is built, and `ADR-0011` rule 2 (the caller names a
model, the catalog holds the addressing) turns out to have been necessary rather than prudent — a
fourth addressing mode arrived within a day.

Self-deployment also brings failure modes that managed models do not have, and treating them alike
would produce two surprises rather than one: an endpoint scaled to zero **cold-starts for minutes**
(a budget reservation held open that whole time, a rate-limit token already spent, a fallback chain
burning its primary timeout instead of failing over), and its **429 means no free replica**, not
quota — so retrying the same endpoint cannot help. `hosting` becomes a declared property that the
dispatch timeout, the retry decision and the readiness probe read; and the probe must **not** wake a
scaled-to-zero endpoint, or it spends GPU minutes to answer a question about availability.

**Documents are where unification would do real harm.** The predecessor's callers send PDFs — "here
is a document, answer questions about it" is a large share of what KIRA is used for. Across the four
families that capability is genuinely not uniform: **Gemini and Claude read PDFs natively, including
layout; a text-only GPT deployment and a NIM-hosted Nemotron cannot see one at all.**

The tempting behaviour is to let a fallback chain drop the attachment and carry on. It must not.
Dropping it does not produce an error — it produces a fluent, confident answer about a document the
model never saw, returned with a **200**, indistinguishable from a correct answer to everyone
including the caller. So `ADR-0012` §3: a chain **skips** a candidate that cannot read the
attachment, and **fails** if none qualifies. Failing is recoverable; being quietly wrong is not.

The practical shape is better than it sounds: Gemini and Claude are both document-capable and both
sit on the same transport and the same credential, so a document-capable chain with a genuine
fallback already exists without any conversion at all.

`ADR-0012` also fixes the governing principle for all of this, which was implicit until now:
**hide the plumbing, declare the semantics.** Clouds, credentials, URL shapes, streaming vocabularies
and structured-output mechanisms are plumbing and belong behind the canonical core. Anything that
changes *what comes back* — an attachment a model cannot see, a thinking mode it cannot honour, a
schema it cannot enforce — is declared, visible in the builder, and enforced after routing.

`FRD-121` specifies the opt-in conversion path (extract text or render pages) for the cases where
capability gating is genuinely too strict. Three constraints decided up front: never default, never
silent, and **never in the gateway process** — a PDF parser is a large attack surface on
caller-supplied bytes and this process holds the cloud credentials, so it belongs behind a managed
document service or an isolated worker. The recommendation in its own §11 is to **not build it
first**; ship the gating, run with it, and let a concrete blocked use case justify it.

`ADR-0012` and `FRD-121` written; `FRD-110` (chain homogeneity), `FRD-114` (`hosting`) and `FRD-115`
(self-deployed endpoints, the matrix) amended.

---

## 2026-08-06 — A third platform decides the shape of the second
Microsoft Foundry is wanted for the future: Azure OpenAI models and Microsoft's own. Not urgent —
and precisely because it is not urgent, it is the right moment to let it settle the upstream
architecture, since it is the third vendor and the third is where the abstraction is decided. With
two you can always absorb the difference in a conditional.

Foundry brings three things Vertex did not: the **OpenAI Chat Completions** wire format, **Entra ID**
authentication, and a different notion of what a model *is* — Azure addresses a customer-named
**deployment** in a resource in a region, and the same deployment name in two resources can be two
different models.

`ADR-0011` records the resulting shape: **transport × dialect × model identity.** A transport owns
reaching the vendor's cloud (endpoint, credential, retries, quota errors); a dialect owns the API
shape (bodies, streaming events, usage, capability mechanisms); an upstream composes them. Vertex is
one transport with two dialects; Foundry is one transport with the OpenAI dialect — which is then
reusable by any platform that speaks it, and that reusability is most of the justification.

Three rules came out of it, each fixing something that would otherwise have been decided by
accident:

- **Credential acquisition is one abstraction.** All three platforms need identical behaviour —
  cache, refresh ahead of expiry, single-flight, serve through a failed refresh — and differ only in
  how the token is obtained. `FRD-115`'s token holder becomes a shared `TokenSource`. Writing that
  refresh race three times means getting it right three times, and the second one is always the one
  that is subtly wrong.
- **A caller names a model; the platform's addressing is catalog configuration.** No use case's
  pipeline config may contain an Azure deployment name. The failure mode if we got this wrong is
  the interesting part: `FRD-403` prices by model name, a deployment called `production` has no
  price, and unpriced traffic is *counted apart rather than as zero* — so the spend figures would
  not break, they would quietly stop being complete.
- **Capability flags say whether, never how.** Three vendors, three unrelated structured-output
  mechanisms (a schema parameter, a forced tool call, a `json_schema` response format) and two
  reasoning shapes (token budget, effort level). The flag stays a boolean; the mechanism lives in
  the dialect.

Two pleasant confirmations. `FRD-111`'s canonical thinking model — `mode` + optional `tokens`, taken
from the *predecessor's* vocabulary — turns out to cover Azure's `reasoning_effort` levels, a vendor
it was not written for. And Azure reports reasoning tokens separately, which finally answers
`FRD-111` FR-6's open verification for at least one vendor.

One planning consequence: the **OpenAI wire format now arrives as an upstream whether or not
`FRD-106` is ever built.** Once canonical ⇄ OpenAI exists in one direction, the deferred OpenAI
*inbound* surface is largely that mapping reversed plus a router. The decision to defer it stands;
the estimate behind that decision does not, and should be revisited when it next comes up.

`ADR-0011` and `FRD-120` written; `FRD-114` (model identity and addressing), `FRD-115` (shared
token source, region allow-list generalised), `FRD-111` and `FRD-112` (the third mechanism) amended.

---

## 2026-08-06 — Model Garden answers one question and opens another
Two facts landed after the parity FRDs were written, and both change them.

**EU residency applies.** `FRD-115` moves from "worth doing" to required: our current adapter calls
a global endpoint and cannot make a residency statement, so it is not a production candidate no
matter how complete the rest becomes. The FRD now also *enforces* it — an allowed-region list, a
model configured outside it refuses to start, and provider, publisher and region recorded on every
audit row. Configuration alone would not hold: someone adds a model in `us-central1` because that
is where a preview launched, and nothing objects.

**Access is through the Gemini Enterprise platform's Model Garden — Gemini *and* Anthropic**, one
project, one credential. That is a governance win and a technical complication, because the two
vendors do not share a wire format. Anthropic models on Vertex are called through `:rawPredict` and
speak the Anthropic Messages API:

- `max_tokens` is **required**, and our canonical field is optional. A caller who omits it — most
  of them, since it is optional today — would get a vendor error about a field they never set. So
  `FRD-114` gains a **per-model default output cap**, which sharpens the budget reservation for
  both vendors anyway.
- **Thinking blocks come back in the response.** With Gemini, "we do not return chain-of-thought"
  was cheap: we simply do not ask. With Anthropic it becomes an active obligation — the adapter
  must drop them, and they must reach no response, log, span or audit row. A mapper that
  concatenates all content blocks is the obvious implementation and the wrong one, so it gets its
  own test. Their token *count* still reaches usage, because they were billed.
- Anthropic's thinking budget is drawn from `max_tokens`, so `budget < max_tokens` becomes a
  validation rule and the catalog must refuse to hold a combination that cannot work.
- **There is no `responseSchema`.** Structured output is a forced tool call — one tool whose
  `input_schema` is the caller's schema. So `FRD-114`'s `structured_output` flag means "by some
  mechanism", the adapter refuses schema fields it cannot express faithfully rather than dropping
  them, and `FRD-112` §5.3's post-routing capability check stops being defensive and becomes
  load-bearing.
- **No embeddings at all**, so `FRD-113` is Gemini-only and the capability declaration is what
  enforces it — before dispatch, not by an adapter raising deep in the stack.

New `FRD-119` for the dialect; `FRD-115` rewritten as the *platform* (transport, OAuth, region,
registry) with the two dialects above it. The seam matters: put authentication in the adapters and
it is written twice, put body mapping in the transport and a third vendor rewrites it. `FRD-110`'s
media-type allow-list becomes an intersection of what AIRA accepts and what the target model
accepts, checked after routing for the same reason the schema capability is.

This is also the first honest test of `FRD-100`'s claim that the canonical core is
provider-agnostic — until now "two upstreams" meant two spellings of Google's format. `FRD-115` §10
carries an architecture assertion for it: if the diff reaches outside `upstreams/`, the core is
Gemini-shaped and we should fix the core rather than smuggle a vendor field through it.

One question deliberately left open in `FRD-115` §11: whether "Gemini Enterprise" here means Model
Garden *raw model access* (assumed throughout) or the agent platform's own API, which is not a
model API and would model grounding and server-side conversation state that our canonical core does
not have. One authenticated `curl` against the project's `publishers/anthropic` endpoint settles
it, and getting it wrong is a rewrite rather than a correction.

---

## 2026-08-06 — KIRA parity: the programme, and where the gap actually is
The predecessor's requirements (`kira_api.md`, KIA-KIRA-API v0.1.2) arrived with the instruction
that AIRA must carry all of them. Reviewed against the code rather than against our own
documentation, the result was not what the phase history would suggest.

**In breadth we are well ahead.** Use cases with object RBAC, self-service keys, budgets down to
spend, cross-instance rate limits, the pipeline, Kafka config distribution, the management UI,
retention, cost reporting — the predecessor has none of it.

**In the core request path we are behind, and further than it looks.** `CanonicalMessage` carries
exactly one field, `text: str`, and the Gemini surface's `Part` requires `text` — so a request with
`inlineData` is not merely unmapped, it is **rejected with a 400**. The predecessor accepts
documents and images in fourteen MIME types, controls the thinking budget, and forces JSON output
against a schema. None of that exists here. Its embedding path takes eight task types, batches and
two dimensionalities; ours takes one string.

Two findings I had not expected to matter as much as they do:

- **Vertex AI, not the Generative Language API.** The predecessor calls `europe-west1` and the
  `eu` multi-region with a service account. We call the global endpoint with an API key. If a data
  residency requirement sits behind that configuration — and an `eu` endpoint in a production file
  is decent evidence — then no amount of feature parity makes our adapter a replacement.
  `FRD-115`, and it may be the most schedule-critical item in the programme.
- **Vault is in the stack and nothing reads from it.** `CLAUDE.md` §2 has said "secrets only in
  Vault" since Phase 0; every secret actually comes from an environment variable. `FRD-116`. This
  becomes pressing rather than untidy the moment a service-account *private key* is involved.

Eleven documents written: `ADR-0010` plus `FRD-107`, `FRD-110`–`FRD-118`, `FRD-602`.

**The one open decision** is in `ADR-0010`: does AIRA also serve the predecessor's *wire contract*,
so clients migrate by changing a URL, or do the clients move to the Gemini surface? My
recommendation is the compatibility surface **with a stated sunset date and its usage visible in
reporting**, because the alternative couples our decommissioning date to the slowest consuming
team, and until they migrate their traffic is ungoverned — which is the whole thing the budgets and
limits exist for. Recorded as *Proposed*; `FRD-107` stays blocked until it is decided. Everything
else is contract-independent and can start immediately.

Three places where the FRDs deliberately **do not** copy the predecessor, each written down so the
deviation is a decision rather than an omission: TLS verification stays on (`kira_api.md` sets
`verify=False`); CORS is an origin allow-list, not `*` with credentials; and `GET /models` requires
authentication. A fourth is close to it — the predecessor resolves group membership from the
UserInfo endpoint on **every request**, which would make each authenticated call depend on Keycloak
being up and fast; `FRD-118` §11 asks whether that requirement even applies to us before anyone
builds it.

Three design points inside the FRDs are worth repeating here because they are the ones most likely
to be got wrong quietly:

- **An attachment costs tokens no character count predicts** (`FRD-110` §5.3). The pre-dispatch
  reservation would estimate a 20 000-token PDF request as a sentence, reopening under documents
  precisely the race `FRD-405` closed for text.
- **A batch must not be a way around a rate limit** (`FRD-113` §5.3). One token per request means a
  caller limited to 10 requests a minute can embed 5 000 texts a minute. A batch of *n* takes *n*.
- **The structured-output capability must be checked after routing** (`FRD-112` §5.3). With a
  fallback chain, the model that answers is not the model that was asked for, and returning prose
  to a caller that will `JSON.parse` it is a failure that surfaces days later in someone else's
  application.

The OpenAI-compatible surface (`FRD-106`) is deferred by decision so parity is not competing with a
second new contract.

---

## 2026-08-06 — Reporting: the data has been collected since Phase 1, and is finally readable
Every dispatched request has been recorded since `FRD-103` and priced since `FRD-403`. Nothing
showed any of it. The only figures anywhere were the consumption bars beside a budget — one use
case, the current period, three numbers — so "what did last month cost, and which use case is
responsible" was a question answerable only with `psql`. That is most acute for **IT Steuerung**,
the role the PRD defines around exactly this oversight and which until today had a read-only list.

`FRD-601` closes it: `GET /v1beta/reporting?from=&to=` on the gateway (the request log lives in
its database), and a **Reporting** screen in the SPA. Totals plus breakdowns by use case, by model
and by member — requests, the prompt/completion token split, spend, failures, latency.

Three things were decided rather than defaulted:

- **The visibility rule lives at the edge, in one function.** Governance sees every use case;
  anyone else sees the use cases their token puts them in; a caller with neither gets an **empty
  report, not a refusal** — having nothing to see is not a failure. `None` (everything) and `()`
  (nothing) are deliberately distinct values rather than one falsy scope, because confusing them
  is the single mistake here that would show an installation's whole spend to somebody entitled
  to one use case. Both halves are pinned by mutations `N1`/`N2` and by the browser test.
- **Latency is an average and a maximum, and is called that.** A percentile is the figure an
  operator actually wants, but `percentile_cont` is Postgres-only and the hermetic tests run on
  SQLite. A dialect-dependent query would leave the production expression exercised only by the
  integration suite — precisely the shape of thing that breaks quietly. The compromise is
  documented in the FRD rather than papered over by calling an average a median.
- **Unpriced stays unpriced.** A request on a model with no price counts toward
  `unpriced_requests` and toward nothing else, in every breakdown row, and the screen says the
  spend is a lower bound whenever there is any. Same rule as the budget bars, same reason.

The index assertion is against `pg_indexes`, not `EXPLAIN`: on a test database of a few hundred
rows the planner correctly prefers a sequential scan whatever the schema says, so an `EXPLAIN`
here would have been measuring how much traffic the stack happened to have.

**What the layers caught, again in that order.** The unit and mutation passes were green before
anything else ran. The e2e layer then failed on two things that had nothing to do with reporting:
a helper that decided "this use case does not exist" from a `count()` taken while the list was
still a spinner — latent in an existing test since it was written, and only exposed once the row
it looked for actually existed — and `demo-uc`, which another test deliberately caps at five
requests a month, answering **429** to the traffic this test wanted to generate. The first was a
real race and is fixed in `support.ts`; the second was a bad fixture choice, and the visibility
test now makes its contrast on the same screen and the same period with two different users
instead of borrowing a use case another test owns.

Also, an environment trap worth writing down: recreating the Keycloak realm gives every user a
new `sub`, and Management binds users to `sub` (ADR-0007). The old rows keep the plain usernames,
so the new identities get provisioned as `ucadmin-dedf235d` and the e2e login assertion fails on a
name it has never seen. That is the binding working exactly as designed — but it means **a realm
recreation orphans the Management users**, and the fix is to drop the stale rows, not to loosen
the binding. Noted in `deploy/compose/README.md` next to the realm-import caveat.

---

## 2026-08-05 — The browser layer finally ran, and immediately earned its keep
The Playwright download had been blocked by network policy since the e2e suite was written, so
36 browser tests had never executed here. Allowed at last: **38 passed, 4 failed**, all four in
`gateway.spec.ts` — everything that needs the browser's own session token to reach the gateway.

The cause was not in either service. The SPA's container serves through nginx and proxies `/gw`
to the gateway; nginx was connecting to `172.19.0.4` while Docker's DNS had been answering
`172.19.0.10` for some time. **nginx resolves a literal hostname in `proxy_pass` exactly once**,
when the configuration loads, and keeps that address for the life of the process. Every restart
of the gateway container since then had been invisible to it.

This is a production defect, not a test artefact: in any orchestrator a redeploy gives the
container a new address, so every gateway redeploy silently breaks the dry-run and consumption
screens until somebody thinks to restart the frontend too. The symptom — "the gateway could not
be reached" — points at the wrong service, which is the part that would have cost the most time.

Both upstreams now go through a variable with a `resolver`, which defers resolution to request
time. Proved rather than assumed: the gateway was forced onto a different address (a placeholder
container took its old one) and `/gw` kept answering 200 with nginx untouched — `172.19.0.5` →
`172.19.0.14`, no restart.

Two guards added to the integration suite: that the SPA reaches both services through its own
origin, and that the rendered config still passes its upstreams through variables. The second
asserts the *shape* of the config, because the behaviour it protects only appears after a
container has actually moved. Verified to fail against the old form.

Also, and worth saying plainly: the six rate-limit e2e specs written earlier without ever being
run all passed. That was luck as much as care — writing tests one cannot execute is not a
practice to repeat.

---

## 2026-08-05 — The older phases under the same standard (74 properties)
The mutation check covered FRD-405 and the tombstone work; auth, budgets, the pipeline, retention
and the management control plane had never faced it. Two samples in older code had already turned
up one real defect each, so the odds of the rest being clean were not good.

The properties were derived **from the requirement documents**, not from the code — that is the
whole discipline, and reading in the other direction is what let the earlier defects hide. Four
parallel passes over FRD-101/102, FRD-200/201/202/204, FRD-400/401/403 and FRD-300/303/404 plus
ADR-0006/0007 produced ~50 candidate properties; 45 became mutations, for 74 in total.

**4 of 74 survived**, all of them missing tests rather than defects:

- **A key bound to one use case could act on another** — nothing defended the tenant boundary for
  API keys. Verified against the running gateway before deciding: the code is correct (403 on a
  foreign selector), so this was a hole in the suite, not in the product. It is the one that would
  have hurt most if it ever regressed.
- The half-price rule was tested in **one direction only** — an output-only model would have been
  accepted, billing nothing for the prompt.
- A model published **without any price** was never exercised through the consumer, so turning
  those nulls into zero would have made its traffic silently free.
- The default for whole-row deletion (`log_retention_days = 0`) was unpinned; a drift to non-zero
  would have given every installation a reporting horizon nobody chose.

One prediction was wrong in a useful way. I expected `JSON(none_as_null=True)` to survive, on the
grounds that SQLite cannot distinguish SQL NULL from the JSON literal — it is caught, by the
retention *idempotence* test, because the SQL-level `is_not(None)` still sees the difference. The
mechanism was not what I assumed, and checking which test failed is what showed that.

Deliberately excluded: the constant-time hash comparison. It is a timing property, and no
hermetic test can defend it honestly. Staging one that appears to would be exactly the
self-deception this exercise is against, so it is recorded as knowingly undefended instead.

---

## 2026-08-05 — Deleting a use case did not withdraw access
Found while looking for the next piece of work, and verified against the running stack before
being believed: **24 active API keys were bound to use cases that no longer existed**, and a
request with such a key answered **HTTP 200**.

Management cascades a use-case deletion in its own database — the foreign keys see to that — but
publishes only `usecase.deleted`. The gateway's handler removed the use case and its members and
nothing else, so keys, budgets, rate limits, pipeline configs and usage counters were left
pointing at nothing. Two consequences, and the first is the serious one: whoever deleted a use
case believed access had ended when it had not, and a slug created again later silently inherited
the deleted one's budgets, limits and pipeline.

The handler now cascades. Two asymmetries are deliberate. Keys are **deactivated, not deleted** —
delivery is at-least-once, so a re-delivered `api_key.created` would otherwise resurrect one, the
same reason revocation is terminal elsewhere (ADR-0007). And `request_logs` are **kept**: the
audit trail and the spend history are what a later question about what was spent, and by whom, is
answered from, so they outlive the use case on purpose (FRD-404 §4.1).

Migration `0011` clears what earlier deletions already left behind, since those never get a second
`usecase.deleted` event. Applied to the running database: 24 orphaned active keys → 0, orphaned
budgets and pipelines gone, all 73 request-log rows untouched.

One thing deliberately *not* done: refusing a key at authentication time because its use case is
unknown. It looks like cheap defence in depth and is not — keys and use cases arrive on different
Kafka topics with no ordering between them, so a freshly issued key can legitimately reach the
gateway before the use case it belongs to, and the check would refuse it.

Proved end to end over the real event path: the key answers 200, the tombstone is applied, the
same key answers 401. Three mutations added (`make mutants` is now 29), including one asserting
that the request log is *not* deleted — with a local import inside the mutation, so it fails on a
test rather than on a NameError, which would have counted as caught for the wrong reason.

---

## 2026-08-05 — Proving the tests can fail (`make mutants`)
Prompted by the obvious question after the review: the suite was green, coverage was 99%, and
seven real defects were in there anyway. How?

Three different mechanisms, not one.

1. **The tests were written from the code, not the requirement.** A test named "both scopes apply
   and the stricter wins" asserted *alice is refused* — which is what the code did. The
   requirement said more: *and it must cost the use case nothing*. Test and code came from the
   same mental model, so they agreed. Agreement is not evidence.
2. **Coverage measures lines, not properties.** `embedContent` was neither rate limited nor
   budgeted while its lines were fully covered — by the happy-path test. A missing requirement is
   invisible to a coverage tool.
3. **Two tests never reached the path they were named after.** `TestClient` buffers a streamed
   body before the test can hang up, and SQLite enforces no column lengths, so the "failing
   write" test's write always succeeded.

The response is `tools/mutation_check.py` (`make mutants`): 26 properties, each expressed as the
one-line defect that would break it. It applies each in turn and checks that something goes red.
The first run found five survivors — four real gaps (the failing-write test that could not fail,
the untested circuit-breaker reopening, an unasserted `maxOutputTokens` estimate, and an
unasserted `enabled` flag), and one false gap caused by too narrow a test selection, which the
tool now warns about because a false gap costs as much time as a real one.

The harness is crash-safe for a concrete reason: the first run was interrupted and left
`writer.py` mutated. Undetected, that would have looked like a genuine defect to whoever ran the
suite next. It now journals the original before each edit and restores from it on the next start.

After the four gaps were closed: **all 26 properties are defended**. The convention is in
`CLAUDE.md` — when you fix a bug, add the mutation that reintroduces it.

---

## 2026-08-05 — Review of FRD-405: seven defects found and fixed
A structured review of the freshly written FRD-405 code and its documentation, run as four
parallel audits (docs-vs-code, correctness, extensibility/readability, test quality) with every
serious finding re-verified by hand before being acted on. It found real defects in work that had
been reported as verified the same day — the verification had covered the paths that were tested,
not the edges.

**The worst one was the opposite of a promised property.** `FR-4` says a member's own burst may
not consume the whole use case. The code took a token from the wide use-case bucket *first* and
only then tested the narrow member bucket, keeping the token when the member was refused.
Measured: use case 5/burst, alice 1/burst — after alice's one allowance and four refusals, bob
got **0** of the remaining 4. One throttled member starved everyone else, which is a denial of
service rather than a rounding error. The decision is now all-or-nothing across every bucket a
request must pass, expressed in the interface (`take()` takes the whole set) rather than as a
rule callers must remember.

**Reservations leaked on several exit paths.** Only `UpstreamError` released, so any other
failure — a malformed upstream body, a database hiccup in the pricing lookup, an outright bug —
left the reservation behind. The streaming path settled instead of releasing on failure, charging
a request-limited budget for a request that produced nothing, and its settlement and audit write
sat after the loop with nothing guarding them, so a client hanging up skipped both: the request
vanished from the log despite having reached the upstream. `BudgetService.hold` now makes the
guarantee structural, and the streaming finish runs in a `finally`.

**`embedContent` was neither rate limited nor budgeted** — the controls sat inside the
generateContent branch, so a caller only had to pick the other verb. The handler now parses per
method and runs one shared gate, which is the actual fix: a control that applies to some verbs and
not others has to be impossible to write by accident.

**Two Redis edge cases.** A failure between two budgets left the first reservation unreachable;
it is now handed back. And a correction that could not reach Redis left the estimate in place for
the rest of the period — my first attempt at a fix was to delete the key, which is nonsense,
because the store holding the stale figure is the store that is unreachable. The real fix is a
lifetime, not a repair: counters expire in five minutes and are rebuilt from Postgres, which costs
nothing since every reservation already reads that figure to seed with.

**And the audit writer dropped rows during shutdown** — `stop()` awaits the worker, and a request
landing in that await queued against a worker already being cancelled.

Documentation: `DEPLOYMENT.md` still listed "No rate limiting" as a known gap and its topic table
omitted `aira.rate-limits`, which fails **silently** — Management writes its outbox, the relay
cannot publish, and a setting appears saved while doing nothing.

Test quality mattered as much as the code. Three integration tests caught `except Exception`
around the guards, which would have counted a database error as "correctly refused". The
config-cache test only exercised manual invalidation, which production never calls. And the
disconnect test I wrote first passed against the *old* structure — going through `TestClient`
buffers the whole body, so it never reached the path it was named after. Each fix was proved by
restoring the defect and watching the test fail.

---

## 2026-08-05 — Rate limiting, atomic budget reservations, and the audit write off the hot path
`FRD-405`, decided in `ADR-0008`. Three defects with one cause: the gateway acted on state it had
already stopped being sure about.

**Nothing limited how fast a caller could consume.** Measured on the running stack, one request
opened six to seven separate database sessions — so a client in a retry loop exhausted the
connection pool, and the first casualties were the *other* use cases. A budget states how much
may be spent, never how fast.

**A budget could be exceeded by a multiple.** `guard` read the period's usage, dispatch ran, then
`record` booked it. Requests in flight were invisible to each other's guard, so twenty concurrent
requests all passed a limit with room for one. Since `FRD-403` that limit is a sum of money, which
made it an accounting defect rather than a cosmetic one.

**The audit write blocked the answer.** `record_request` was awaited before the response
returned, contradicting `CLAUDE.md` line 55 — *persistence must not block the request path*.

Added **Redis** as the shared counter store. The argument was the access pattern, not raw speed:
these counters are high-frequency, tiny, contended and worthless once their window closes, which
is the one shape a row-locking MVCC database handles worst — and pointing the hottest path at
Postgres would have loaded the component that is already the throughput ceiling.

- **Rate limits** per use case and per member: a token bucket, refill-test-take in one Lua script,
  so instances behind a load balancer enforce one limit rather than one each. A bucket rather than
  a fixed window, which permits twice the limit across a boundary and cannot tell a short
  legitimate burst from sustained flooding. Over the limit is a 429 carrying `Retry-After`.
- **Budget reservation**: `guard` reserves an estimate before dispatch, `settle` corrects it to
  the real figure, `release` gives it back when the request failed — otherwise a provider outage
  would look to a use case exactly like having spent its month. Postgres stays authoritative and
  seeds the counter on a miss, so a Redis restart costs the in-flight reservations and never the
  period's accounting.
- **Persistence** moved to a bounded queue drained by a worker. Bounded, because an unbounded one
  only moves the exhaustion from the connection pool to memory; drained on shutdown; and a full
  queue writes inline rather than dropping, since the rows lost under pressure would be exactly
  the ones from the incident someone later has to reconstruct. Also removed a `session.refresh()`
  that re-selected every inserted row for nothing.

**Degradation is decided, not accidental** — a new dependency on the request path must not turn a
cache outage into a product outage. Rate limiting falls back to a per-instance bucket,
deliberately *not* to allowing everything: Redis being down is when infrastructure is already
strained, the worst moment to stop bounding a runaway caller. Budgets fall back to the old
Postgres path — enforcing but racy — because refusing traffic would be an outage and skipping
enforcement would be free money. `/readyz` reports `degraded: true` and still returns 200.

Verified against the live stack: two independent limiter instances allowed 4 of 6 requests against
a burst of 4 rather than 4 each; 25 concurrent guards against a budget with room for one admitted
exactly one; 20 concurrent guards against a 1.00 cost budget with a 0.40 estimate admitted exactly
three; the real gateway answered 429 with `Retry-After` after its burst; and with Redis stopped,
requests kept being served in ~6 ms while `/readyz` reported the degradation and recovered by
itself when Redis came back.

The proof that the race was real is a pair of tests sharing one harness: twenty concurrent
requests pass 20/20 on the old path and 1/20 on the new one. The reservation tests run the real
Lua against `fakeredis` rather than a Python reimplementation — the defect lives exactly in the
gap the script closes.

Counts: 22 integration tests (from 14), 199 frontend tests (from 191), Python coverage 99%.
The e2e specs for the new tab are written but were **not executed** here: the Playwright browser
download is blocked by network policy in this environment.

---

## 2026-08-05 — Inline forms were a staircase
Reported from looking at the running app: the hint under the slug field ("Used in the gateway URL
and in API keys.") pushed that input upwards, so the controls in the row no longer lined up.

`.form-inline` was `align-items: flex-end`. Bottom alignment looks right only while every field
is equally tall — the moment one carries a hint under its input, that field grows and its control
rises. Measured before the fix: the slug input started at y=371 and the name input at y=394, a 23
pixel step. Four of the five inline forms in the app were affected.

The row now aligns at the **top**, every label reserves exactly one line, and children that are
not fields (the submit button, an inline error) skip the label row explicitly. Verified across all
five forms and at a width where the budget form wraps: every control in a row starts within a
pixel of its neighbours.

`expectFormControlsAligned()` in the e2e suite now groups a form's controls by the row they landed
in and fails on a step of more than 2px. Confirmed by putting the old CSS back: it reports
"row 10 is a staircase — uc-name@394, button@397". Neither the unit tests nor a DOM assertion can
see this — jsdom has no layout at all.

**Gates**: 454 unit + 14 integration + 36 e2e + 177 frontend tests green.

---

## 2026-08-05 — Payload storage can be switched off per use case
Follow-on to FRD-404. Retention answers "how long"; this answers "at all". Until now the only
control was the installation-wide `AIRA_STORE_PAYLOADS` env var — not per use case, not in the
database, not in the UI.

- **`UseCase.store_payloads`**, default on, next to the retention period on the use-case
  overview. Off means no prompt or response is written for that use case.
- **The installation setting is a kill switch above it**: a use-case admin may decline storage,
  but cannot re-enable it where the operator forbade it.
- **Switching off purges**: it is treated as a period of zero, so what is already stored goes on
  the next pruner run instead of lingering for the remainder of the old period.
- Requests without a use case follow the installation setting; a use case the gateway has not
  heard of yet keeps the previous behaviour rather than silently dropping the audit payload.

**Verified on the live stack**: with storage off for `demo-uc`, a request containing a personnel
number returned 200, its tokens and cost were recorded — and the number appears **nowhere** in
`request_logs`.

**Two UI bugs the browser tests found, neither visible in jsdom.**

1. A `<label for="x">` that also *wraps* its input makes a real browser forward the click twice:
   the box toggled back instantly and the switch looked dead. jsdom does not reproduce label
   forwarding, so the unit test was green.
2. Then the same race as the pipeline builder: the settings form was interactive while the GET was
   still in flight, so the arriving response reset the switch. It intermittently appeared to work.
   The overview panel now renders only once the use case has loaded, and a unit test asserts it.

A one-way `[ngModel]` on a checkbox inside an `NgForm` also writes the old value straight back;
the switch uses plain `[checked]`/`(change)` instead.

**Gates**: 454 unit + 14 integration + 33 e2e + 177 frontend tests green.

---

## 2026-08-05 — FRD-404: stored prompts now expire, per use case, a week by default
The least defensible property this product had: FRD-103 stored every request and response body,
`store_payloads` is on by default, the redaction hook is a no-op — and **nothing ever deleted
them**. Prompts routinely contain personal data.

- **`UseCase.retention_days`**, 1–3650, **default 7**, editable by a use-case admin and shown on
  the use-case overview so it is visible to whoever is accountable rather than buried in config.
  Distributed with the existing `usecase.upserted` event.
- **`python -m aira_gateway.retention`** (`make prune`) applies it; the reference stack runs it
  hourly in a container. It is a one-shot process like the relay: **if nothing schedules it,
  nothing is deleted** and the period in the UI is a promise nobody keeps.
- Requests with no use case follow `AIRA_DEFAULT_RETENTION_DAYS` (7) — not exempt just because
  nobody claimed them.

**Two clocks, deliberately.** Payload retention (per use case, 7 days) removes the request and
response bodies; the row and its metadata stay. A seven-day *row* retention was the obvious first
design and is wrong: `request_logs` is where per-request cost lives (FRD-403), so it would leave
the spend reporting able to see one week and no further. Whole-row deletion is a separate,
installation-wide switch (`AIRA_LOG_RETENTION_DAYS`), **off by default** because the reporting
horizon is an organisational decision, not something a release makes silently.

**A bug the idempotency test caught.** SQLAlchemy's `JSON` type writes `None` as the JSON value
`null`, not SQL `NULL`. "Has no payload" and "has a payload that is null" were therefore
indistinguishable, so the pruner rewrote the same rows on every run and its reported count meant
nothing. Columns are now `JSON(none_as_null=True)`; migration 0008 normalises existing rows and
adds the `(use_case, created_at)` index the scan needs.

**Verified on the live stack**: rows aged 10 and 2 days on a use case with the default period →
the older payload gone, the fresher kept, both rows retaining tokens and cost; a second run
cleared nothing; lowering the period to 1 day then removed the second payload too.

**Still open, and stated in the FRD**: content redaction. Retention decides *when* a payload
goes; nothing yet masks sensitive values *inside* one while it is kept.

**Gates**: 448 unit + 14 integration + 31 e2e + 171 frontend tests green.

---

## 2026-08-05 — CI: the gates are enforced, not merely available
`.github/workflows/ci.yml`. Until now every quality gate — three test layers, two coverage
thresholds, ruff, mypy, Prettier — only ran when somebody remembered, while `CLAUDE.md` claimed
CI enforced them. It does now.

- **Three jobs**: Python (lint, format, mypy, unit tests with the coverage gate); frontend
  (Prettier, a **production** build, unit tests with their thresholds); and the stack, which
  builds the three images, runs `make up-full`, waits for health, then runs the integration
  suite and the Playwright end-to-end suite against it. On failure it uploads the Playwright
  report and dumps the container logs.
- **The workflow is deliberately thin**: every step calls the same `make` target a developer
  runs locally, so CI and a local run cannot drift, and switching CI systems is a rewrite of one
  file. `make ci` reproduces the hermetic half; `make wait-healthy` is the readiness gate, useful
  by hand too.
- **The frontend job builds for production on purpose** — that is where the CSP silently disabled
  the entire stylesheet; a development build would not have caught it.

**Verified by breaking it on purpose**: a stray import, a wrong return type, and a deliberately
miscalculated cost each made `make ci` exit non-zero; the clean tree exits 0. A gate that cannot
fail is theatre.

Also fixed on the way: **Node was never pinned**, although ADR-0003 requires it — added
`.nvmrc` (26) and an `engines` block, which CI now reads instead of hardcoding a version.

**Caveat**: the workflow runs on push, but a green run only *blocks* a merge if branch protection
requires it, and this repository has no remote configured yet — the workflow starts working the
moment it is pushed to GitHub.

---

## 2026-08-05 — FRD-403: budgets in money, not tokens
Driven by the observation that budgeting in tokens is not cost control. A token differs in price
by more than an order of magnitude between models, and every provider bills **output** tokens
several times higher than input — so even a known price cannot be applied to a single
`total_tokens` figure.

- **Model catalog with prices** (the price half of FRD-307): per model, the cost of 1M input and
  1M output tokens, maintained by a **Global Administrator** only — a price follows the provider
  contract, not a use case. Distributed over the new compacted topic `aira.models`.
- **`Budget.limit_cost`** alongside the existing token/request caps, enforced pre-dispatch with
  the same `429 RESOURCE_EXHAUSTED`.
- **Per-request cost in `request_logs`**, so spend can be *reported*, not only capped.
- **UI**: the budget tab leads with a spend limit and a spend bar; a new **Models & prices**
  screen (read-only unless global-admin).

**Two decisions worth recording.**

*Money is an integer, never a float.* Amounts are nano-units (10⁻⁹ of the currency) in `BIGINT`,
via the new `aira_common.money`. Floating point cannot represent 0.1 exactly and a spend figure
is the sum of millions of small charges; `NUMERIC` would be exact on Postgres but SQLite — which
the tests run on — stores it as a float, so the tests would not exercise production behaviour.
Amounts therefore also cross API boundaries as decimal **strings**, never JSON numbers.

*Unknown is not zero.* A request on an unpriced model did cost money; AIRA just cannot say how
much. Booking it as `0` would make the spend figure silently too low — the worst failure mode for
a number somebody is accountable for. It is counted under `unpriced_requests`, excluded from the
cost total, does not consume the cost budget, and is named in the UI. In the same spirit, the
display never renders a non-zero amount as `0.00`; it widens its precision until it is truthful.

Also fixed along the way: adding a positional `cost_nanos` to `BudgetService.record` made the
existing callers pass their *timestamp* as an amount. Both extra arguments are keyword-only now —
an amount of money and a timestamp side by side as positionals is exactly how a wrong figure gets
booked with nothing failing.

**Verified on the live stack**: `mock-1` priced at 1.00/10.00 per 1M; three requests of 5 input +
8 output tokens each priced at exactly 85 000 nanos, accumulating to exactly 255 000 — no drift;
lowering the limit below that produced `429 Cost budget exhausted`.

**Gates**: 429 unit + 12 integration + 28 e2e + 165 frontend tests green; ruff, mypy and both
coverage gates unchanged.

---

## 2026-08-05 — Containerised: `make up-full` brings the whole system up
Three images (`gateway/Dockerfile`, `management/backend/Dockerfile`,
`management/frontend/Dockerfile`) and a compose overlay
(`deploy/compose/docker-compose.apps.yml`). A cold start — volumes removed — reaches a working
demo in **42 seconds**, with all 23 e2e tests green against it.

- **One image per component, several roles each.** The gateway image also runs the config
  consumer and `alembic upgrade head`; the management image also runs `manage.py migrate`, the
  outbox relay and (in the `demo` profile) `seed_demo`. Both are multi-stage, ship only the
  resolved virtualenv, run as uid 10001 and carry a healthcheck. The SPA is served by nginx,
  which takes over the `/api` and `/gw` proxying the dev server does in development.
- **Ordering is expressed, not slept on**: migrations and topic creation run to completion before
  the services that need them (`service_completed_successfully`).
- **The relay runs as a loop container** (`AIRA_RELAY_INTERVAL`, default 10s), so configuration
  propagates without anyone remembering `make relay`.

**Three defects the containers exposed**, none of which could show up in the dev setup:

1. **The CSP broke the production stylesheet.** Angular's build defers the global stylesheet with
   `<link media="print" onload="this.media='all'">`; that inline handler is script, and the CSP
   added in ADR-0007 allows scripts from `'self'` only — so it never ran and **the entire design
   system was missing from any production build**. The dev server injects styles differently, so
   everything looked right locally. Fixed by disabling `inlineCritical`; a new e2e test asserts
   no stylesheet is left deferred and that `.card` actually paints.
2. **`aira_common` did not declare PyJWT.** `aira_common.oidc` imports `jwt`, but the dependency
   was declared on *aira-gateway*. The shared dev virtualenv hid it; the isolated management
   image failed to import at startup. Declared where the import is.
3. **`up-full` produced an empty demo.** The Keycloak realm has the five accounts, but their
   Django counterparts only appear on first login, so "add member ucuser" had nobody to add.
   Seeding now runs in a `demo` profile — a real deployment omits it, and `seed_demo` refuses
   outside local/demo mode anyway.

Also: management had **no production-capable server** — only Django's `runserver`, which Django
itself excludes for production. `uvicorn` is now a declared dependency and serves the ASGI app.

Docs: `docs/DEPLOYMENT.md` §1/§2 rewritten around the images, "no container images" removed from
the gaps table, README and the compose README updated.

---

## 2026-08-05 — Deployment documentation (`docs/DEPLOYMENT.md`)
Written from the code, not from intent — every variable and command in it was read out of the
settings classes and the Makefile, and the setup sequence was re-run against the live stack.

- **New `docs/DEPLOYMENT.md`**: what actually runs (five processes, not two), the standalone
  quickstart, integration with an existing Postgres / Keycloak / Kafka / OTel collector / upstream
  provider / reverse proxy, a complete reference of all **28 gateway and 21 management settings**,
  what has to be prepared in Keycloak (client, the five realm roles, the groups mapper, the
  `/use-cases/<slug>` groups), and a production checklist.
- **Root README** no longer claims "Planning phase" — Phases 0–4 are delivered. It now carries a
  quickstart that matches reality and links the deployment guide.
- **`deploy/compose/README.md`** corrected: it still advertised SigNoz (superseded by ADR-0004),
  described the realm directory as "empty until Phase 2", and did not mention that topic
  auto-creation is off.
- **`make help` was hiding targets**: its grep had no digits in the target-name character class,
  so `test-e2e` never appeared. Fixed, and `e2e` demoted to a plain alias.

**Stated plainly in §7 rather than glossed over**: there are no container images for the two
services (Compose is infrastructure only); Vault and the Schema Registry run in the reference
stack but **no code reads from them**; the SPA's issuer and client id are compiled in, so
retargeting it needs an edit and a rebuild; Kafka has no auth/TLS settings; the relay is a
one-shot command that must be scheduled or configuration never propagates; `request_logs` has no
retention; there is still no CI.

---

## 2026-08-05 — Verified against the live stack: e2e (Playwright) + integration tests
Point 1 of the plan: stop trusting the hermetic suites and actually run the thing. Three defects
surfaced that no unit test could have caught — two of them in the security pass itself.

**What the run found**
- **The hardened Keycloak realm broke Keycloak's boot.** The client `description` added in
  ADR-0007 was 259 characters; the `CLIENT.DESCRIPTION` column is `varchar(255)`, so the import
  aborted and the container refused to start. Also: `--import-realm` skips existing realms, so a
  running stack silently kept the old wildcard `redirectUris`/`webOrigins` — the hardening was
  never actually applied. Both now documented in `deploy/compose/README.md`.
- **The dev realm had none of the five AIRA roles** and one user with no roles. Keycloak is the
  source of truth for roles (FRD-201), so the documented demo acceptance in FRD-203 §5 could not
  pass: you could log in and do nothing. The realm now carries the roles and one user per role,
  usernames matching the Django seed so a login adopts the seeded account.
- **The pipeline builder discarded early edits.** Adding a step before the config GET resolved
  let the response clobber it: the graph stayed empty while the header claimed "Unsaved changes".
  The builder is now rendered only after the config has arrived; unit-tested as a regression.
- `make kafka-topics` really did need its fix — the three previously missing topics were created
  on this run.

**Consequence of ADR-0007 that was not thought through**: the gateway authorizes `usage` by
Keycloak group membership, but use cases are administered in Management, so a use case created in
the SPA has no group and its consumption stays hidden. The strict check is kept (it matches how
the data plane authorizes); the UI now distinguishes "refused" from "unreachable" and names the
missing group. Proper fix recorded as a follow-up in the ADR addendum.

**New test layers**
- `e2e/` — Playwright, **22 tests**, real Chrome: the Keycloak code flow incl. PKCE, role-aware
  nav, layout at 360/768/1280/1920 px (measured as `scrollWidth <= clientWidth`, which jsdom
  structurally cannot do), the sticky-inspector reachability fix, key issue/reveal-once/revoke
  with confirmation, the ADR-0007 rule that a governance role cannot mint a key, and the
  gateway dry-run driven with the browser's own token.
- `tests/integration/` — **12 tests** against the live stack, moved out of `gateway/tests/` into
  a top-level folder: the gateway's HTTP contract (401s, 413 body ceiling, `/readyz` not naming
  hosts, API-key auth + `request_logs` attribution over real HTTP) and the full config
  distribution round trip management outbox → relay → Kafka → consumer → gateway read-model.
- `make test-e2e` and `make run-gateway-oidc` added; `make test-integration` unchanged.

**Gates**: 392 unit + 12 integration + 22 e2e + 139 frontend unit tests, all green; ruff, mypy
and the coverage gates unchanged.

---

## 2026-08-05 — Management UI: usability, layout, and a frontend coverage gate
No new screens — a pass over the existing SPA for the things that made it feel unfinished.

- **Two silent-failure bugs (zoneless).** The app runs without zone.js, so a plain component
  property changed from *code* schedules no re-render. Clearing the create-use-case form and the
  member/key/budget forms from their success callbacks therefore left the submitted text sitting
  in the inputs, and switching a budget to member scope did not reveal the username field. All
  form state moved to **signals** with explicit `[ngModel]`/`(ngModelChange)` binding; regression
  tests assert the DOM, not just the model.
- **Nothing failed silently any more.** Every load and every mutation now reports its outcome:
  a new `errorMessage()` helper unwraps the shared `{"error": {...}}` envelope (including DRF's
  per-field `details`) so the server's own wording is shown. This mattered most right after the
  ADR-0007 pass: a use-case viewer clicking "Issue key" got a 403 and *no feedback at all* — the
  button looked broken. Loading states replace the misleading "No use cases yet." shown while the
  request was still open, and forms stay open (keeping input) when the server rejects them.
- **Width overflow.** Wide tables now scroll inside their card (`.table-wrap`) instead of dragging
  the page sideways; `min-width: 0` on flex items stops one long name from widening the layout;
  long identifiers break; header username truncates; nav and tab strips scroll on a phone; inputs
  no longer overflow narrow columns; forms stack below 640px.
- **The builder.** The sticky inspector had no height cap — taller than the viewport, it pinned
  its top and left the lower fields (e.g. "Default model" with several categories) permanently
  unreachable. It now caps and scrolls, and is only sticky where there is a second column. On
  ≥1200px screens the builder breaks out of the 960px reading column.
- **Destructive actions ask first** (remove member, revoke key, delete budget, delete step) via a
  stub-able `ConfirmService`; invalid submits are disabled with the reason shown inline instead of
  doing nothing; the clipboard fallback explains itself when the browser blocks the write.
- **Accessibility**: tablist/tab/tabpanel semantics with `aria-selected`, a label for every
  control, accessible names on icon-only buttons, `aria-expanded` on disclosures, progressbar
  roles on the budget bars, Space as well as Enter on graph nodes, visible focus rings.
- **Tabs are deep-linkable** (`?tab=keys`) and survive a reload.
- **Coverage gate**: frontend coverage went **53.8% → 92.3% statements (95.6% lines)** across
  30 → **134** tests, and `angular.json` now enforces 90/92/93/75 — verified to fail when unmet.

---

## 2026-08-04 — ADR-0007: security hardening pass (gateway, management, frontend)
Full-codebase security review with no new features — see **ADR-0007** for the findings, the
options weighed, and the trade-offs.

- **Closed authorization gaps.** `POST /v1beta/pipeline:dryRun` and `GET /v1beta/usage/{use_case}`
  now require an authenticated principal (usage additionally authorizes the use case) — the
  dry-run runs real LLM steps with caller-supplied prompts, so open access was a free relay to
  the upstream. Issuing an API key now requires **membership**, not mere visibility: the
  oversight roles see every use case and could previously mint a data-plane key for any of them.
  Django users are bound to the Keycloak `sub` (`OidcIdentity`, api migration 0001, trust-on-
  first-use for existing accounts) so a re-issued username cannot inherit someone's permissions.
- **Safe defaults.** Management refuses to boot outside `local` with the dev `SECRET_KEY`,
  wildcard `ALLOWED_HOSTS`, or `DEBUG`; security headers on by default. `X-Forwarded-For` is
  only honoured behind a declared proxy (`AIRA_TRUST_FORWARDED_FOR`) — the audit trail was
  client-forgeable. `?key=` and friends are redacted before the query string reaches a span.
  Revocation is terminal in the gateway read-model (a replayed `created` no longer resurrects a
  revoked key). `seed_demo` only runs locally / in demo mode.
- **Input bounds.** Request-body ceiling (`AIRA_MAX_REQUEST_BYTES`, 8 MiB, enforced before
  buffering, with or without `Content-Length`); use-case selector validated against the slug
  charset; pipeline configs bounded and **nested-quantifier regexes rejected** at authoring time,
  with independent execution bounds in the gateway and the browser preview (ReDoS on the hot path).
- **Frontend.** `requireHttps: 'remoteOnly'` + strict discovery validation + PKCE explicit,
  bearer token scoped to first-party prefixes only, every user-supplied URL segment encoded,
  CSP shipped in `index.html` (scripts same-origin), bounded live preview.
- **Infra.** Keycloak dev realm: redirect URIs / web origins pinned to the dev hosts (were `*`
  on a public client) and the password grant disabled. `make kafka-topics` now creates all five
  compacted topics (three were missing, so api-key/pipeline/budget distribution silently failed
  on a fresh stack with auto-create off).
- **Operational note:** the SPA's dry-run and consumption views now need a token the gateway
  accepts — enable `AIRA_OIDC_ENABLED`/`AIRA_OIDC_ISSUER` (see `.env.example`). Without it both
  degrade gracefully; nothing else changes.
- **Gates green**: backend 389 tests / 99.83% coverage, ruff + mypy clean; frontend 30 Vitest
  tests, Prettier clean, `ng build` OK.

---

## 2026-08-04 — FRD-402: budget UI (closes Phase 4)
- Gateway `BudgetService.usage()` + unauthenticated `GET /v1beta/usage/{use_case}` return
  current-period consumption per budget (used tokens/requests).
- Angular use-case detail gains a **Budgets tab**: set use-case / per-member budgets (scope, period,
  token/request limits) and **see consumption** as progress bars (warn ≥80%, full ≥100%); admins
  edit, members read. Consumption fetched from the gateway via `/gw`; limits from Management.
- **Gates green**: backend 328 tests / 99.85%; frontend 26 Vitest tests, Prettier clean, `ng build` OK.
- **Phase 4 (Budgets & Quotas) complete.**

---

## 2026-08-04 — FRD-401: budget enforcement + usage accounting
- Gateway `BudgetService`: **pre-dispatch `guard`** loads the budgets applicable to the request's
  use case + subject, checks the current period's usage, and **rejects with `429 RESOURCE_EXHAUSTED`**
  when a limit is met; **post-dispatch `record`** increments the counters (generate + streaming).
- Usage accounting table `budget_usage` keyed by `(scope_key, period_key)` — `uc:<slug>` /
  `member:<slug>:<subject>` × `YYYY-MM` | `YYYY-MM-DD` — so it **resets at day/month boundaries**
  (gateway migration 0006). Request-count limits block the request itself; token limits block once
  exceeded. `enforce_budgets` toggle (default on); no budgets configured → zero overhead.
- **Gates green**: 326 tests / 99.85% (budget service + routes 100%), ruff + mypy --strict clean.
  Next: `FRD-402` budget UI.

---

## 2026-08-04 — FRD-400: budget model + distribution (Phase 4 start)
- New Management `budgets` app: `Budget` per use case — `scope` (use_case | member), `period`
  (day | month), `limit_tokens` and/or `limit_requests`, `enabled`; unique on
  (use_case, scope, subject, period). Nested endpoints `GET/POST /use-cases/{slug}/budgets` (POST
  upserts) + `DELETE …/budgets/{id}` (members read, admins write); validation (member needs subject;
  at least one positive limit).
- Distribution: `budget.upserted` / `budget.deleted` via the transactional outbox → Kafka
  `aira.budgets` → gateway idempotent consumer → `budgets` read-model (gateway migration 0005).
- Enforcement + usage accounting is FRD-401; UI is FRD-402 (both planned).
- **Gates green**: 314 tests / 99.85% (budget modules + views 100%), ruff + mypy --strict clean.

---

## 2026-08-04 — FRD-306: pipeline rework — LLM routing, explainable filter, dry-run
- Reworked the pipeline after feedback that routing was length-only and the builder was opaque.
- **Routing** is now an **LLM classifier**: it reads system + user text, picks one of the configured
  `categories` (`{name, description, model}`) and routes to that model (`default_model` fallback).
- **Injection filter**: built-in patterns are **shown**; operators add **custom patterns** (invalid
  regex → literal); `use_builtins` toggle; `scope` user | system+user; LLM mode takes model +
  instruction.
- **Transparency**: `engine.dry_run()` + `POST /v1beta/pipeline:dryRun` return a full per-step trace;
  the builder gains a **test panel** with an instant **live preview** (deterministic steps,
  client-side) and a **Dry-run** button (full trace incl. LLM via gateway, `/gw` dev proxy).
- Inspector redesigned with inline help per step + a categories editor.
- **Gates green**: backend 299 tests / 99.8% (pipeline modules ~100%), ruff + mypy --strict clean;
  frontend 23 Vitest tests, Prettier clean, `ng build` OK. `FRD-306` done.

---

## 2026-08-04 — UI usability: tabbed use-case detail
- The use-case detail page was overloaded with stacked lists (members + keys + forms). Split into
  **tabs** (Overview / Members / API keys) so one section shows at a time; add/issue forms moved
  behind **disclosure** toggles; Overview shows quick **stat tiles**. Added `.tabs`/`.tile`/
  `.disclosure` to the design-system. `ng build` + 21 Vitest + Prettier green.

---

## 2026-08-04 — FRD-300/303: pre-dispatch pipeline (filter · routing · fallback) + graph builder
- **Gateway engine** (`aira_gateway/pipeline/`): per-use-case, config-driven pipeline runs before
  dispatch on the canonical request. Steps: `injection_filter` (heuristic **or LLM-backed**, fails
  open; action block|flag), `allow_check` (model allow-list), `model_route` (rule-based incl.
  cost/length rerouting). Dispatch follows a `fallback_models` chain. Default (no config) =
  pass-through, so prior behavior is unchanged. Decisions logged + traced (`aira.pipeline.*`).
- **Distribution**: `aira.pipelines` topic; idempotent consumer → `pipeline_configs` read-model
  (gateway migration 0004). Management `pipelines` app + `GET/PUT /use-cases/{slug}/pipeline`
  (members read, admins edit) publishes `pipeline.upserted` via the outbox.
- **Angular graph builder** (`features/pipelines`): route `use-cases/:slug/pipeline` renders the
  pipeline as a **clickable node graph** (Request → steps → Dispatch → fallback) with a per-step
  inspector; zoneless-safe signal state. Entry from the use-case detail.
- **Gates green**: backend 285 tests / 99.8% (pipeline modules ~100%), ruff + mypy --strict clean;
  frontend 21 Vitest tests, Prettier clean, `ng build` OK. `FRD-300`/`FRD-303` done. **Phase 3 core
  (pipeline) delivered.**

---

## 2026-08-04 — FRD-205: self-service API-key issuance + UI redesign (closes Phase 2)
- **Backend (Management → Gateway)**: Management is now the source of truth for API keys
  (ADR-0006). New `apikeys` app (model + serializers) with nested endpoints on the use-case
  viewset: `POST/GET/DELETE /api/v1/use-cases/{slug}/api-keys[/{prefix}]`. A member issues a key
  **bound to the use case**, plaintext returned **once**, only the hash stored. `api_key.created`/
  `api_key.revoked` flow through the transactional outbox to a new `aira.api-keys` compacted topic.
- **Gateway**: idempotent consumer upserts/deactivates the `api_keys` read-model; `ApiKey` gains
  `use_case` (migration 0003); a verified api_key `Principal` carries its bound use case, so
  requests need **no `/uc` selector** and a mismatched selector is rejected (403). Shared key
  format/hash extracted to `aira_common.apikeys`. CLI stays as break-glass.
- **Frontend**: use-case detail gains an **API-keys panel** (issue with one-time reveal + copy,
  list masked, revoke) and a members table. Typed `UseCaseService` methods + tests.
- **UI redesign**: global design-system (`styles.scss`) — tokens, cards, buttons, tables, badges,
  callouts; polished app shell (brand header, active-state nav, constrained content). Templates
  restyled with the shared classes.
- **Gates green**: backend 241 tests / 99.95%, ruff + mypy --strict clean; frontend 16 Vitest tests,
  Prettier clean, `ng build` OK.
- **Phase 2 (Management Foundation) is complete.**

---

## 2026-08-04 — Upstream status passthrough (gateway hardening)
- `UpstreamError` now carries the upstream HTTP `status_code` (`None` for transport failures).
- Gemini routes map it: **429 → `429 RESOURCE_EXHAUSTED`**, **503 → `503 UNAVAILABLE`**,
  **504 → `504 DEADLINE_EXCEEDED`**; everything else (upstream 4xx from *our* key/config, upstream
  5xx, transport errors) is masked as a generic **502 UNAVAILABLE** so a broken upstream is never
  mistaken for a client error. Streaming still logs + terminates cleanly (status already sent),
  now including the upstream status.
- Rationale: a client (e.g. opencode) hitting a real Gemini rate-limit should see `429` and back
  off, not a misleading `502`.
- **Gates green**: 225 tests / 99.9% (routes + gemini modules 100%), ruff + mypy --strict clean.

---

## 2026-08-04 — FRD-304: real Google Gemini upstream adapter (Phase 3)
- **Async provider protocol**: `Upstream` (`upstreams/base.py`) is now `async` (`generate`/`embed`
  coroutines, `stream_generate` async-iterator); added `UpstreamError` for upstream failures.
  `MockProvider` updated accordingly.
- **`GeminiUpstream`** (`upstreams/gemini.py`): calls the Generative Language API
  (`generativelanguage.googleapis.com/v1beta`) with an **injectable `httpx.AsyncClient`** so tests
  drive it via `MockTransport` — fully hermetic. API key sent as `?key=` query param, **never
  logged**. Non-2xx / transport errors → `UpstreamError`. `build_gemini_upstream(settings)` returns
  `None` when no key is set; the app registers `[MockProvider(), *gemini]`.
- **Pure mappers** (`upstreams/gemini_mapping.py`): canonical ⇄ Gemini request/response/stream-chunk,
  incl. `systemInstruction`, `generationConfig`, `usageMetadata`, `finishReason` normalisation.
- **Routes**: `generateContent`/`embedContent` return **502 `UNAVAILABLE`** on `UpstreamError`;
  streaming logs the error server-side and terminates the stream cleanly (headers already sent).
- **Config**: `GOOGLE_API_KEY`, `GEMINI_MODELS` (`gemini-2.0-flash,gemini-1.5-flash`),
  `GEMINI_BASE_URL`. `httpx` promoted to a gateway runtime dependency.
- **Gates green**: **222 tests / 99.9%** (new `gemini` modules 100%), ruff + `mypy --strict` clean.
- Enables binding **opencode** (Google provider + custom baseURL) to a use-case with real responses.
- See `docs/features/FRD-304-upstream-adapters.md`.

---

## 2026-08-04 — FRD-203: Angular management shell
- **Auth** (`core/auth`): `angular-oauth2-oidc` code-flow+PKCE against the `aira` realm; `AuthService`
  facade; functional `authInterceptor` (bearer on `/api` calls) + `authGuard` (redirect to login);
  `provideAppInitializer` runs OIDC discovery on startup.
- **API** (`core/api`): typed `MeService` + `UseCaseService` (list/get/create/update/remove +
  members) with models.
- **Shell**: header + **role-aware navigation** (Security/Governance/Administration shown by role
  from `/api/v1/me`), logout.
- **Screens** (lazy-loaded): use-case **list** (+ create form) and **detail** (edit context, member
  add/remove) wired to FRD-202 endpoints.
- **Dev proxy** (`proxy.conf.json`): `/api` → management `:8002`; `make run-frontend` uses it.
- **Gates green**: `ng build` OK (lazy chunks), **13 Vitest tests** pass (interceptor/guard/service/
  list/shell, browserless), Prettier clean. Python side unchanged (202 tests / 100%).
- **Next: FRD-205** (self-service API-key issuance) closes Phase 2.

---

## 2026-08-04 — FRD-204: config distribution over Kafka (Management → Gateway read-model)
- **Transactional outbox** (management `outbox` app): use-case/membership change events are written to
  an `OutboxEvent` row **inside the same transaction** as the change (mutations wrapped in
  `transaction.atomic`; subscriber wired via `events.subscribe` in app-ready). A `relay` command
  publishes pending rows to Kafka and marks them — at-least-once (crash-safe; consumer idempotent).
- **Shared Kafka** (`aira_common.kafka`): `Producer` protocol + `InMemoryProducer` (tests) +
  `AiokafkaProducer` (real; `# pragma: no cover` I/O); topics `aira.usecases`/`aira.memberships`;
  W3C trace context on headers.
- **Gateway consumer**: `apply_event` (idempotent upsert/delete) into read-model tables `use_cases`
  + `use_case_members` (Alembic 0002); `worker` (aiokafka) + `decode_event_type`. `make kafka-topics`
  creates compacted topics; `make relay` / `make consume`.
- **Gates green**: 202 tests, **100% coverage** (pure logic; Kafka I/O pragma-excluded, integration-
  tested); ruff + mypy --strict clean (aiokafka untyped import ignored).
- **End-to-end verified**: created `kafka-uc` in management → outbox rows → `relay` published to Kafka
  → gateway consumer applied → read-model shows `use_cases: kafka-uc` and `use_case_members:
  kafka-uc/demo-user/admin`. Failed publish (missing topic) left rows pending (nothing lost).
- **Next: FRD-205** (self-service API-key issuance, distributed via this backbone) or **FRD-203** (UI).

---

## 2026-08-04 — FRD-202: use-case CRUD + membership
- **`usecases` app**: `UseCase` (slug/name/description/processing_notes) + `UseCaseMembership`
  (unique per user). CRUD at `/api/v1/use-cases/` (DRF ModelViewSet); slug validated to the gateway
  selector charset (`[a-z0-9-]`).
- **RBAC applied** (FRD-201): list is scoped (governance sees all, others see permitted); create needs
  the use-case-admin/global-admin role and makes the creator the use-case admin; edit/delete needs
  `change_usecase` (or global-admin); membership needs `manage_members` (or global-admin). Adding a
  member grants **`django-guardian`** object perms (view; +change/manage for admins).
- **Membership actions**: `POST/GET /use-cases/{slug}/members/`, `DELETE …/members/{username}`.
- **Change hook** (`events.emit`): in-process subscribers on usecase/membership changes — the Kafka
  publisher subscribes here in FRD-204. Migrations excluded from coverage.
- **Gates green**: 190 tests, **100% coverage**; ruff + mypy --strict clean (DRF generics typed).
- **End-to-end verified**: as global-admin `demo-user` created `live-uc`, listed it, added a member,
  and an invalid slug → 400.
- **Next: FRD-203** (Angular shell) or **FRD-204** (Kafka distribution).

---

## 2026-08-04 — FRD-201: RBAC (roles + object-level use-case perms)
- **`aira_management.rbac`**: `sync_user_roles` maps a token's realm roles onto Django groups (the
  five AIRA roles) on every auth — Keycloak is the source of truth. DRF permission classes
  (`IsGlobalAdmin`, `IsITSecurity`, `IsITSteuerung`, `IsUseCaseAdmin`, `IsUseCaseUser`; global-admin
  implies all). `scope_queryset` narrows lists: governance roles (global-admin, it-steuerung) see all;
  others are limited to their **`django-guardian`** object-level permissions.
- **Wiring**: `django-guardian` added (INSTALLED_APPS + object-perm backend; `ANONYMOUS_USER_NAME=None`).
  The auth class calls `sync_user_roles` after provisioning.
- **Gates green**: 174 tests, **100% coverage**; ruff + mypy --strict clean (guardian import ignored).
- **End-to-end verified**: assigned realm role `global-admin` to `demo-user` in Keycloak → token
  carries it → `/api/v1/me` shows `roles:[global-admin]` and the Django group membership is synced
  (`demo-user | global-admin` in `aira_mgmt`).
- **Next: FRD-202** (use-case CRUD + membership, using these RBAC mechanics).

---

## 2026-08-04 — Phase 2 begins · FRD-200: management DRF API + OIDC
- **Shared OIDC** (`aira_common.oidc.JwtVerifier` + `build_jwks_client`): extracted JWT/JWKS
  verification so the gateway **and** management use one implementation. Gateway `OidcValidator`
  refactored to wrap it (behaviour unchanged, tests green).
- **Management DRF foundation**: `api` app with `KeycloakJWTAuthentication` (verifies the bearer JWT,
  auto-provisions a Django user from claims, attaches claims as `request.auth`), a consistent DRF
  **error envelope** (`{"error":{code,message,details}}`), and `GET /api/v1/me` (subject, username,
  email, realm roles, use-case groups). `IsAuthenticated` default; 401 via `authenticate_header`.
- **Gates green**: 167 tests, **100% coverage**; ruff + mypy --strict clean. Hermetic tests use a
  self-signed RS256 + fake JWKS (no Keycloak needed).
- **End-to-end verified**: management backend with `AIRA_OIDC_ISSUER=…/realms/aira` — no token → 401;
  a real Keycloak `demo-user` token → 200 `me` with username/email/groups; user auto-provisioned.
- **Next: FRD-201** (RBAC: realm roles → Django groups + `django-guardian` object-level use-case perms).

---

## 2026-08-04 — Quality: error-safety + test-tier separation (Jenkins-ready)
- **Confirmed the pytest suite is hermetic**: 154→156 tests pass with the **entire Compose stack
  stopped** (in-memory SQLite, fake JWKS, mock provider). The earlier curl checks were *manual*, not
  part of the suite.
- **Two test tiers** for CI: unit tests run by default; stack-dependent tests are marked
  `@pytest.mark.integration` and **excluded** (`-m 'not integration'`). Added `make test-integration`
  and an example integration test; documented in **`docs/TESTING.md`** with a Jenkins pipeline sketch
  (unit stage needs no Docker; integration stage brings the stack up).
- **Error-safety**: added a global exception handler — any unhandled error now returns a
  **Gemini-shaped 500 (`INTERNAL`)** on `/v1beta` (AIRA envelope elsewhere), logs full context
  server-side (path, method, error type/msg, subject, use_case, trace_id), and **does not leak**
  internal details to the client. Tested with a throwing provider.
- **Reviewed**: expected errors already carry contextual messages (model-not-found, missing-method,
  not-a-member-of-use-case, field-located validation errors, unauthenticated). Noted follow-up: OIDC
  fails closed (401) even when Keycloak/JWKS is unreachable — safe, but can't cleanly distinguish
  "provider down" (503) from "bad token" via PyJWT alone.
- **Gates green**: 156 tests, **100% coverage**; ruff + mypy --strict clean.

---

## 2026-08-04 — FRD-104 + FRD-105 — **Phase 1 (Gateway MVP) complete**
- **FRD-104 (mock fidelity + streaming)**: `:streamGenerateContent?alt=sse` now returns
  `text/event-stream` (`data: {json}\n\n`, the google-genai SDK path); the default returns a streamed
  **JSON array** (Gemini REST form). Mock honours `generationConfig.maxOutputTokens` → truncates and
  reports `finishReason=MAX_TOKENS`.
- **FRD-105 (tracing enrichment)**: `aira_common.set_span_attributes(mapping)` sets non-None
  attributes on the current span. `require_attribution` tags `aira.subject/use_case/auth_method`;
  `record_request` tags `aira.model/operation/status/source_ip/total_tokens`.
- **Gates green**: 154 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: SSE (`text/event-stream`) + JSON-array streaming + `maxOutputTokens`→
  `MAX_TOKENS`; a trace is **searchable in Tempo by `aira.use_case=demo-uc`** (filter traces by use
  case in Grafana).
- **Phase 1 complete**: FRD-100 (Gemini API) · 101 (auth) · 102 (attribution) · 103 (persistence) ·
  104 (mock/streaming) · 105 (tracing). Every request is authenticated → attributed to a use case →
  authorized → dispatched → persisted → traced. **Next: Phase 2 (Management foundation).**

---

## 2026-08-04 — FRD-103: request/response persistence + Alembic
- **`request_logs`** table + `RequestLogService`: persist each dispatched request/response with
  attribution (subject, auth_method, use_case), source IP, model, operation, token usage, status,
  latency, and **trace_id** (correlates to Grafana). Wired into generate/embed/stream routes via
  `record_request`.
- **Source IP** from first `X-Forwarded-For` hop else socket peer. **Redaction hook**
  (`Redactor`/`NoOpRedactor`) + `store_payloads` toggle (metadata-only when off).
- **Alembic** introduced for the gateway DB (`migrations/`, async env, `0001_initial` = api_keys +
  request_logs); `make migrate-gateway`. Dev/tests keep `create_all` (SQLite/bootstrap).
- **Gates green**: 149 tests, **100% coverage**; ruff + mypy --strict clean. Route persistence tested
  via httpx ASGITransport (hermetic SQLite).
- **End-to-end verified**: alembic migrated Postgres; a `:generateContent` call wrote a `request_logs`
  row with subject=demo, use_case=demo-uc, source_ip=203.0.113.7 (XFF), tokens 3/6/9, trace_id set,
  payloads stored.
- **Next: FRD-104** (mock upstream full fidelity) / **FRD-105** (tracing spans + IP on the span).

---

## 2026-08-04 — FRD-102: attribution & use-case selection (OIDC)
- **Problem addressed**: an OIDC token authenticates the *identity*, not *which use case* — a user
  can be in several. Solution: explicit per-request use-case **selector** + membership authorization
  from Keycloak **groups** (no Management DB/Kafka needed yet).
- **Selector**: `/uc/<use-case>/v1beta/...` path (via `UseCasePathMiddleware`) **or**
  `X-AIRA-Use-Case` header; **header overrides path** (per user's choice).
- **Membership**: `Principal.use_cases` derived from token groups under `/use-cases/<slug>`;
  `require_attribution` dependency authorizes `use_case ∈ use_cases` for OIDC (403 otherwise),
  attaches `Attribution(subject, method, use_case)` to `request.state`. `require_use_case` toggle
  (400 when missing). API-key/demo attributed without the group check (binding comes in FRD-205).
- **Keycloak realm**: added `/use-cases/{demo-uc,other-uc}` groups + a group-membership protocol
  mapper (`groups` claim); demo-user ∈ `/use-cases/demo-uc`.
- **Gates green**: 138 tests, **100% coverage**; ruff (+ FastAPI `Depends` bugbear config) + mypy
  --strict clean.
- **End-to-end verified**: real Keycloak token carries `groups`; `/uc/demo-uc` → 200, `/uc/other-uc`
  → 403 PERMISSION_DENIED, header overrides path → 200, no use case → 200.
- **Next: FRD-103** (persist request/response + attribution), then FRD-104/105.

---

## 2026-08-04 — Decision: API-key issuance belongs in Management (ADR-0006)
- Clarified the control-plane/data-plane split for API keys: **issuance/lifecycle/show-once** →
  **Management** (self-service UI, bound to use case); **validation** → **Gateway** against a local
  **read-model** fed by **Kafka** (`api_key.*` events; never plaintext). Rejected sync-call and
  shared-DB alternatives.
- The Phase-1 gateway-side generation + CLI are a **bootstrap**; issuance moves to Management in
  **Phase 2** (new ROADMAP `FRD-205`). The gateway `api_keys` table becomes the read-model. OIDC
  validation stays in the Gateway. No code change now — documented as `ADR-0006`; updated
  FRD-101/PRD/ROADMAP.

---

## 2026-08-04 — FRD-101 Slice B: OIDC bearer validation — **auth complete**
- **OIDC validation** (`gateway/auth/oidc.py`): `OidcValidator` verifies a Keycloak JWT via the
  issuer's **JWKS** (`PyJWT` + `cryptography`), checking signature, issuer, expiry, and (optional)
  audience; resolves to a `Principal(method="oidc")`. JWKS client is injectable → unit-testable
  without a live Keycloak. `build_oidc_validator` gates on `oidc_enabled`/`oidc_issuer`.
- **Wired** into `resolve_principal`: a non-AIRA `Bearer` token is validated by the OIDC validator
  when configured (`app.state.oidc_validator`); API keys still take the `aira_` path.
- **Keycloak realm**: added `deploy/compose/keycloak/realms/aira-realm.json` (realm `aira`, public
  client `aira-gateway` with direct-access grants, demo user `demo-user`); imported on startup.
- **Gates green**: 123 tests, **100% coverage**; ruff + mypy --strict clean. Hermetic OIDC tests use
  a self-signed RS256 keypair + fake JWKS resolver (valid/expired/wrong-iss/wrong-aud/bad-sig/no-sub).
- **End-to-end verified**: fetched a real access token from Keycloak (password grant) → Gemini route
  returns **200** with the bearer, **401** for a garbage token. Run with
  `AIRA_OIDC_ENABLED=true AIRA_OIDC_ISSUER=http://localhost:8080/realms/aira`.
- **FRD-101 complete** (API key + OIDC). **Next: FRD-102** (attribution: request → user/project/use-case).

---

## 2026-08-04 — FRD-101 Slice A: API-key authentication + gateway DB layer
- **Gateway DB layer** (`gateway/db/`): SQLAlchemy 2.0 async via **psycopg3** (Postgres) /
  **aiosqlite** (tests); `Base`, engine/sessionmaker builders, `create_all` (Alembic deferred to
  FRD-103), and the `api_keys` table. App builds the engine + runs `create_all` in a lifespan.
- **API keys** (`gateway/auth/`): format `aira_<prefix>_<secret>` (hex), only the SHA-256 **hash**
  stored; `ApiKeyService` (create/verify/revoke/ensure_demo_key) with constant-time compare;
  `Principal` (subject + method); credential extraction (`Authorization: Bearer` → `x-goog-api-key`
  → `?key=`); `require_principal` dependency guarding the Gemini routes (Gemini-shaped 401).
- **Toggle & demo**: `auth_required` (default true); demo mode seeds a deterministic demo key.
- **CLI** (`python -m aira_gateway.cli api-key create|revoke`) to mint/revoke keys.
- **Gates green**: 111 tests, **100% coverage**; ruff + mypy --strict clean. Tests hermetic
  (in-memory SQLite; pytest auto-detected).
- **End-to-end verified** against Postgres: CLI minted a real key (persisted in `api_keys`); the
  Gemini route returns **401** without a credential, **200** with the key (header/`?key=`/Bearer),
  **401** for a bad/revoked key.
- **Next: FRD-101 Slice B** — OIDC bearer validation (Keycloak JWKS) + realm import, plugged into
  the same `resolve_principal`.

---

## 2026-08-04 — FRD-100: Gemini-compatible unified API (Phase 1 begins)
- **Decision**: ship the **Gemini** wire format first (existing projects run on it); OpenAI later →
  `ADR-0005`. Updated PRD/ROADMAP/README; added detailed `FRD-100`.
- **Canonical core** (`gateway/core/canonical.py`): provider-agnostic request/response/usage/chunk —
  the single schema every surface and upstream agrees on (so OpenAI/FRD-106 is just another mapper).
- **Upstream abstraction** (`upstreams/base.py`): `Upstream` protocol + `ProviderRegistry`; the
  deterministic `MockProvider` (evolved from FRD-002) is the only provider in Phase 1.
- **Gemini surface** (`api/gemini/`): Pydantic wire schemas, Gemini⇄canonical mappers, and routes —
  `POST /v1beta/models/{model}:generateContent | :streamGenerateContent | :embedContent`,
  `GET /v1beta/models`, `GET /v1beta/models/{model}`. Gemini-shaped error envelope (400/404/500).
- **Gates green**: 88 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified** via curl: list models, `:generateContent` (correct candidates + usage),
  NDJSON `:streamGenerateContent`, and unknown-model → 404.
- **Next in Phase 1**: FRD-101 (auth: API key + OIDC), then attribution/persistence/tracing.

---

## 2026-08-04 — FRD-002: seed & demo mode — **Phase 0 fully complete**
- **Seed framework** (Django, `aira_management.apps.seed`): an extensible registry — each phase
  registers idempotent `SeedContribution`s (run in `(order, name)`); a `seed_demo` management command
  runs them, supports `--fresh` (reset) and refuses production without `--force`.
- **Phase 0 contribution** `roles_and_users`: creates the five roles as Django `Group`s and one
  deterministic demo user each (admin/itsec/itgov/ucadmin/ucuser), idempotently. Roles centralized in
  `aira_management.roles.Role` (reused by Phase 2 RBAC).
- **Mock upstream** (gateway `upstreams/mock.py`): deterministic offline completions/embeddings for
  demo mode (basic; full fidelity in FRD-104).
- **Hermetic tests**: `settings.py` uses in-memory SQLite under pytest (`"pytest" in sys.modules` —
  ordering-robust, replaced a fragile conftest env hack), so the suite needs no Postgres.
- `make seed` / `make seed-reset` wired (migrate + seed_demo).
- **Gates green**: 68 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: `make seed` against live Postgres created 5 groups + 5 users mapped to
  roles; re-run created nothing (idempotent); confirmed in the `aira_mgmt` DB.
- **Phase 0 (Foundation & Infra) is complete** (all of FRD-000/001/002). **Next: Phase 1 — Gateway MVP.**

---

## 2026-08-04 — FRD-001: observability baseline (backend switched to Grafana otel-lgtm)
- **Decision change**: SigNoz deprecated its Docker Compose manifests (Foundry-only), so it can't be
  embedded cleanly. Switched the local OTLP backend to **Grafana `otel-lgtm`** → `ADR-0004`
  (supersedes `ADR-0002`). Updated PRD/ROADMAP/CLAUDE.md/FRD-001.
- **Compose**: added `otel-collector` (contrib 0.157) + `otel-lgtm` (0.30) under an `observability`
  profile; collector config forwards OTLP → otel-lgtm (`otlp_grpc`). `make up` now includes
  observability by default; `make up-core` for a lean start.
- **Instrumentation**: new `aira_common.observability` (tracer/meter/logger providers, OTLP/HTTP
  export, gated by `otel_enabled`); structlog `add_trace_context` processor (trace/span ids in
  logs); Kafka header inject/extract helpers for cross-component context. Gateway auto-instruments
  FastAPI, management auto-instruments Django when enabled.
- **Gates green**: 55 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: ran the gateway with `AIRA_OTEL_ENABLED=true`; spans for `/healthz` +
  `/readyz` (service.name=aira-gateway, http.route, status) flowed apps → collector → otel-lgtm and
  are **queryable in Tempo**; no export errors. Grafana UI at `http://localhost:3000`.
- **Next:** `FRD-002` (seed & demo mode), then Phase 1 (Gateway MVP).

---

## 2026-08-04 — Phase 0 / Slice 3b: Angular frontend shell — **Phase 0 complete**
- Scaffolded **`management/frontend`** with **Angular 22** (latest; note: Node is 26, Angular is 22).
  Uses the new `@angular/build:unit-test` builder → **Vitest + jsdom** (no browser needed — CI-friendly).
- Replaced the default welcome page with a minimal **AIRA shell** (title/subtitle header, nav
  placeholder, `router-outlet`); updated specs (3 tests) and page `<title>`.
- Wired frontend into `make`: `test`/`test-frontend`, `lint`/`lint-frontend` (Prettier + build),
  `fmt`, `run-frontend`, and `sync` (npm install). `make test` now runs Python + frontend together.
- **Gates green**: `ng build` OK (~216 kB), 3 frontend tests pass, Prettier clean; Python side still
  41 tests / 100% coverage / ruff + mypy clean. `node_modules`/`dist` git-ignored.
- **Phase 0 (Foundation & Infra) is complete**: full local stack (`make up`) + gateway, management
  backend, and frontend skeletons, all tested and observ-ready hooks in place.
- **Next:** Phase 1 — Gateway MVP (`FRD-100` unified API, `FRD-101` auth, `FRD-102` attribution,
  `FRD-103` persistence, `FRD-104` mock upstream, `FRD-105` tracing/IP). Also still pending from
  Phase 0 plan: OTel Collector + SigNoz wiring (`FRD-001`) and seed/demo (`FRD-002`).

---

## 2026-08-04 — Phase 0 / Slice 3a: management backend (Django + DRF)
- Added **`management/backend`** as a third uv workspace member: **Django 6.0 + DRF 3.17 +
  psycopg 3.3** on Python 3.14 (src layout, package `aira_management`).
- Structure: `config` (settings driven by a typed `ManagementSettings`, `runtime.get_settings()`,
  urls/asgi/wsgi), `apps/health` (`/healthz` + `/readyz` mirroring the gateway contract, reusing
  `aira_common`), `manage.py`.
- **Type-checking**: wired **django-stubs** mypy plugin; refactored the dynamic `settings.AIRA`
  access to a typed `get_settings()` accessor so `mypy --strict` stays clean.
- **Quality gates green**: 41 tests total, **100% coverage** across gateway+libs+backend;
  `ruff`, `ruff format`, and `mypy --strict` (25 files) all pass. `make run-backend` added.
- **Smoke test**: `manage.py check` clean; runserver `/readyz` returns `ready` against the live
  Compose stack (postgres+kafka reachable, HTTP 200).
- **Next:** Slice 3b (Angular frontend shell) to close Phase 0.

---

## 2026-08-04 — Phase 0 / Slice 2: gateway skeleton + shared libs
- **uv workspace** at repo root (`pyproject.toml`) with members `gateway` + `libs`; shared tooling
  config (ruff, mypy strict, pytest, coverage gate `--cov-fail-under=90`). Python 3.14 venv via uv.
- **`aira-common`** shared lib: `config` (pydantic-settings base), `logging` (structlog JSON),
  `errors` (AiraError + ErrorResponse envelope), `events` (EventPublisher protocol +
  InMemoryEventPublisher; real Kafka transport deferred to Phase 1), `health` (async TCP checks).
- **`aira-gateway`** skeleton (FastAPI): app factory (`create_app`), `GatewaySettings`,
  `/healthz` + `/readyz` (probes Postgres + Kafka), AiraError exception handler, `main:app` entry.
- **Quality gates green**: 32 tests, **100% coverage**; `ruff check`, `ruff format --check`, and
  `mypy --strict` all pass. Wired `make sync/test/lint/fmt/run-gateway`.
- Note: on Python 3.14, ruff formats multi-type excepts with PEP 758 syntax
  (`except TimeoutError, OSError:` — no parentheses); valid and intended.
- **Smoke test**: ran the gateway against the live Compose stack — `/readyz` returns `ready`
  with postgres+kafka reachable (HTTP 200).
- **Next:** Slice 3 (management backend skeleton: Django + DRF) + Angular workspace shell.

---

## 2026-08-04 — Phase 0 / Slice 1: infra stack + toolchain
- **Toolchain** (ADR-0003): confirmed Python 3.14.4 + uv 0.9.26 present. Installed **Node 26.6.0**
  via nvm; worked around `NPM_CONFIG_PREFIX` (unset in persistent env) and symlinked node/npm/npx
  into `~/.local/bin` (first on PATH); installed system lib `libatomic1` (Node 26 dependency).
- **Monorepo skeleton**: `gateway/`, `management/backend/`, `management/frontend/`, `libs/`,
  `deploy/compose/` created.
- **Docker Compose infra** (`deploy/compose/`): postgres 17, keycloak 26.1, kafka 3.9 (KRaft),
  schema-registry 7.8, vault 1.18 — with healthchecks, `.env.example`, postgres init script
  (creates `aira_gateway`/`aira_mgmt`/`keycloak` DBs), and a root `Makefile`
  (`up/down/destroy/ps/logs` + stub `test/lint/fmt/seed`).
- **Brought up & verified healthy**: postgres (DBs created), kafka (fixed a KRaft
  `advertised.listeners 0.0.0.0` error → use `://:PORT` + `localhost` quorum), schema-registry
  (API responds), vault (unsealed).
- **Keycloak**: initially blocked (quay.io 403); resolved after the host allowed quay.io. Image
  pulled, service healthy, OIDC discovery reachable at `/realms/master/.well-known/openid-configuration`.
- **Slice 1 complete**: all five infra services (postgres, keycloak, kafka, schema-registry, vault)
  up and healthy via `make up`.
- **Next:** Slice 2 (gateway skeleton + shared `libs/`).

---

## 2026-08-04 — Git init + Phase 0 FRDs
- Initialized the Git repository (branch `main`) and added a `.gitignore` (Python, Node/Angular,
  secrets/`.env`, Docker data volumes).
- Wrote the three **Phase 0 FRDs**:
  - `FRD-000-foundation-infra` — monorepo layout, Docker Compose stack (Postgres, Keycloak, Kafka
    +schema-registry, Vault), service skeletons, shared `libs/`, CI + coverage gate, Make targets.
  - `FRD-001-observability-baseline` — OTLP → OTel Collector → SigNoz, app instrumentation, trace
    context propagation over HTTP + Kafka, correlated logs/metrics.
  - `FRD-002-seed-and-demo-mode` — `DEMO_MODE`, mock upstream (basic), idempotent extensible
    seed framework covering all five roles, deterministic data.
- **Next:** implement Phase 0, starting with `FRD-000` (Compose stack + skeletons + CI).

---

## 2026-08-04 — Project kickoff & planning foundation
- Established project vision and scope; created **`docs/PRD.md`** (Project Requirements Document v0.1).
- Created **`docs/ROADMAP.md`** — phased delivery plan (Phase 0–7).
- Added **`docs/features/FRD-TEMPLATE.md`** and **`README.md`**.
- Locked key decisions:
  - Management UI = **Angular + Django REST Framework** → `ADR-0001`.
  - Local observability = **OTel Collector + SigNoz** (alt: Grafana LGTM) → `ADR-0002`.
  - Docs & code in **English**; **Docker Compose** locally; **automated seeding** + demo mode required.
- Created **`CLAUDE.md`** (project guidance) and set up **`docs/adr/`** (ADR process + first two ADRs).
- **Next:** write Phase 0 FRDs (`FRD-000` foundation, `FRD-001` observability, `FRD-002` seed/demo),
  then begin implementation of Phase 0 (Foundation & Infra).
