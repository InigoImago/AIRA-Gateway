# Lessons — what this project has already paid for

Rules that came out of defects found here, each with the cases that produced it. They are not in
any FRD: an FRD says what a feature must do, and none of them says *"this failure shape has now
happened five times."* That sentence is what this file is for.

**How to use it.** Read the headings when planning; read an entry when its shape is in front of
you. A rule with several instances is not a stronger rule — it is one this codebase keeps
re-learning, so it is the one worth checking first.

**How to extend it.** A new round appends to [`DEVLOG.md`](DEVLOG.md). Only a rule that is *new*
comes here, and it is **merged into an existing entry** if it is the same shape wearing different
clothes — this file goes stale by growing, not by being wrong.

Detail and dates: [`DEVLOG.md`](DEVLOG.md) · decisions: [`adr/`](adr/README.md) · features:
[`features/`](features/README.md).

---

## 1. Recurring defect shapes

These have each happened more than once. When something behaves impossibly, check these before
reading code.

- **Know which quantity a lever moves before reaching for it.** An external OTLP receiver
  answering `429` looks like "we send too much", and the obvious knob is the sample ratio — which
  is measured to reduce spans per request from 17.5 to 5.9 and to leave the **request count
  unchanged**. The request count is set by the batch *timer*; the payload is set by sampling. A
  receiver that limits requests per second and one that limits ingested volume need opposite
  answers, and the wrong one produces a confident change with no effect.

  *And the default of a component you did not write is a decision you have made.* The collector's
  `otlphttp` exporter forwards every 200 ms from ten concurrent senders and retries a `429`,
  because a `429` is retryable — three defaults that are individually reasonable and together are
  the worst available shape for a rate-limited endpoint. Nothing in this repository had ever
  chosen them.

- **A diagnostic that is present, correct and unreadable is worse than an absent one**, because
  its presence is taken for coverage. Two in one day, both reported by a user of the thing: the
  `otel` export line was in the log and carried no `trace_id` — an export is a timer, not a step in
  a request — so beside `redis/script` and `postgres/connect`, which do carry one, it read as
  belonging to nothing and was reported as *"I don't see it go through OTel"*. And
  `showcase_doctor` walked Keycloak, the realm, the accounts, the database and Kafka container to
  container, reported everything green, and said nothing about the login — which a **browser**
  walks, on a machine the doctor cannot see. Both were read as findings: "nothing happened", and
  "the login is fine". Ask of a diagnostic not only whether it fires but **what a reader will
  conclude from its shape** — and, for anything reporting on a chain, whether the reader can tell
  which links it walked.

- **An address handed to a browser is not an address, it is an address *from where the browser
  is*.** `localhost` is correct in `runtime-config.js`, in the realm's redirect URIs, in
  `AIRA_OIDC_ISSUER` and in everything `make` prints — and every one of them is correct only for a
  browser on the Docker host. From anywhere else all four give the same `server could not be
  reached`, at four different walls, in an order that makes each fix look like the last one
  needed. When a value crosses into a browser, ask whose name resolution will read it.

  *And half a rule is the worse half.* The console's **port** was made a variable in August with a
  test file explaining that a knob which silently breaks authentication is worse than no knob; the
  **host** in the same two lines stayed a literal until somebody tried to reach the stack from
  their own machine. A parameter added beside a literal reads as *this is configurable* — so the
  next person stops looking at exactly the point where the remaining literal is about to refuse
  them.

- **A file that sets a key twice means whatever its line order means.** Compose takes the last
  definition and says nothing about the first, so a value appended at the bottom silently beats the
  one near the top — and the top one is what anybody reads. Found live: `AIRA_BIND_HOST` at lines
  10 and 123 of a working `.env`, which made rebuilding that file from the shipped example look
  like a regression in something else entirely. The check is cheap; the reason it was missing is
  the interesting part — `verify` returned early with a friendly note for hand-made files, so the
  files most likely to have the defect were the ones checked for nothing. **An early return that
  skips a whole class of file is a check that does not cover its likeliest case.**

- **Write the reader's command out, and run it.** A debug facility is finished when somebody can
  extract what it produced, not when it produces it. Printing the OTLP payload passed every test
  and was unusable twice over: it was pretty-printed, so one payload was forty lines and no longer
  one line of a log — `tail -1` returned a closing brace — and the obvious `docker logs … | jq`
  prints *nothing at all*, because the web server's access lines are not JSON and `jq` stops at the
  first one. Both surfaced in the minute spent writing the recipe into the documentation, and
  neither is visible from inside a test that already holds the parsed object.

- **A rendering meant to show what the far end receives is only tested by comparing with the far
  end.** `payload_as_json` printed an OTLP batch "through the exporter's own encoder, so it cannot
  drift from what is sent" — and `MessageToJson` applies protobuf's *generic* JSON mapping, which
  OTLP overrides: identifiers came out base64 (`TETTPxm0Rt5w6G3guyzfIA==`) where a receiver expects
  hex, and enums as names where a collector sends numbers. Every test passed, because they asked
  whether the document parsed and said the right span name — questions about the renderer. A reader
  found it in a day. Ask the question the feature exists to answer, and for anything claiming to
  show a wire format, that means **standing up the other end and diffing whole documents**: doing
  that here turned one reported symptom into three findings, the third of which nobody had noticed.

  *And the rule was written a day before the check was.* The entry above went into this file with
  the fix, and the test it names — hand the output to a real collector and ask whether it took it —
  was not written until a reader asked *did you actually check*. **A rule recorded without the
  check it prescribes is a rule that has been agreed with, not adopted**; the repository already
  fails a build for a stale FRD header, and this is the same shape one level up.

- **A success returned by a client library is a claim about the client's own call, not about the
  far end.** `SpanExportResult.SUCCESS` means `resp.ok` and nothing more — and OTLP answers `200`
  with a body saying it dropped half the batch, which the Python exporter discards unread. So the
  channel built around *"no errors" and "it arrived" are different statements* printed a clean
  green line for telemetry that had been thrown away, one layer inside the very distinction it
  exists to draw. Ask of any wrapped client: **what does its success value actually attest** — the
  socket, the status, or the work? — and report that thing by name rather than folding it into a
  word the reader will take for the strongest of the three.

- **A recipe in a document is a claim; ask the product.** The `.env.example` rewrite offered four
  ready-made configurations, and one of them did not start — found by running each through both
  planes' own `unsafe_settings` in a subprocess rather than by reading them back. Underneath was a
  real asymmetry: `AIRA_DEMO_MODE` waives six checks on the gateway and none on Management, so
  `AIRA_ENVIRONMENT=demo` yields a stack whose data plane starts and whose control plane refuses —
  and `CONFIGURATION.md` said the opposite in a flat list of eight that mixed the two planes.
  **Where a check exists per plane, a document that lists checks without naming the plane is wrong
  for one of them**, and it is wrong for whichever half the reader meets first.

- **A shared stateful stack is one test run at a time, and the rule does not stop at the suite
  boundary.** `playwright.config.ts` sets `fullyParallel: false, workers: 1` with the reason in a
  comment — *the suite drives shared, stateful services*. Starting the integration suite and the
  browser suite against the same containers at once produced one failure that passes alone in a
  tenth of the time. Whatever a suite says about its own concurrency is a statement about the
  stack, not about the suite.

- **A schema change is a change to every writer, and the ORM hides how many there are.**
  `FRD-615` added `OutboxEvent.traceparent` — a `CharField`, so `NOT NULL` with no database
  default, which is Django's ordinary shape: the ORM supplies `""` on every save, so every ORM
  writer kept working and nothing in `make ci` moved. The writers that broke were the four
  hand-written `INSERT INTO outbox_outboxevent (…)` statements in `tests/integration/`, which name
  their columns — and that layer does not run by default, so five tests sat red for a day with a
  `NotNullViolation` naming the very column the round was about. **Ask, of any added column: who
  writes this table without the ORM?** — raw SQL, a fixture, a backfill, another service, a
  `COPY`. And note where the answer lives: raw SQL survives in the layer that needs a live stack,
  which is exactly the layer a green `make ci` says nothing about.

- **Two correct halves and no wire.** Both ends exist and nothing joins them, so every review of
  either end passes. *Six instances:* `record_to_outbox` had no topic for an event type;
  `payload_size` counted bytes into a column nothing wrote; the seed wrote a catalog and emitted
  no event; a `throttle` produced a value the limiter could not consume; `FRD-116` shipped Vault
  and no container was given `VAULT_ADDR` for three days; and `Upstream.thinking_modes` — which
  dialect can express *"you decide"* — was declared by four adapters, asserted by one test, and
  **read by no code on any path**, so the console offered a mode whose every use was refused.
  **Test the wire, not the ends.**

  *A seventh, and it names the trap that catches the fix.* `GroupGrantResolver.use_cases` answers
  `{slug: role}` — with a test asserting the role is carried through — and `_with_group_grants`
  took `.keys()`. `payloads.grant_role_in` then re-derived the role from `use_case_members`, where
  a **group** grant writes no row, so the route `FRD-209` FR-6 leads with produced an administrator
  the gateway read as a plain member: refused their colleagues' prompts in a use case that
  restricts members to their own, and shown a narrowed trace list, while Management (which asks
  guardian) treated them correctly. The trap is what happened next: six tests were written for the
  fix and **five of them construct a `Principal` themselves**, so cutting the wire again left all
  five green. A test that builds the object under test is a test of the reader, however faithfully
  it fills the object in — the sixth had to drive the layer that *populates* it. When you fix a
  wire, the test that proves it has to start upstream of the wire.

  *An eighth, and it was caught by the harness rather than by the round that created it.*
  `FRD-613` widened the kill switch to stop a person whichever credential they hold, tested it
  against `SuspensionService.check` — and the mutation that removes the **argument** from
  `guard_before_work` survived. The service knew; the gate did not hand it over. The round whose
  entire subject was this shape wrote the test at the end rather than at the wire, which is
  evidence that knowing the rule is not the same as applying it under time pressure: the only
  reliable defence is the mutation, and the only reliable place for the test is **upstream of the
  wire**.

  *An eighth, and the injecting end was the one that looked right.* `FRD-615`: trace context has
  been propagated on Kafka headers since `FRD-001`, with both ends written and a round-trip test,
  and no trace ever crossed the bus. The consumer read nothing — expected, once looked for. The
  producer wrote nothing either, and that half is the lesson: `kafka_headers_from_context()` reads
  the **ambient** span, and an outbox breaks the causal chain on purpose, so the relay that
  publishes has no span and the injection produced an empty carrier on every event in every
  deployment. **A call that reads implicit state is a call whose correctness depends on where it
  runs**, and "where it runs" is exactly what an outbox changes. Asked of a context, a queue or a
  transaction: is the thing this reads still in scope in the process that will actually run it?

  *A ninth, and the missing wire was a **handler**.* `configure_observability` attaches the OTLP
  `LoggingHandler` to the root logger, and the OpenTelemetry SDK reports every export failure — with
  the endpoint and the reason — on a stdlib logger under `opentelemetry.*` that propagates there. So
  with telemetry on, the only handler that sentence could reach was **the exporter that had just
  failed**, and `logging.lastResort` does not cover it: it prints only when a record finds *no*
  handler, and this one found the broken one. A collector on the wrong port therefore produced a
  perfectly ordinary-looking service and no diagnosis anywhere, for as long as the flag had existed.
  The general form is worth carrying: **a diagnostic that travels by the thing it describes has no
  route on the day it is needed.** Ask, of anything that reports on a transport — a log about
  logging, an alert about the alert pipeline, a health check that writes to the database it is
  checking — which path it takes when the subject is broken.

  *A tenth, and the wire was fine — it was **when** it ran.* `FRD-617`'s channel is switched on
  from settings, and `VaultSource` is a settings *source*: `load_secrets` runs **inside**
  `GatewaySettings()`, one step before every entry point configures anything. So the one system
  whose entire life is start-up was the one system the feature could not describe — a gateway
  pointed at a dead Vault port failed closed correctly, with the switch set to `all`, and said
  nothing. No test could have found it, because every test constructs its settings before it looks;
  the running stack found it in one restart. **A wire configured from settings cannot cover what
  runs while the settings are being built.** Ask of anything switched on at start-up: what runs
  *before* the switch is read — a settings source, a module import, a `ready()` hook, a migration.

  *A constant is an end too.* `pipeline/config.MAX_MODEL_LENGTH` — *"the same ceiling Management's
  serializer applies"* — sat beside a comment naming three ways into that parser which bypass
  Management, and was read by nothing; `foundry.DEFAULT_API_VERSION` documented the pinned Azure
  version while the settings default carried the same literal and was the one on the wire, so
  bumping the constant would have changed the comment and not the request. **A named bound that
  nothing reads is a bound the module claims and does not have**, and it reads as reassurance to
  the next person, which is worse than its absence. The counterpart is cheap: apply it, or assert
  the two copies are equal.

- **"Unreachable in practice" is a claim about who can reach it, and the console is somebody.**
  `DialectUnsupported` was left out of the one list both surfaces catch, on a documented argument:
  *"a model that cannot do a thing does not declare the capability."* True of the seed, false of
  the screen where a Global Administrator declares one — ticking a box took ten seconds and turned
  every thinking request into `500 Internal error`. An exception whose reachability depends on
  nobody making an ordinary mistake in a form is reachable. **A configuration mistake must arrive
  as a named refusal**, or the operator is sent to read the logs of a service that is working.

- **When every check is green and the thing is broken, ask which half you never tested.** Twice
  now, and both times the untested half was invisible because the tested half was genuinely
  correct.
  The console was unreachable from outside with `ERR_CONNECTION_RESET` while
  `docker compose ps` was healthy, `curl localhost:4200` answered 200, and the served HTML was
  right — and nginx's access log held **no request from the browser at all**. Docker opens one
  socket per published entry rather than one dual-stack socket (`bindv6only=0` notwithstanding),
  so `AIRA_BIND_HOST=0.0.0.0` left fourteen IPv4 listeners and **zero** IPv6 ones; the forwarder
  carried `::1 4200 -> 4200`, the browser resolved `localhost` to `::1`, and the connection was
  accepted on the near side and reset on the far side.
  Three things generalise past IPv6:
  **a reset is not a refusal** — something accepted, so every diagnosis starts at the wrong end and
  looks outward;
  **the access log of the thing that should have answered is the fastest discriminator** — an empty
  log means the traffic never arrived, which separates "my service is wrong" from "my service was
  never asked" in one look, and it was the first thing that pointed anywhere useful;
  and **the evidence was in the user's own output** — the `::1` rows were in the port listing I had
  already read, and I skimmed them because I was looking for a missing forward rather than a
  present one.
- **Recognise a shape, do not remember a list of names.** *And a hand-written list of what-must-
  be-checked is the same defect one level up.* `test_compose_passes_the_settings_it_names.py`
  exists to catch a credential the shipped stack cannot pass, and carried nineteen names by hand —
  nine credentials, every one of them on the gateway. `AIRA_DIRECTORY_CLIENT_SECRET`, a Keycloak
  service-account secret on the **management** plane, was on no list and reached no container, so
  `FRD-209`'s directory search was unreachable in every containerised deployment; it degrades
  quietly by design, so nothing said so. A guard's own coverage is a list somebody maintains, and
  the next item is by definition the one nobody thought of — derive it (`_SECRET`, `_PASSWORD`,
  `_API_KEY`, … are a *shape*) and keep the exceptions as a waiver that has to name something real. `strip_attachments` matched wrapper keys
  — `inlineData`, `inline_data` — under a comment saying the second surface's shape would be added
  "when it lands". It landed, nothing was added, and every attachment on that surface went into the
  audit row verbatim: a 5 KB PDF stored whole, on a request that was *refused*. A dict carrying a
  media type and `data` together **is** inline binary wherever it sits, and asking that covers the
  surface nobody has written. Related: a comment predicting a future gap is not a plan, and nobody
  re-reads it on the day it comes true.
- **A guard written in the language it guards inherits that language's blind spots.**
  `is_catastrophic` decides which operator-supplied regexes may run on the request path, and it
  *was* a regex: it matched the outer quantifier as `[+*]`, so every `{n}` form walked past, and
  its `[^)]*` could not see beyond the first `)`, so a group inside a group was invisible. Four
  shapes it accepted were timed at 35–159 seconds on a thirty-character input. A pattern language
  cannot describe its own nesting — use a scanner. And check the widening in **both** directions:
  a detector that refuses one of the built-ins is not a fix, it is a gateway that will not start.
- **A number is not a defect until it moves.** `(.*a){20}` cost 30 ms and looked like a fifth
  finding; measured against inputs from 20 to 800 characters it stayed flat at 30 ms, so it is a
  fixed cost, not a backtracker. Growth is the property, never the first reading.
- **A caller's own value must never become a server error — and never a missing record.** Three in
  one sweep, each dying far from where it entered: a lone surrogate (`"\ud800"`) parses fine and
  cannot be encoded, so it died inside the HTTP client nine steps later; `1e309` parses to `inf`,
  which Python writes as `Infinity` and no `json` column will take, so the request was refused
  correctly and **the refusal was recorded nowhere**; an `int` wider than the `INTEGER` column it
  is compared against reached the driver. Python's types are unbounded and the database's are not,
  and a boundary that models one as the other has moved the failure to where it reads as ours.
  Ask at the boundary whether the value can be *written down*, and make the recorder survive one
  that cannot — the caller's door and the upstream's are different doors.

  *Four more, and how they were found is the lesson.* `POST /v1beta/suspensions` and
  `:checkThinking` read their body with a bare `await request.json()`, so a stray brace was a **500**
  on the two endpoints somebody reaches for during an incident — while `api/pipeline.py` and both
  API surfaces already spell out the guarded form. The same endpoint's two numbers were `int(...)`
  with nothing in front (`"many"`, then `10**30` as `OverflowError`), a non-numeric id in
  `/installation-budgets/<id>` raised out of Django's *query builder*, and `to_nanos` promised a
  `ValueError` and raised `InvalidOperation` for `"Infinity"`. **None of these was found by asking
  about a field.** They were found by sweeping every endpoint with the same short list of wrong
  values, which is a question about the *file* rather than about the field — and the only kind of
  question that can see a rule stated in three places and not inherited by the two routes written
  afterwards. A per-field test proves a field; a sweep proves the ones nobody thought to name
  (`test_a_callers_value_is_never_a_server_error.py`).

  *An eighth, and this one was created by a fix rather than found by one.* `FRD-617` split the JWKS
  fetch out of `JwtVerifier.verify` so that an unreachable Keycloak could be told apart from a bad
  token — and thereby narrowed what the fetch's `except` covered, from `PyJWTError` to
  `PyJWKClientError`. `PyJWKClient` parses a token to find its `kid` **before** it fetches anything,
  so a truncated bearer now raised `DecodeError` straight out of `verify()`: a `401` that had held
  since `FRD-101` became a `500`, for a value any client can send. Seven tests were written for that
  round and every one of them used a well-formed token; the demonstration found it in a minute, with
  a token typed by hand. **Narrowing an `except` is widening what escapes**, and the question to ask
  of a `try` you are splitting is not *what do I now catch* but *what used to be caught here that no
  longer is*. The cheap counterpart: when the value under test is one a caller supplies, one of the
  cases has to be malformed — a suite that only ever presents well-formed input is a suite that
  agrees with its author about what the input looks like.

- **An export is not read, it is executed.** A CSV cell beginning with `=`, `+`, `-` or `@` is a
  formula to Excel, LibreOffice and Sheets, and both exports here hand one straight through: the
  usage report's `key` column is caller content (`served_model` falls back to `requested_model`, so
  a `404 model_not_found` carries the string out of the URL — measured, `=1+1,1,0,…` as the first
  data row of a file every oversight role can download), and the smoke-test export's `response`
  column is a **model's own answer**, which needs no attacker at all. The second file was written
  after the first and says so — *"the same conventions `FRD-602` had to get right once already"* —
  and copied the BOM, the CRLF and the quoting, because those were there to copy. **A convention a
  later file can copy is not the same thing as a hazard nobody wrote down**: ask what the *reader*
  of an artefact does with it, not only whether the artefact is well-formed. Prefixed, never
  stripped and never refused — a row quietly missing from a governance document is worse than an
  odd-looking one.
- **A search-and-replace that stops two files early leaves the shape it was fixing.**
  `tools/seed_local_catalog.py` moved the demo's chat model onto the predecessor's own id, and
  `tests/integration/conftest.py` was given `LOCAL_CHAT_MODEL_ID` in the same round with the hazard
  written out in full: *"Six tests carried `9001` as a literal, and moving the demo … would have
  left every one of them addressing a model that no longer answers — reported as a `404` about a
  number, which reads as a broken surface rather than as a stale test."* Six were corrected.
  **Twenty-one were not**, in six other files, and the embedding id was typed nine more times
  beside them. The consequence arrived exactly as that paragraph described it: a live-stack run in
  which every KIRA test failed while one hand-made call to the same endpoint answered `200`, and an
  hour spent reading a correct gateway as a broken surface.

  Two things generalise. **The fix for a copied constant is not to correct the copies, it is to
  make the next one impossible** — the paragraph explaining the danger existed, and it protected
  the six files somebody happened to have open. And **scope the ban to where the number is somebody
  else's**: the first version of the guard covered every test layer and reported thirteen files
  that were all correct, because a hermetic test writes its own catalogue row and the number is
  local to it. A guard written against a real defect can still cry wolf, and then it is the guard
  that gets deleted.

- **A default argument is a silent one** — the wire shape's worst variant, because there is nothing
  *missing* to notice. `resolve()` had taken a `direct` argument since the vocabulary was written,
  with tests of its own; both planes called it with two arguments, so a grant naming a person was
  specified, replicated to the gateway and read by nobody (`FRD-209` FR-6). The same day, in the
  fix for it: deleting `username=username` from the write survived the whole suite — the column,
  the grouping and the panel were covered and the step that fills them was not. A missing map entry
  at least leaves a gap somebody can see; an unpassed parameter looks exactly like a call.

  *And an argument a library **discards** looks exactly like one it uses.*
  `HTTPXClientInstrumentor.instrument` takes `request_hook` and `async_request_hook`, and keeps the
  second only `if iscoroutinefunction(...)` — a plain function passed there is dropped without a
  word. Every upstream adapter in this gateway uses `AsyncClient`, so a sync-only redaction hook
  would have been a redaction that never ran, on a span carrying **our own** upstream API key. The
  call site reads identically either way. **Where a library selects an argument by inspecting it,
  the test has to drive the path that selects it**, which is why the guard makes a real async call
  and asserts on the attribute rather than on the wiring.

- **A test that cannot tell which of two calls answered is a test of neither.** Written while
  fixing two silent loads on one screen: the stub broke both, and the assertion looked for a word
  the *other* one also produced. Green, and it stayed green when the fix under test was reverted —
  found only by breaking it on purpose, which is the whole reason this project does that. Where a
  behaviour has two sources, **give each its own distinguishable answer and assert the one you
  mean**; a shared substring is a passing test with no subject.

- **An identity that crosses a system boundary is set once.** Two planes, two databases, one
  string: a use case's `slug` and a model's `name` are editable fields on the Management side and
  **primary keys** on the gateway side, arriving over Kafka. A rename therefore renames nothing.
  It abandons one object and starts another, and only the plane that did it knows.

  Measured: one `PATCH` by a use-case administrator on their own use case left the gateway holding
  the old row intact — no tombstone, keys still bound and still served `200`, pipeline and limits
  still enforcing — while Management answered `404` for it. Every control that reaches the data
  plane by naming a slug then reaches nothing: retirement, revocation, budgets, limits, purge. The
  same `PATCH` on a model left the installation with two approved catalogue entries, one of them
  permanently unreachable.

  Two things generalise. **The test is "does this string leave the process", not "is this field
  user-visible"** — a display name is free to change precisely because nothing keys on it, which
  is why the refusal points at `name`/`display_name` as the field that does the job. And **refuse,
  do not ignore**: `read_only` answers `200` with the old value, and a caller who patches an
  identity and reads `200` believes it changed.

- **A permission that asks about the installation is not a role.**
  `IsGlobalAdminOrUseCaseAdministrator` reads "does this person administer *any* use case", which
  is a fact about the whole grant table rather than about the caller's token or this request. A
  live RBAC matrix written against it granted a group administration in one cell and asserted the
  same group's exclusion two cells later — **the test disproved its own premise**, and it took a
  failure to notice, because the assertion looked like every other role assertion beside it. When
  a predicate's answer depends on state any other actor can change, assert only the ends that
  cannot move and say in the test why the middle is not asserted.

- **A value nobody wrote is a value nothing checks.** Every governance rule in this system reads
  what somebody *typed*: Management's serializer validates the models a pipeline **names**, the
  gateway refuses a model the use case was not **released**, the release check collects *"every
  model this pipeline could reach, wherever it is written."* A fallback that substitutes a value at
  runtime is outside all of it by construction, and the substitution is invisible in the artefact
  being checked. `PipelineEngine._default_model()` answered *"the first model in the registry"* for
  any step whose configuration named none — so a `pii_filter` with `config: {}` sent the caller's
  personal data to a model that was not released, not necessarily approved and in no particular
  region, at a `200`. Naming that same model in that same step is a `400`. **The whole difference
  between refused and served was whether the escape was written down.**

  Two things generalise. The justification is where it hides: the exemption list that lets a
  pipeline step call a model without conditions vouches for itself with *"bounded by the release,
  which the serializer validates every named model against"* — a true sentence about a named model
  and a silent one about an unnamed one, and a reader checking the claim finds it holds. **When a
  rule is stated over "what is named", ask what happens when nothing is.** And the repair is
  usually deletion rather than governance: each step already had a defined answer for *no model
  resolved*, and each was the safe one — block, fall back to the heuristic, use the configured
  default. A convenience that reaches around a gate is worth less than the gate.

- **A non-goal is about the feature that was there when it was written.** `FRD-300` recorded
  *"Embeddings filtering"* as a non-goal, and it was right: the pipeline was an injection filter
  and a router, both about a prompt a model will **answer**, and an embedding is not answered.
  `pii_filter` arrived into the same branch a fortnight later with a different contract — *where
  the caller's text goes and what is stored* — and inherited a decision nobody had made about it.
  Measured: the same sentence, the same use case, redacted on `:generateContent` and sent **and
  stored** untouched on both embedding verbs, on both surfaces, while the console showed one switch
  per use case and none per verb.

  The repair is not the branch, it is the **named set**: `TEXT_ONLY_STEPS` says which steps mean
  anything for a payload that is only text, so a fourth step has to answer that question instead of
  inheriting an answer from the shape of an `if`. **When a feature joins an existing stage, re-read
  the stage's non-goals as claims about the new feature** — they were true about its neighbours.

- **A promise with two halves is usually built with one.** `FRD-309` FR-3: *"where the substitution
  cannot be applied the payload is dropped, never kept."* Two ways it cannot be applied — the body
  does not contain the text the step rewrote, and **the redaction never happened** — and only the
  first was implemented, which is the rarer of the two. An unreachable redactor blocked the request
  and left the caller's name and address in `request_logs`, on a row nobody was served.

  The same family as the `pii_filter` rewrite that reached the model and not the database, one door
  along: both are *a data-protection control whose served path is right, which is what makes the
  other path invisible*. Reading a requirement sentence as a **list of cases** and asking which one
  the code branches on is cheap; noticing it later costs a stored prompt. And where a flag looks
  like it settles one of the cases, check what it actually names: `on_failure: allow` says keep
  **serving**, and keeping **storing** is a second decision nobody made.

- **A collection that silently discards is a check that silently stops.** The narrowest relative
  of the wire shape, and the only one where both ends *and* the call site read correctly.
  `plaintext_problems` took a `dict[str, str]`; the gateway built it with one
  `("AIRA_OIDC_ISSUER", issuer)` pair **per configured realm** (`FRD-118`), under a comment saying
  *"every issuer and every key set — a second realm reached over plaintext is the same hole as the
  first."* A mapping keeps the last value per key. Measured: `environment=production`, two issuers,
  the plaintext one **first** — and `unsafe_settings` returned an empty list, on the check the
  module's own docstring calls *the one misconfiguration that defeats authentication outright*.

  Nothing is missing from the source: the comment is right, the loop is right, the function is
  right. The loss happens in the *type*, between the two. Two things generalise. **A key that
  repeats is not a key** — a name that identifies a setting does not identify an occurrence of one,
  and the moment a setting can be given more than once, a mapping keyed on its name is a silent
  `distinct`. And **the fix belongs in the signature, not at the call site**: taking a sequence of
  pairs makes the collapse impossible to write anywhere, where fixing the one caller would have
  left the next one to rediscover it. The test that distinguishes the two readings puts the bad
  entry **first**; put last, it passes against the defect, which is precisely why the existing
  tests did not see it.

- **A hand-written list with no counterpart.** A set has no opinion about what it does not
  contain, so a missing entry announces itself through nothing. *Six instances:* Kafka topics
  (twice — `aira.rate-limits`, `aira.anomaly-rules`, created by nothing); `use_case_group.granted`
  with no topic; the SPA's capability array missing `tools` and `prompt_caching`; two abolished
  roles surviving in the realm file; `app.state` services. **The answer every time: compare the
  list against the constant in both directions** — a topic with no emitter is as wrong as an
  emitter with no topic.

  *And the copy can be the guard.* `aira_common.anomalies` calls itself closed; the console
  restated it **four** times — a dropdown, a units table, a sentence writer, and a test named
  *"every kind has words"* iterating a hand-written list. All four offered `token_spike`, which
  does not exist, and omitted `blocked_prompt_rate`, which does — so the test asserted completeness
  against a list that was itself incomplete. When a vocabulary cannot be imported across a language
  boundary, the comparison belongs in the language that can read **both** files.

  *A written-down danger is not a guard.* `core/auth/roles.ts` opens by naming the live round where
  two planes answered one question differently, states that a third copy is *"the same defect with
  a longer fuse"* — and then restates all three role sets, with nothing comparing them for a
  fortnight. The lists happened to agree, which is the only reason the paragraph was still true.
  **A paragraph explaining why a copy is dangerous is evidence that the copy needs a test, not a
  substitute for one.**

  *And the DEVLOG is not a fix either.* The round that switched telemetry on closed with
  *"AIRA's own log lines are not exported — structlog writes to stdout past the standard library"*,
  recorded because *"a reader will look for both and find neither"*. It is an accurate sentence
  about an unmet acceptance criterion in an FRD whose header says **Done** (`FRD-001` FR-6, §10),
  and it left the third of the observability baseline missing for another day. **Writing a gap down
  moves it out of the place that would have made somebody fix it** — a note reads as a decision,
  and nothing in a log is ever re-read against the code.

  *And the most misleading place to write one down is a test.*
  `test_compose_lifecycle_covers_the_stack.py` states, in its own docstring, that *"the application
  services carry `restart: unless-stopped` while the infrastructure does not, so they also come
  back"* — as an aggravating detail of the `make down` defect it does check. It never asked what the
  same asymmetry does to a **host restart**, which is the more ordinary of the two events, and
  nothing did: a daemon restart brought the console, both planes and the consumer back on top of no
  Postgres, no Kafka and no Keycloak. The console answered, the gateway's container
  reported `healthy` for five hours, and nobody could log in. A sentence inside a file called
  `test_…` reads as a checked fact to every later reader, including the one who wrote it. **When a
  test's prose names a second hazard, that hazard needs its own assertion or it has been
  documented into invisibility.**

- **A search that finds nothing looks exactly like a search that found nothing.** `grep` skips a
  file it judges *binary* silently — no message, exit status 1 — and `model-release-panel.ts`
  earned that judgement by writing `join('\0')` as **raw NUL bytes**: valid TypeScript, the same
  string to the compiler, invisible to every text tool. `grep allowed_models` across the console
  then reported that nothing writes which models a use case may call, in the middle of an audit
  whose entire method was searching the source. **Before trusting a negative result, check the
  tool could see the file**; the guard is that no tracked source may carry such a byte
  (`tools/tests/test_source_files_are_readable_as_text.py`).

- **A rule restated on a second surface.** Identical the day it was written, and compared by
  nothing afterwards. *Many:* KIRA read an empty membership list as *"anything goes"* while Gemini
  refused the same request; `:embedContent` bypassed the pre-dispatch gate, then the streaming
  verb bypassed the dispatch conditions, then embedding did again — three times, which is what
  makes it a class; a kill switch guarded by a *visibility* predicate on one plane and an
  *authority* predicate on the other; the thinking-mode parse written out twice; `api` defaulted so
  one surface was right by accident; `is_governance` left on two call sites after `visible_scope`
  was corrected to `is_oversight`, on both of which the *message* already said "oversight";
  `is_catastrophic` copied privately into the redactor and left behind when the shared one grew.
  **Extract the rule, or write the comparison test.**

  *And a **path** is a surface too, when four of them attribute a request.* `require_attribution`
  put the caller on the span; the KIRA surface's own resolver, `pipeline:dryRun` and the console's
  model check each built the same `Attribution`, assigned it to `request.state` and stopped — so
  every figure was on the audit row and the *trace* could be filtered by who only for the one
  surface. Found in a trace view rather than in the source, because in the source all four look
  like the same three lines. **Where a fact has two destinations, make attaching it one act**: the
  split is what let three of the four perform half of it.

  *A direction is a surface too.* `redact_span_query` has kept `?key=` out of the **inbound** span
  since `ADR-0007`, and `AccessLogRedaction` out of the access log — and the moment client
  instrumentation was switched on, the same query string went out on a **client** span with the
  installation's own upstream key in it, because a caller's credential and ours had never been the
  same question. The redaction that already existed covered every place the credential could
  arrive and none of the places it departs. **Ask of any rule about data at a boundary whether the
  boundary has two sides.**

  *A rule can also be stated one layer up and not held below.* `record_request` removed the
  `api="gemini"` default and documented at length why — and `PendingLog.api` and
  `RequestLogService.record` both kept it, with the body-size middleware building a `PendingLog`
  directly. The fix that removes a footgun from the layer where it fired leaves it loaded in every
  layer underneath: **when a default is the defect, remove it everywhere the value is spelled.**

  *And one field along, which is the cheapest place of all to miss it.* `evaluate_rule` guards a
  rule's `kind` against a word this build does not implement — at length, with the reason written
  out — and coerced `target` and `action`, which `consumer.apply` writes out of the **same** Kafka
  payload with no enum and no default, straight through `RuleTarget(...)` and `RuleAction(...)`.
  The consequence was an order of magnitude larger than the guarded case: `tick` has no per-rule
  boundary, so the `ValueError` took the whole round, the watermark deliberately did not move, and
  the next tick re-read the same row and died in the same place — **one unreadable row switched
  detection off for the entire installation, permanently**, with a warning in a log and a console
  still showing every rule as enabled. Ask of a tolerance whether the *neighbouring* field arrived
  through the same door; and give a loop over independent items a boundary, or one of them decides
  for all of them.

- **The same column read in two alphabets.** A narrower relative of the above, and it survives every
  test whose fixture makes the two coincide. `use_case_members.subject` holds a **username** —
  Management emits one and the consumer writes it — and `auth/grants.py` read it against
  `principal.username` while `payloads.py` read it against `principal.subject`, which for an OIDC
  token is a directory id. No console user was ever recognised as an administrator of their own use
  case. It passed for months because the test principals carried no `preferred_username`, so
  `person()` fell back to the subject: **a fixture that makes two things equal is a fixture that
  cannot tell them apart.** Give the stand-in the shape production has.

  *And doing the grep is a different act from writing that the grep is the answer.* This entry has
  ended with **"correct the definition, then grep for the comparison"** since it was written. The
  definition (`scopes.person`) was corrected three rounds before `FRD-613`; the grep, when it was
  finally done, found **eight more readers**, five of them wrong: a `subject` suspension stopped
  one of a person's two credentials, the detector grouped one person into two buckets so sixty
  refusals split thirty/thirty never crossed a threshold of fifty, a payload read was recorded
  under whichever alphabet the reader's credential used, a suspension's author was a directory id
  naming nobody a human can look up, and `own_requests` disagreed with the predicate it is
  documented to match. **A remedy written in an instruction is not a remedy applied**, and the gap
  between the two was three rounds and five live defects. If an entry here ends in a verb, the
  round that adds it should have already done it.

  *The narrowest of them, and the one that shows why a fixture cannot be trusted to be neutral.*
  `own_requests` guards its widening with `if person and person != principal.subject` — an
  optimisation that is a no-op for every caller except the one whose subject **is** their name,
  which is exactly an API-key holder. So the query dropped the clauses reaching rows written in
  the other alphabet while `is_own_request` beside it kept applying them, and the two forms of one
  rule — with a docstring promising a test that they agree — returned different sets. It was found
  by writing that test with a fixture in which the subject and the name differ. **An equality that
  is "obviously" incidental is a branch**, and a fixture that makes two things equal cannot see it.

  *And fixing one reader does not fix the question.* `_member_key` was corrected to `person`;
  `payloads._authority` two functions below, the trace list's restriction and the `mine=true`
  filter went on asking `row.subject == principal.subject`, so a member of a use case that shows
  each member their own requests saw an **empty list with their own rows in the table** and `403`
  on their own prompt. Same module, same alphabet, same test principals — the matrix that covers
  every role and every refusal reason is thirteen parametrised rows in which `subject` and
  `username` are the same string. **Correct the definition, then grep for the comparison**: an
  identity read in two alphabets has as many sites as there are readers, and the one that keeps
  passing is the one whose fixture cannot see the difference.

- **A guard somebody else already applied looks exactly like a guard.** `aira_common.access`
  skips a group path that is not a string, because a `groups` claim that carries an object is a
  realm misconfiguration and used to be an `AttributeError` **inside token validation** — a 500 on
  every request that caller makes rather than a role they do not get. Both planes narrow the claim
  before calling it, so the test written for the tolerance passed with the tolerance deleted, and
  `make mutants` said so. Two things generalise: **test a shared function at the shared function**,
  because a caller's own narrowing is not the property; and a survivor here does not always mean a
  missing test — sometimes it means the rule is enforced in three places and only the two you did
  not write are running.

  *And the mirror image, from the same round.* `test_mutation_anchors` refused the change because
  one anchor matched **two** places: `is_member` and a new `holds_a_grant` were two copies of one
  rule, five minutes old, and its message already said the right answer is to remove the
  duplication rather than widen the anchor. A harness that only reports coverage would have said
  nothing; this one noticed a defect in code written that hour.

- **Returns silently for something unknown.** *Three instances:* `record_to_outbox` for an
  unmapped event type; a seed loop's `continue` past a rule naming a use case it does not create;
  a missing Kafka topic. **An unknown input is an error, not a no-op.**

- **A validator is only as narrow as its anchor.** Python's `$` matches at the end of the string
  *and before a trailing newline*, so `^[a-z0-9-]{1,64}$` accepts `"kundenservice\n"` — on the
  three validators whose entire purpose is that a string carries nothing but a restricted character
  set: the use-case slug (a **primary key on the other plane**, emitted over Kafka and printed into
  every audit row), a Keycloak group path (where the extra character makes the grant match nothing,
  silently, which is the failure that validator exists to catch), and the gateway's own selector
  (which reaches a structured log line). All three had been read and reviewed repeatedly; the
  character that made them wrong is one nobody reads. **Anchor with `\Z` wherever `$` is meant as
  "the end".**

- **An instruction with no destination.** The console said a rule "is changed on that use case"
  and there was no such panel; `docs/deployment/showcase.md` ended with `make down-full-volumes`,
  a target that has never existed; `tools/opencode/README.md` named a use case `make showcase`
  did not create. **`FRD-206` one indirection out** — not a button that 403s, a sentence pointing
  nowhere.

- **A control that is off has not been tested, it has been skipped.** `AIRA_OTEL_ENABLED` defaults
  to false, and switching it on for the first time found **four** defects in the path behind it,
  each of which had been there for as long as the flag: Django instrumented from a place that ran
  before `MIDDLEWARE` existed, so the control plane exported no span ever; a middleware that
  returns silently for ASGI requests without a package the image did not carry; background
  processes that configured no telemetry at all, which made a *just-merged* consumer span inert in
  the deployment; and a migration naming a table that does not exist, which the hermetic tier
  cannot see because it builds its schema from the models with `create_all`.

  Every one of them passed every check the project had, because a disabled feature's code path is
  not reached by anything. **The switch is part of the surface**: a default-off control needs at
  least one test, one environment, or one measured run with it *on*, or "it is off by default" is
  just where the untested code lives.

- **A badge-wearing absent control.** A control displayed as active and doing nothing. *Five:* the
  LLM injection filter set to `block` and passing everything; a `member` rate limit matching an
  OIDC caller's name and therefore nobody; `routerLinkActive` never imported, so the nav marker had
  styled nothing since the shell existed; info hints written as `title=`/`text=` that showed the
  reader nothing; a caching switch on a runtime that reports no cached tokens. **Worse than an
  absent control**, because the reader then trusts the figures beside it.

- **A capability with no way in** — `FRD-206` inverted. IT Security could author a global anomaly
  rule since the day it was written; the console never offered it, so every global rule anywhere
  had been seeded into the database by hand. **Only a control that refuses when used announces
  itself; one with no entry point is silent.**

- **It works on a machine that has already done the thing by hand.** *Five:* `make showcase`
  pulling images four services never build (fine wherever they had been built once); Vault's dev
  server forgetting `secret/aira` on restart, so the demo silently required a prior
  `make vault-init`; Keycloak importing a realm only if it does not exist, so every realm edit
  reached a fresh machine and no other; a `node_modules` copied over a fresh `npm ci`; tests
  naming `gpu-b`, `qwen2.5:3b`, `gemini-flash-latest` — inventory only one machine has. **The tell
  is identical: the assertion is about behaviour and the failure is about inventory.**

  *A sixth, and the tell was in `/readyz` all along.* Three browser tests failed on a stack brought
  back with `make up`: an empty provider listing twice and a `502` once — symptoms that point
  straight at the console and the gateway. `make up` starts infrastructure and observability;
  **Ollama is behind the `demo` profile**, so the stack was complete except for the local model,
  and `/readyz` said so in one line: `degraded: true — upstream reachability: unreachable: local`.
  **Ask the health endpoint what it thinks before reading a failure as a defect** — this project
  built one that distinguishes *degraded* from *down* precisely so that question has an answer, and
  it is the cheapest discriminator between "the code is wrong" and "the machine is missing a part".

  *And the sentence above was written, and still not applied where it was needed.*
  `test_diagnostics.py` stops `aira-ollama` on purpose and starts it again in a `finally`; a run
  interrupted between the two leaves it down, and every later test fails `502 Upstream error:
  ConnectError` — a symptom that points at the gateway, the adapter, and the code that was just
  changed, in that order, and at none of them correctly. `/readyz` had been saying
  `unreachable: local` the whole time, to nobody: the paragraph above was written about the
  **browser** suite, and the layer that actually calls a model had no such check. **A lesson is
  applied where it was learned unless somebody carries it**, and the carrying has to be mechanical
  or it is another sentence — the reading is now a `pytest_report_header`, above the first test
  rather than inferable from the fortieth. Reported and never a refusal: a stack without the `demo`
  profile legitimately has no local model, and failing there would be the wolf-crying check §3
  names.

  *A seventh, and this time it was the **guard** that depended on the inventory.* A browser test for
  a pager whose buttons must not move needed a list long enough to page **and** a search term that
  changes the page count's digit width — both facts about how much demo data the machine holds (917
  use cases here, every one of them debris from earlier runs). It passed against the unfixed console
  on the first try. The property the fix actually establishes is **structural** — nothing sits
  between the two buttons — and a component test says that whatever the data. **When a guard needs
  the world to be a certain size, ask what the fix really established**; the measurement belongs in
  the log, and the invariant belongs in the test.

- **A copied block whose subject changed.** The block is correct; what it is *about* is not.
  `/v1beta/anomalies` restricted `select(AnomalyEvent)` with the trace view's condition over
  `RequestLog`, so SQLAlchemy added a second table to the FROM clause with no join predicate — a
  cartesian product **and** a filter about unrelated rows, failing open. `?may_call=true` was
  decided in `get_queryset`, which DRF also calls from `get_object`, so a list filter widened every
  detail route. Neither is visible in the source: one needs the rendered SQL, the other needs the
  framework's call graph. **When a block moves, ask what it now names** — and prefer a guard that
  reads the artefact (`stmt.get_final_froms()`, the route table) over one that reads the code.

- **A fact applied at each `return` is missing from one of them.** Written down under `FRD-126` and
  `FRD-128`, and then true again: `FRD-309`'s notice reached one of four exits while its own
  docstring said *"called from every exit"*. Fixing it exposed the variant that is worse — the
  condition guarding it tested two of the three cases the docstring named, and the third
  (a `responseSchema` document) was the one it was written for, because that check needs a fact
  about the **request** and the function was only ever handed the response. **A docstring naming
  three cases and a condition testing two is a claim, not a control.**

  *The same shape at its smallest, in a recursion.* `storable` exists to make a payload something a
  `json` column will take, and applied the rule to what a mapping *held* while copying its **keys**
  through untouched — `{str(key): storable(item) …}` — so a key carrying a lone surrogate came out
  of the function whose whole job is that and still could not be encoded, costing the row it was
  written to save. Both of its tests hand it a well-keyed dict. **A rule applied inside a structure
  has to be applied to every part of it**, and the part nobody writes a test for is the one that is
  not the obvious content.

- **A selector narrower than the rule it states.** The stylesheet appeared to have a rule about
  windows and had one about *one* window. *"One question per row, in a window the width of a form"*
  was written after the model editor was reported twice, as
  `.modal--steady .modal__body > form.form-inline > .field` — and both halves of that are narrower
  than the sentence above them: `modal--steady` occurs **once** in the whole console, and
  `.modal__body > form` matches only a window that writes its own markup. Every window built the
  intended way, through the shared `<app-modal>`, projects a wrapper element and has no input for a
  class, so it was outside the rule **by construction** — the reusable path was the excluded one.
  Measured: choosing a per-head scope inserts a paragraph, the wrapping row re-packs around it, and
  four fields move up to 404 px sideways while one grows by 223 px.

  The rule's own comment says the *per-field* version failed because it "has to be remembered at
  every field added afterwards". Per-window is that sentence one level up, and it was forgotten at
  every window there is. **Ask what the smallest thing is that the rule is really about** — here a
  form inside a window, which the stylesheet can see without being told — and scope it to that;
  a rule keyed on a marker somebody must apply is a rule that is applied where it was written and
  nowhere else.

  *And then twice more, in the same rule, for reasons the source cannot show you.* Widened to
  `.modal__body form.form-inline > .field`, it still missed the anomaly rule editor — because the
  `form.` was itself an exemption, written so a `.form-inline` nested in a fieldset could stay a
  row, and it therefore exempts **every** form whose fields live one level down. The one window
  that separated its actions from its fields properly was the one window whose fields never
  stacked. Naming the exemption `fieldset` — a grouping somebody wrote on purpose — fixed the
  spelling and changed nothing on screen, because that row is a **grid**, and a grid item ignores
  `flex-basis`. Given one column, it *still* did not stack: `.field.grow` asks for
  `grid-column: span 2`, which beside a single-column template does not widen a track, it invents
  an implicit second one — `grid-template-columns` computed as `527.703px 286.297px`. **A layout
  rule is believed when the browser has been measured, not when the selector reads correctly**;
  each of those three was invisible in the stylesheet and obvious in `getComputedStyle`. The same round found the counterpart in the same footer: an existing idiom
  (`form-actions__spacer`) used in one file and not in the other, so a badge appearing pushed the
  button that had just been pressed 101 px out from under the cursor.

- **A dead definition is a rule the module appears to have.** `realm_roles` and `_injection_verdict`
  were removed for this reason and it kept recurring: `ratelimit._capacity` documented *"the tests
  and the refusal message both ask how big this bucket is"* and neither did;
  `pipeline/config.TEXT_KEYS` named the step keys holding operator prose while `_bounded` clips
  **every** string, so a reader adding a field would have added it to a list that decides nothing;
  `state.model_catalog_of` was worse than merely unused — it handed back the app-wide catalog where
  every reader wants the per-request one, so the obvious name was a trap. **Delete it, and say in
  its place what the module actually does** — a reader who finds a plausible helper uses it.

  *And the pressure not to.* Removing one of these turned a guard red: `test_app_state_is_typed`
  asserted `len(reads) >= 8` as a vacuity check. A literal floor makes deleting dead code look like
  breaking a guard, and the cheapest way out is to keep the dead code. **A guard against vacuity is
  written as the property, not as a number** — every accessor in the module is one the parser sees.

  *And a check that reports success is not the same as a check that passed.* `make showcase` ran
  green while the demo had stopped demonstrating: `demo_traffic.py` counts a `429` as `refused`,
  and the two requests it drives **in order to be refused by a control** were being refused by a
  *budget* instead. Both readings are "a refusal"; only one of them is the demo. The script's own
  comment had named half the hazard — *"or the demo's most important refusal quietly becomes a
  served request"* — and guarded that direction alone, so the mirror case walked past a sentence
  written about it. **A refusal is not interchangeable with a refusal**: where a script exists to
  show that a *particular* control fired, it has to assert which one, and the two explanations are
  different enough that the failure message can name both. Same shape as *a comment predicting a
  future gap is not a plan*, one step worse: here the comment predicted the gap it was standing in.

- **A name with no definition is a style the page appears to have.** The inverse of the entry
  above, and worse, because it *renders*. A misspelled class is not an error anywhere: Angular does
  not warn, `tsc` never sees a `class` attribute, this project has no ESLint, and the element simply
  gets what the browser gives a bare tag — so the page looks nearly right and survives review, a
  test suite and a demo. Two whole pages opened with `<div class="page"><header class="page__head">`
  and an `<h2 class="page__title">`, **none of which exists in any stylesheet**; every other page
  uses `.stack` and its `gap: 1rem`, and these two had their heading flush against the content at
  0px. A scan of every class in every template against every rule found five more of the same
  thing: the small muted line spelled `.hint` where it is `.field__hint`, a green badge spelled
  `.badge--ok` where it is `.badge--success`, a scroll container spelled `.table-scroll` where it is
  `.table-wrap` — so two tables had none — and a right-aligned actions cell spelled `.right`.
  **A vocabulary with no compiler needs a scan**, and its allow-list is the point: fourteen names
  are on an element for some other reason, each now recorded with that reason instead of being
  indistinguishable from the six that were mistakes.

- **A guard that cannot fail.** *Five, each caught only by breaking it deliberately:* an Angular
  spec using `import.meta.glob` failed to *load* and Vitest reported "0 tests" inside a green run;
  an alignment guard queried a container the case it named did not use; a parser that matched
  nothing; and a **mutation** whose edit had become a no-op — `mode is not ThinkingMode.DISABLED`
  survived `ADR-0021` making that field a plain `str`, so the comparison was always true and the
  harness reported `SURVIVED` about a property that was fully defended. **Every new guard is broken
  on purpose before it is believed** — the first three were silently wrong in the same direction,
  passing; the fourth was wrong in the other, and that is the more expensive one, because it sends
  the next reader to write a test that already exists.

  *A fifth, of the fourth's variety, and it was reported about a **security** property.* `G7` —
  *"an account nobody invited is claimed by nobody, however its name matches"* — was re-anchored
  the day the account-takeover fix landed, onto
  `.filter(user__username__in=[preferred, preferred])`, which is **the same query** as the
  `.filter(user__username=preferred)` it replaces. So the harness reported `SURVIVED` about a rule
  `test_an_uninvited_account_is_claimed_by_nobody` had defended since the hour it was written, on
  the one finding of a 665-property run — which is where a reader's whole attention goes. **A
  mutation is a claim that an edit changes behaviour; write it and watch it be caught, or it is a
  test that has never been observed to fail wearing the costume of the tool that catches those.**

  *And a guard's own description is part of it.* `M29` anchors in `_purge_usecase` and read
  *"deleting a use case keeps its request log"*; a reader matching it to a test found the one about
  **retirement**, which never reaches that code — so a genuine gap sat behind a name that looked
  covered. **Name the guard after the code it edits**, not after the neighbouring concept.

  *And a guard nothing runs is a guard nobody has.* The browser suite's `tsc --noEmit` was added to
  `make lint-frontend` under the words *"a rule only a reviewer enforces is one the next file
  breaks"* — and was added to the **Makefile** and not to CI, where the frontend job
  checks `src/` and the stack job runs Playwright, which transpiles rather than type-checks. The
  next file broke it: `main` was red for a day over one call with one argument of two. A gate has
  two halves, the check and the thing that runs it, and writing the first is the half that feels
  like finishing. **Add a gate to the pipeline in the same change that adds the gate.**

---

## 2. Evidence and the audit trail

- **The log records what was *asked*, not only what was served.** A refused request that leaves no
  row makes rate-limiting, budget and validation failures invisible (`FRD-122`).
- **A fact repeated at every `return` is a fact eventually forgotten at one of them.** Refusals are
  recorded at one exception boundary; the trail is mutable and passed down rather than assembled
  at each exit.
- **An unverifiable claim is not evidence.** A 413 row carries no identity — the credential was
  never verified there, and recording it would let anyone write another system's name into the
  audit trail with one oversized request. A 401 leaves no row at all, on purpose.
- **Unknown is not zero, and zero is not unknown.** Unpriced traffic is counted apart, never as
  zero; a per-person budget answers `null` to a reader it does not bind, because zero is also what
  an untouched allowance looks like; an empty report says whether it was *allowed* to be full
  (`in_scope`).
- **An allow-list, never a deny-list**, for anything persisted from model output or caller content:
  tool calls keep names and counts, pipeline decisions keep a fixed key set. A deny-list starts
  storing whatever is added next.
- **A row describes what *called*.** The credential's owner is on every row; the human who issued
  it is a separate field, because those are two questions and conflating them puts a colleague's
  name beside an agent's traffic.
- **Money is integer nano-units, never a float**, and crosses APIs as a decimal string.
- **Residency is enforced, not intended.** A model outside the allowed regions refuses to start,
  and provider/publisher/region are on every row — so an EU claim is evidence rather than
  configuration.

---

## 3. Configuration, defaults and refusals

- **A convenience default is a production default, one variable away.** Open routes, a published
  password, OIDC with no audience: refusals are *environment-shaped* rather than uniformly
  stricter, because a hardening pass that breaks the demo gets reverted (`ADR-0015`).
- **An exemption is a list, never a `return []`.** The environment shape above was implemented as
  `if is_local(settings): return []`, and `is_local` was true for a declared demo — so one variable
  waived *every* check at once, including authentication. A demo needs the published Compose
  password and a realm with no audience mapper; it does not need its port open to anybody who finds
  it, and the shipped demo uses neither concession. **Waive what the exempt case actually uses,
  named one by one** — a concession nobody asked for is a hole, and a blanket cannot say which is
  which. The same applies to what a *port* exempts: the dev stack published Postgres, Redis, Kafka
  and a dev-mode Vault on `0.0.0.0` because Compose's `"5432:5432"` means that, and nobody chose it.
- **A pre-flight check that blames the file for the checker's environment is one nobody reads.**
  `make config-check` first reported *"2 things this file has to answer for"* when the only
  problem was that the machine running it had no Vault secret-id. Three outcomes have to stay
  apart: the file's own problems, a credential the file deliberately does not carry (Vault's half,
  split on the renderer's own list so the two cannot drift), and *the question could not be asked*.
  Folding the third into either of the others is the permissive stand-in in its most tempting
  form — it makes the tool look decisive.
- **A comment that states a rule does not enforce the line beneath it.** The pull-wait loop in
  `make showcase` carries a paragraph explaining why the seed must not run before the model is on
  disk — directly above `docker inspect … aira-ollama-pull`, a literal that finds nothing on a
  prefixed stack, so the loop breaks immediately and the seed runs mid-download. The prose was
  right, adjacent, and load-bearing for nothing. Same family as *a rule only a reviewer enforces
  is one the next round breaks*; here the reviewer had already written it down.
- **A variable that namespaces some of a thing namespaces none of it.** `AIRA_STACK` prefixed
  every container name so a second stack could run beside the first, and left the Compose project
  and the network pinned to literals. A fixed network name is shared by every project on the
  machine, so the second stack came up healthy, kept an empty database, and read and wrote the
  first one's Postgres — with its seed reporting success. Partial isolation is worse than none:
  none fails at `docker run`, partial fails silently and at the data layer.
- **A cold start is a different system from a warm one, and only one of them is what a colleague
  meets.** Every check that mattered here was green against a machine whose volumes had been
  filling for weeks: config diffs, a stack that came back up, a doctor, 161 browser tests. The
  defect above needed two stacks *running at once* with one of them writing — the state no warm
  check reaches. The earlier "proof" of a second stack had rendered it and started one image,
  which is the half that cannot fail.
- **A deployment file is read by somebody who was not here.** `docker-compose.apps.yml` was 38%
  comment, and most of it was the DEVLOG: dates, defect counts, *missing until a named day*, the
  story of four wiring bugs. None of that helps the person wiring AIRA into their own Keycloak —
  and it sat on top of a third of the file that existed only for the demo, which they also had to
  read to find out it did not apply to them. Two rules, one shape: **the narrative has one home**
  (the same reason `CLAUDE.md` §6 was cut), and **the file you deploy contains only what you
  deploy**. Rewriting every comment to the operational half — what the setting does, what breaks
  if it is wrong — took the three files from 997 to 916 lines with `docker compose config`
  byte-identical.
- **A helper that models another tool agrees with it only until the shape changes.** A test merged
  Compose services with `merged.update(...)` under a docstring saying "the way Compose merges
  them". True while every service lived in exactly one file; the moment an overlay named a service
  to add one `depends_on` edge, the real definition was thrown away and two migration jobs read as
  having no restart policy. And in the same round, a rule written as `theirs <= mine` where it
  meant `mine <= theirs` had passed for six weeks, because the only pair that could tell the two
  apart lived in a file that test did not read. **A model of another system is guarded by the case
  that distinguishes it**, not by the cases both readings agree on.
- **A precedence nobody checks is a preference, not a hierarchy.** The config file was written to
  rank above the deployment, and did — until the moment something disagreed with it, which is the
  only moment the ranking means anything. Compose fills every gap it is given from `${VAR:-…}`, so
  a value left empty, a variable the file does not name, a `.env` edited after rendering and a
  source edited without re-rendering all end identically: the stack starts, healthy, on a value
  nobody chose. **Rank 2 became true when a command could prove it and fail.** Same shape as
  *a rule only a reviewer enforces is one the next round breaks*, one layer down.
- **A check that cries wolf on the supported path is one nobody reads on the day it is right.**
  The first version refused every `.env` without a render stamp — which is the *demo*, where a
  hand-made file is correct and there is nothing above it to disagree with. Strictness that fires
  on the normal case does not add safety, it spends the reader's attention; and the takeover it
  was meant to catch looks identical, because the stamp leaves with the file that carried it. The
  fix was to make the deployment's own intent knowable — a marker beside the file saying *this one
  is config-driven* — so the two cases can be told apart instead of averaged into one warning.
- **A ceiling nobody can account for is a number somebody raises.** Every bound written here says
  what it is made of — the two use-case reads name their two readers, the session ceiling names
  what it was before. A guard whose figure has no derivation is edited the first time it fails
  rather than investigated.

  *And a bound that sits inside the natural spread of what it measures fails by coin toss.* The
  showcase's per-head daily cap was `0.000100` in a list whose docstring says every figure is
  *"calibrated against what the demo traffic actually costs"* — it was the one that was not. A run
  of `demo_traffic.py` cost, measured across eight runs in the audit trail, between 50 600 and
  129 400 nanos: a 0.6B model's verbosity is not a constant, and the cap lay in the middle of the
  range. So `make showcase` worked or did not depending on how chatty the model felt that morning,
  and when it did not it took the two rows the demo exists to show — the injection attempt and the
  embedding batch came back `429 budget_exceeded`, refused by an allowance before the pipeline ever
  ran. **A limit calibrated against a mean is calibrated against nothing**; take the observed
  maximum and say so beside the figure. The tell was in the neighbouring entries: every other cap
  in that list carried its derivation in a comment, and this one carried none.
- **Absent and empty are different answers.** `${VAR:-}` overrode a working default with an empty
  string; Vault ranks above the environment, so writing an unset secret made the empty string win;
  `None` (nothing has said) is not `[]` (somebody released nothing) is not a list — folding the
  first into the second stops every use case on a partially upgraded stack.
- **Undeclared means unsupported. Absence of information is not permission.** Same rule as
  *unpriced is not free*. A vendor's listing that says nothing about a capability serialises to
  `null`, never `false`.
- **An enum member is not a specification.** `throttle` had no rate; `payload_size` had no byte
  figure. A configuration schema is only proved by the code that consumes it.
- **A default on a discriminator stops discriminating** at the first hurried call site.

  *And a validator's default is read against the wrong subject entirely.* Two serializers asked
  `attrs.get(field, DEFAULT)` in `validate()`, which is the **edit** — so on a `PATCH` every check
  below answered about an entity nobody has. `AnomalyRuleSerializer` defaulted `kind` to
  `refusal_rate` and `action` to `alert` whatever the row said, and the three consequences were all
  different: *every* partial edit refused over a `min_sample` the caller had not sent; one that did
  carry it **cleared `action_minutes`** on a `throttle` rule, so the gateway then correctly declined
  to enforce a rule the console still displayed as throttling; and a `spend_spike` threshold below
  100 was accepted, which fires every window for ever. The catalogue's price pair had the same shape
  one field over — correcting one price of a fully priced model was refused, and *clearing* one was
  accepted, so the rule was broken in both directions at once. **Ask what the field will hold after
  the save, not what the request mentioned**; the declaration check five lines below the price pair
  already did, and said why, in the same method. And note where it was found: not in either
  serializer, but in the console, where `rule-form.ts` sends the whole object on every edit under a
  comment explaining that a partial one *"would be checked against the default instead"*. **A
  workaround in one client is not a property of the endpoint** — it is a report nobody filed.
- **Deprecation warns, revocation blocks.** Conflating them removes the ability to announce a
  retirement.
- **Off has to be said out loud** — but only where there is something to switch off. A model that
  declares no thinking is sent no parameter at all; a model that thinks by default and is asked for
  `disabled` must be told explicitly, or the default wins.
- **Fail closed.** The moment a control stops working is the worst moment to stop applying it:
  rate limits fall back to a per-instance bucket, an undetermined classifier verdict blocks, an
  unreachable Vault stops the process. Restoring the old behaviour is available as a *choice*, on
  the audit row.
- **Two refusals that need different actions stay apart** — *"not in the catalog"* (add it) and
  *"not approved"* (release it); *"no capable model"* (operator-fixable, 400) and an outage (502).
- **Where a fact has no copy, look for the join.** The opposite search to the one above, and it
  found more: two halves that are each correct with nothing checking the wire between them. The
  gateway's consumer dropped an event type it did not recognise **in silence**, so a configuration
  change could be recorded, routed and never applied — and the three statements of the event
  vocabulary (`emit`, the topic map, the consumer's chain) had only ever been compared two at a
  time. Management had `makemigrations --check`; the gateway had thirty-nine Alembic revisions and
  nothing comparing them to its models, on the plane where a missing column is a `ProgrammingError`
  on the request path rather than a refusal to start. Both checks found a real defect on their
  first run.
- **A fact that must agree in N places has one owner and a test in both directions.** Met seven
  times now (the Kafka topics twice, the group grant, the capability vocabulary, the realm roles,
  the console's issuer, the local-model seeds) and twice more in one day: the stack's **published
  ports**, which became variables in Compose while the Makefile, `tools/`, both upper test layers
  and the dev proxy went on writing them out — so moving a port to dodge a collision brought the
  stack up correctly and left everything that talks to it knocking on the wrong door; and the
  console's **URL prefixes**, stated in the call sites, the auth interceptor, the nginx template
  and the dev proxy, where a prefix missing from the interceptor sends the request without a token
  and the resulting `401` *logs a valid session out*. One owner, everybody asks, and a guard that
  fails when a second statement appears. **Mirror the owner's resolution exactly** — Compose's
  fallback is nested (`${AIRA_PUBLISH_X:-${AIRA_X:-8001}}`) and reading only the outer name
  reproduced the very bug the owner was written to remove.
- **Documentation is configuration when it names a variable.** `docs/CONFIGURATION.md` listed seven
  `AIRA_VAULT_*` variables; the code reads `VAULT_ADDR` and friends, unprefixed. An operator
  following the reference to switch Vault on would have set names nothing reads, seen no error, and
  had every credential come from the environment — the failure `secrets_state()` exists to expose,
  after it cost three days. Nine more settings were missing, five of them the Kafka SASL/TLS family
  a production deployment cannot do without. A reference is the copy nobody opens *until it
  matters*; compare it to the settings classes in both directions, as with every other pair.
- **A rule is not its mechanism, and refusing is not the only way to keep one.** *No silent drop*
  was implemented as `extra="forbid"`, which made the compatibility surface refuse the traffic it
  exists to accept: a real chatbot got `422` on every call, over fields that change no answer. The
  fix is not to relax the rule but to ask what ignoring the field would **do** — drop something the
  caller set (`conversationHistory` for `conversation_history`: refuse, naming the spelling taken)
  or nothing at all (accept, and name it in a header on every exit). Where the answer is *invisibly
  wrong* the strictness stays: a `responseSchema` field is a constraint on the output, so that
  vocabulary is still closed. And the same audit turns up the mirror mistake — a field refused only
  because it was **missing from the list**: `additionalProperties` means the same thing on both
  sides of the translation, and "not a field of the supported vocabulary" was simply untrue.
- **A value your enum has is not a value the wire has.** `minimal` is a thinking level here and on
  exactly one vendor's newest family; every other OpenAI-compatible server answers `400 invalid
  value`, so the least-thinking mode was unreachable everywhere. Sending the adjacent level that
  exists is not the rounding refused for `limited` — there the caller named a number, here the
  dialect has no number to name. Say which of the two a translation is, in the table itself.

---

## 4. Models, providers and dispatch

- **Asking a model for one word means reading one word, not searching for it.** The injection
  classifier gets this right and says why: *"neither word, or **both** … picking a winner would be
  a precedence rule nobody can predict from outside"* — so an ambiguous reply is `UNDETERMINED`
  and the step decides what to do about it. The router in the same file did the opposite, matching
  a category by `name.upper() in answer` — a substring, anywhere, first entry in the operator's
  list wins. Measured: `NONE`, the instruction's **own** word for *no category fits*, selected a
  category named `one`; *"not code — use general"* selected `code`, the one the model rejected;
  *"general or code"* selected `code` again. Not a security hole — the release and the approval
  still bound where a routed request lands — and the feature defeated, since a `model_route` exists
  so a cheap question reaches a cheap model.

  Three things a reply parser owes its protocol. Take the **exact** answer first, because a model
  that obeyed the instruction must not be at the mercy of a rule written for one that did not (and
  a category may be called `c++`, which no word boundary can express). Match **whole words**, or
  every short name is a wildcard. And require **exactly one** match: a reply naming two has not
  answered the question, and deserves the same honest outcome as a reply naming none. The reserved
  word falls out for free — `NONE` contains `one` only if you were searching.

- **A vendor's capability flag is a claim, not evidence, and one successful call is not a
  capability.** `ollama show` lists `tools` for a model that returns the JSON as prose; a 0.5B
  model called correctly once, then answered in prose, then invented a parameter.
- **A capability belongs to a model — not to a family, a vendor or a runtime.** A seed writing one
  declaration for "whatever is configured" is the mechanism that turns a measurement into an
  assumption. It has produced two defects here (`minimal`, then `tools`).
- **Listed is not usable.** `gemini-2.5-flash` is listed and refuses every request from a new key —
  hence *declared · served · reachable* as three separate answers, and `reachable: null` is never
  reported as healthy.
- **Transport × dialect × model identity.** A transport reaches the cloud, a dialect owns the API
  shape, and **the caller's model name is never the platform's addressing** — an Azure *deployment*
  has no price, so attributing spend to it fails silently rather than loudly (`ADR-0011`).
  *Which means a chain that changes the name must change the address with it.*
  `dispatch_with_fallback` re-pointed a request with `model_copy(update={"model": model})`, and
  `addressing` — resolved once, before the chain, from the routed model — travelled unchanged to
  every hop after the first. Invisible in every dialect where a name *is* the whole address, and on
  Vertex `addressing` is the region list: a fallback catalogued in `europe-west4` was addressed at
  the primary's `europe-west1`, and behind a primary carrying no address at all it was refused with
  *"says no region"*, which the catalogue contradicts. The lookup that fetched two thirds of the
  declaration and left the third behind is the tell: **when two facts must move together, return
  them as one value.**
- **Capability flags say *whether*, never *how*.** Three vendors do structured output by three
  unrelated mechanisms; the catalog never learns which.
- **A chain skips an incapable candidate; it never degrades the request.** A stripped attachment
  returns a confident answer about a document the model never saw, with a 200. Checked **per hop**,
  because a check before routing protects nothing.
- **A health check must not be able to take down a healthy service**, and a probe is a listing,
  never a generation — a "does this work" button must not be what wakes a scaled-to-zero model.
- **Refusal, not best effort.** `seed` on a candidate that cannot express it answers perfectly and
  simply is not reproducible, and nothing in the response says so.

---

## 5. Layering

- **A surface parses; the layer decides.** Both halves of the request path have one owner —
  `prepare_for_dispatch` before dispatch, `accounting` after it. Sharing the *steps* is not sharing
  the *sequence*, and every guarantee that layer makes is a guarantee about the **order**.
- **A page is a parent plus panels.** The parent loads and owns the tab bar; each panel owns its
  form state. A new tab is a new child, never another block in the parent.
- **One definition both planes read.** Role sets, access rules and money live in `aira_common`
  precisely so neither plane restates them.
- **An unreachable helper is a rule the code claims and does not have** — a reader concludes the
  system still honours it. Same for a comment describing a rule the code does not implement, and
  for a **parameter nothing passes**: `OpenAITransport.api_key` offered a credential every call
  site left empty, contradicting `FRD-123` §8's decision that this transport has none. All three
  have cost real defects here.
- **The reader is not the request path.** A cache that decides what a request *may do* has a
  request's lifetime, never the application's: an application-scoped catalog would keep answering
  an old declaration after somebody replaced it, invisibly, which is `FRD-307` inverted. Within one
  request the answer must not change anyway — the pre-dispatch checks and the dispatch that follows
  are supposed to be deciding about the same declaration, and re-reading was how they could quietly
  disagree.
- **Per-process state is correct until there are two processes.** The anomaly evaluator held its
  touched scopes, its cooldown and its right to run in memory; at N=1 every one of those is right,
  and at N=2 they produce duplicate findings, duplicate suspensions, and an evaluator that measures
  only the traffic it happened to serve — *while each instance is individually inside its own
  cooldown*, so the mechanism against repeat firing is the one thing that cannot see the repeat.
  The same state made a **restart** re-fire everything, which a rolling update turns from rare into
  routine. **Ask of any in-memory field: what does a second copy of this process believe?**
- **A read path that writes is a read path that cannot scale.** Reconciling a caller's groups on
  every request wrote eight rows in the steady state, for a `GET`. Compare first and write only the
  difference; agreeing with the database should be free.
- **An ambiguous routing table refuses to boot.** With three adapters, last-registration-wins is a
  silent choice of region and credential.
- **A field that names one of something narrows a decision that deliberately allowed several.**
  A pipeline briefly carried a `start_model` so the question catalogue had somewhere to begin. It
  reads as *this is the model this use case uses* — and a use case releases several models on
  purpose, so the field quietly contradicted the release. It also made the wrong thing the
  precondition: un-runnable for want of a pipeline field rather than for want of a model. The
  question the field answered belonged to the **run**, not to the configuration; ask it there, and
  bound it by the permission that already existed.
- **A feature that must edit a governance decision to work is fighting the model it is built on.**
  The question catalogue asked the caller for a model, `FRD-308` then refused it because the use
  case had never been released that model, so the runner wrote `allowed_models` on the way past
  (`_release_for_testing`). A review flagged the *role set* allowed to do it, which was never the
  problem: the write itself was the defect, and it was a symptom of the feature owning the wrong
  subject. The fix was not a narrower permission but asking what a run is *about* — a use case, whose
  pipeline decides the model (`ADR-0020`) — after which nothing needed writing at all. When a
  feature reaches around a rule another layer owns, suspect the feature's subject before its
  permissions.
- **A class permission guards the door; an object permission guards the room.** `MayRunTests`
  answers *"is there any use case this person could run"* — the right question for offering a
  screen, and the wrong one for starting a run, because an administrator of one use case passes it
  and can then name somebody else's slug. Every endpoint asks again, per object. The composition is
  also guarded per half (`Q1f`, `Q1g`): a rule made of two necessary conditions and tested only as
  a whole is a rule that can quietly lose one.

---

## 6. The console

- **An action nobody can carry out is worse than an absent one.** Absent reads as a boundary;
  present-and-failing reads as a broken system, and the reader then distrusts the figures on the
  same page. Every withheld action names who performs it.
- **Read-only means inert, not un-saveable.** Hiding Save alone lets somebody rearrange a pipeline
  for nothing.
- **The console must not answer a question only the server can answer.** Object-level permission is
  not in the token, so the object says what this caller may do — computed with **the same
  predicates the viewset enforces with**, and proved by an agreement test that attempts the
  request.
- **A control that starts a request must survive that request.** A search box inside the `@else` of
  `@if (loading())` tears itself down on the second keystroke.
- **A field the detail panel prints is a field the form must offer — and a field nothing reads is
  not printed at all.** One mistake from two sides. The model panel showed *"KIRA id —"* and no
  form asked for one, so every model catalogued from the console carried none: addressable on the
  Gemini surface, invisible on the KIRA one, refused with `MODEL_NOT_FOUND` and no hint as to why.
  The attachment estimate was the same field with a budget behind it — displayed when the API had
  set one, settable nowhere, and read by the gateway as **zero**, so a 20 000-token document was
  reserved for as if it were a sentence. Displaying a value is a promise that it can be set; where
  it need not be, give it a server-side default rather than a dash, because a reader cannot tell an
  empty field from an inapplicable one. The other side is `underlying_model` and `addressing`:
  stored, shipped to the gateway, dropped before any decision reads them, and printed among
  Provider and Platform as though they were configuration — giving *those* inputs would be the
  same defect wearing the other mask, a control somebody sets that changes nothing.
  **An upsert makes it worse than unreachable.** Budgets and rate limits are written by a POST
  that keys on their scope, so the same call creates and edits — and the handler defaulted
  `enabled` to true whenever a body did not mention it, which the console's body never did.
  Disabling a budget and then changing its cap from the console re-armed it, silently. A field the
  writer omits is not neutral against an upsert: it is a value being *set*.
  **The durable form of this rule is a comparison test**, in both directions: every writable field
  reaches the payload, and every field the *decision object* carries is enterable
  (`test_every_model_control_is_reachable.py`,
  `test_every_use_case_control_is_reachable.py`). Read the **typed literal** a screen builds, and
  scope the check to the file that owns it — a first version counted one tab's payload towards
  another's and passed with the field deleted. **Point it at what is *sent*, not at any literal of
  the right type**: the anomaly-rule check matched the blank-form template as readily as the
  payload, so a default answered for a control. Both times the tell was identical — the guard kept
  passing under the mutation it was written to catch, which is why a guard is not finished until it
  has been seen to fail.
- **A label is one string.** `Spend limit` on one line and `({{ unit }})` inside an `@if` on the
  next renders as `Spend limit  (CHF)`: a control-flow block contributes its own whitespace, and a
  formatter will put it there whatever the author wrote. Interpolate a label the component
  assembled — the moment a caption is built *across* template structure, its spacing belongs to the
  formatter rather than to anybody's intent.

- **A question is a claim that the answer is unknown.** The model editor asked eight things to add
  a model and five of them — provider, publisher, platform, hosting, the KIRA id — had been
  answered one screen earlier, by choosing the provider. An empty box beside a fact the software
  already holds does not read as *confirm this*; it reads as *this is yours to decide*, and the
  person who cannot decide it stops there. Worse, it invites a **different** answer from the one
  the system knows. The owner's report is the shape to remember: *"too many options where you do
  not know exactly what you are doing — if somebody other than me is to add a model, that person
  will not understand the screen."* State a known fact as a sentence with a way to change it, and
  keep the fields for what nothing else can answer. **This is the mirror of the rule above** — a
  field the panel prints must be settable; a fact the flow already knows must not be asked — and
  the pair has one subject: a control is a statement about who holds the answer. **`textContent` is
  not a rendering**, and this rule's own summary line proved it: the sentence assembled correctly,
  the test asserting it character for character passed, and the browser drew
  `Lives on **vertex** , speaking the google dialect .` — because the container was a flex row with
  a `gap`, and a `<strong>` mid-sentence is a flex item. A gap is not a character. Where the defect
  *is* the layout, only a picture or a measured box is evidence; the same goes for a form whose
  fields stopped standing under one another when there were too few left to fill a wrapping row.
  **`flex: 1` is `flex-basis: 0`, which contributes nothing to a wrap calculation** — an item with
  it never moves to a line of its own; it is squeezed, and squeezes its neighbours. Three defects
  in this one console: a note rendered 67 px wide and 4818 px tall, a growing field collapsed to
  30 px, and a footer message that cramped Save the moment its dialog was narrowed. Each was
  latent at the width it was written at, which is the tell: **a layout rule that only holds at one
  width is not a rule**, so measure it at two.
  **And fix a layout per window, not per field** — the first attempt tagged each field with a class,
  which reached one tab of three and was reported again the same day as *"the window is still
  stretched"*. A rule carried on every element is one the next element added will not carry; a rule
  scoped to the container cannot be forgotten. The guard has the same shape: an assertion about
  *rows* had no rows left to look at and would have gone on passing by finding nothing, so it
  became an assertion about the stack — **on every tab**, which is precisely the hole the per-field
  fix fell into.
  Two corollaries paid for on the way: a form that hides fields once they are known must **latch
  them open while somebody is using them**, or a rule about what a form knows fires while they are
  telling it; and removing a manual route because a better one exists silently removes the case the
  better one cannot reach — here, naming a model a provider's listing does not carry. A third, from
  the redundancy that appears when you state a fact somewhere new: **delete the old statement.**
  The import note still opened with *"Filled in from mock: provider, dialect"* one line under a
  sentence saying exactly that, and two statements of one fact read as two facts.
- **A field nobody can fill is worse than a missing feature — and a guess that leaves the process
  is worse again.** The model catalogue asked for a token budget per thinking level, in a control
  whose screen-reader label was literally *"How many thinking tokens `medium` means"*. No vendor
  publishes that number, so the honest answers were all wrong; and the number did not stay in the
  database, it went upstream as a **ceiling on the model's reasoning**, where a typed
  `medium = 2000` silently truncates work that needed twenty thousand. Two separate tests, and a
  control has to pass both: *can the person filling this in know the answer*, and *what does a
  wrong answer do to a request*. When the answer to the first is "no", deriving one is not the fix
  — a fraction of a range is an invented number with a formula in front of it. **Ask what the
  vendor already accepts, store that, and let the vendor be the authority on whether it works**: a
  free-text word plus one capped request that asks the model beats any list this repository can
  keep current. The same round produced the corollary about *whose* number it is: `limited` — where
  the **caller** names a budget — was never the problem, because the person naming it is the person
  who knows.
- **Read-only is a control, greyed — never prose.** The models released to a use case were shown
  to a reader who may not change them as a paragraph of `<code>` chips. *"It does not look like a
  control, so the developer will not even read it"* — the one piece of configuration they most
  need, rendered as the one thing on the page that reads as decoration. Show the same control,
  disabled: its greyed state says *you may look, not change* without a sentence having to. What is
  removed is only what **acts** — a remove button, a list toggle — and a component that drops its
  whole field when disabled is how a control becomes prose in the first place.
- **A `<select>` is sized by its widest option, and `max-width: 100%` does not stop it.** That
  percentage is measured against a parent which, as a flex item, is itself sizing to content —
  flex items default to `min-width: auto`, which is exactly what carries an intrinsic width
  upward. A long use-case name pushed the page past the window and onto a second monitor. Cap the
  control **and** give the container `min-width: 0`; one without the other does nothing.
- **Unknown is never rendered as zero**, and every figure says what it counts. The variety that
  costs most is a sentence about somebody's **access**: the Runs panel branched on an empty list
  that every load starts in, so until the answer arrived it told every reader *"there is no use
  case you may send requests to"* — including the readers for whom that is false, who would go and
  ask to be added to a group they are already in. A figure shown early is wrong for a moment; a
  statement about permission shown early sends somebody somewhere.
- **A live view must stop, be visible, and never stack** — those are the three ways a polling view
  fails.
- **Cursor paging over an appending table.** An offset page shows one row twice and misses another
  while somebody reads, invisibly.

- **A claim no test can reach is a claim that will be wrong.** Reading every document against the
  code found six false statements, and they sorted themselves perfectly: every kind of claim with a
  guard behind it was clean — relative links, `make` targets, `AIRA_*` setting names, FRD status
  headers — and every kind without one was wrong. Span attribute names that nothing sets
  (`aira.thinking.mode`, `auth.method`), metrics promised in an FRD's *Observability* section that
  were never built, a gap analysis still calling a shipped feature *"not scheduled"*, and a
  `TESTING.md` line claiming an enforced **100% coverage gate** where the gates are floors of
  90–93%. None of those is exotic; each is simply the kind of sentence a person writes once and
  nobody re-reads. **Prefer a claim a test can hold**, and where that is impossible — an
  Observability section, a snapshot document — write the date it was last read against the code, so
  the next reader knows what they are trusting.

- **A delete that the compromised party can perform is not a control.** Use-case deletion was open
  to the use-case administrator, which is exactly who an investigation would be about, and it took
  the configuration record with it — leaving the audit traffic intact and *illegible*, since a row
  names a use case by slug and nothing else. Retiring and purging are **two acts for two roles**,
  and the gap between them is a decision period rather than a retention period: 30 days means
  erasing a record requires deliberately coming back for it. Ask of any destructive action: *who
  benefits from it, and are they the ones who can perform it?*
- **A tombstone is not absence, and the difference is what makes a check possible.** The gateway
  cannot refuse an *unknown* use case — Kafka orders nothing, so one that has not arrived yet looks
  exactly like one that was deleted. It can refuse a **retired** one, because that row is positive
  knowledge which can only exist after the use case was known. Keeping the row also kept two
  answers that had been silently changing at the moment of deletion: the retention period the
  installation had promised, and whether a missing payload was never stored or had expired. **When
  a delete makes a downstream answer change rather than disappear, that is the bug.**

- **Widening a field from one to many is a search, not an edit.** Making a model's region a list
  meant reading the twelve places that consumed it — and three were **already wrong**, in ways that
  the new feature would have promoted from occasional to systematic: an audit row taking the region
  from *configuration* rather than from the request that happened, a stream leaked on every refused
  connection because `__aexit__` does not run when `__aenter__` raises, and a guard asserting a
  field was read by nothing a day after it gained a reader. None was found by thinking about the
  feature; all three came from asking *what reads this today* before writing anything. **Ask it of
  every "make it a list" — the answer is where the bodies are.**

---

## 7. Tests

- **A green test proves nothing on its own.** It proves the code and the test agree, which they
  inevitably do when both came from the same idea. **Prove a test can fail**: break the property,
  watch it go red, restore. `make mutants` does this for the properties worth keeping.
- **An ADR that cites a control as an existing fact is not a check that it exists.** `ADR-0021` §5
  named `Upstream.thinking_modes` in passing — *"beside the existing `thinking_modes`"* — and the
  declaration was read by nothing, so the document described a control the code did not have and
  every reader after it inherited the belief. The same shape as `CLAUDE.md` claiming an ESLint this
  repository never had: **the more confidently a rule is referenced in passing, the less likely
  anybody checks it.** When a decision record cites a mechanism, grep for its reader.

  *And an FRD's own §10a is the most authoritative place to be wrong.* `FRD-117` FR-5 —
  *"outgoing calls are traced, HTTPX and SQLAlchemy instrumented"* — was specified in §5.3,
  recorded in §10a as *"FR-1 through FR-6"* built, and repeated in `GAP-ANALYSIS.md` row 21.
  Neither package was a declared dependency of anything. A gateway's most valuable span is the
  **upstream call**, and for three weeks the trace of a nine-second request was one span nine
  seconds long with nothing underneath it. Two things generalise past the instrumentation: a
  *"what was built"* section is a claim like any other and rots the same way, and the same
  paragraph named a mechanism the library does not have (*"statement text hidden"* is not an
  option) — **when a document describes how a third-party tool is configured, read the tool.**
- **A guard that asserts an *absence* goes vacuous the moment the spelling changes**, and passes
  louder than ever. Three were found in one afternoon: `assert "curl -fsS http://localhost:8001/
  readyz" not in showcase` and `assert "4200" in target` both stopped checking anything when the
  Makefile's addresses became `$(GATEWAY_URL)`, and would have passed with the weaker readiness
  loop right back in. Assert through the **same name the source uses** — the variable, the
  constant, the shape — never through a rendering of it. And a test whose setup never reaches the
  path it is named after is the same failure wearing a positive assertion: an
  `httpx.Response(400, json=…)` has its body already read, so a streaming test built on one passed
  whether or not the fix under test was present.
- **A check that quietly narrows its own scope reports green about the part it kept.** A `try`/
  `except ImportError` around the second of two settings classes made a documentation guard
  measure one plane and call it the product. If the input cannot be loaded, fail — do not check
  half and say nothing.
- **A test that skips when the data is inconvenient reports green about nothing.**
- **Tests without a mutation are a claim, not a proof — and the security controls are where that
  gap hides.** A sweep for source files no mutation touches found the bound on failed
  authentications and the Kafka SASL/TLS wiring: both well covered, neither ever broken to watch a
  test notice. Six properties, six mutations, six caught — but nobody knew that until it was tried.
- **A subset that passes is not a suite that passes.** Including *a layer* as the subset: the model
  editor was split into three tabs, verified by hand in a browser, and shipped — and nine browser
  specs had been red ever since, because they open the editor and go straight to a field. A layout
  change is precisely what the browser layer exists for, and it was the layer not run.
- **A gate nobody runs is a gate that is already red.** The Angular branch-coverage threshold had
  been failing on `main` at 91.37 % against 92 — so `make ci` was red before any change, and every
  local run since had been reading the tail of the output rather than its exit code. Fixed by
  covering the thing that was uncovered, never by moving the threshold: `describe()`, which writes
  the sentence an operator reads for every step outcome, had ten branches and two tests.
- **A test asserting an *absence* is defended by the mutation that *adds*, never the one that
  removes.**
- **A stand-in more permissive than the thing it replaces** proves the permission, not the rule —
  reuse the real method where you can, and mark a test double as one. **Emptier counts as more
  permissive**, and that is the costume it keeps coming back in: a double returning `object()`
  where the real adapter returns a response *with usage* made "the audit row carries what the
  answer reported" a property no test could lose, because the harness never produced one. Both
  times a mutation run found it and no reviewer would have.

  *And the same mismatch in the other direction is loud rather than silent, which is not the same
  as harmless.* Seven spec files stubbed `MeService` with `{get: () => of(…)}`; the service grew one
  signal and **144 tests failed at once** on `ctx.currency is not a function` — a template error in
  files whose subject was budgets, security and the model catalog. Nothing was wrong with any of
  them. A double is a promise to be the same shape, and a promise kept only until the shape changes
  is how one added member costs an afternoon of reading stack traces that name the wrong thing.
- **A file that offers a knob must be checked against the thing that turns it.** `config/`
  rendered 86 variables into the environment both planes read, and **47 reached no container**: 45
  that no compose file interpolated, the rest overridden by a literal there. Nothing failed —
  `enforce_budgets: false` simply went on enforcing. The compose file already carried the sentence
  four times, about four different knobs it had met one at a time: *a knob that is not wired is
  worse than an absent one, somebody turns it and believes the result.* **Offering a knob is a
  claim, and a claim needs a guard**: every name an example renders must be one the deployment
  takes from the environment, checked statically so it fails without a stack.

  *And presence is not effect.* The proof that closed it was a knob set to a value and a request
  refused for it — `AIRA_MAX_REQUEST_BYTES=2048`, a 5 000-byte body, `413`; without it the same
  body reaches authentication and returns `401`; before the wiring the name was not in the compose
  files at all.

- **A configuration file is not tested until something is started from it.** Every check on the
  `config/` examples validated *names and values* — against the settings classes, against the
  parsers, and against 196 deliberate mutations. Rendering one and bringing the stack up on it
  found three things none of that could reach: an audience the demo realm had no mapper for, so
  the console looped for ever between the authorization endpoint and its redirect; a repair tool
  that re-imports through the admin API and therefore stores `${NAME:default}` **literally**, where
  Keycloak's own start-up import would have expanded it; and the reason neither failed loudly —
  every `${VAR:-default}` in a compose file catches a missing variable, so **nothing breaks, and
  something other than what the file says is used**. For the console that was the realm its image
  was built against.

  The first of the three is the sharpest: it is the trap this repository had documented three days
  earlier, in `INTEGRATIONS.md` §2, walked into by the person who wrote it down.

- **A teardown that clears instead of removing.** Two fixtures spied on the in-process event hook
  and ended with `events._subscribers.clear()` — which also removed the subscriber
  `OutboxConfig.ready()` registers at start-up. The list is module-global and start-up happens
  once, so from the first use of either fixture **no use-case event reached the outbox for the rest
  of the session**. Every later test asserting an outbox row was testing nothing, and the one that
  finally failed was a command test three files away, whose subject was not events at all.

  The shape is the exemption list's, one object along: *remove what you added, never reset what you
  found.* And it is the reason the failure is expensive rather than merely wrong — a clear() makes
  a **later, unrelated** test the one that reports it, so the reader starts at the wrong end.

- **A suite that fills the installation it runs against.** The browser suite called
  `createUseCase` ninety times and removed nothing, and the demo reached **1734 use cases in
  Management and 1946 in the gateway's read-model** — four of them the demo's. It had already been
  learned one object along: `removeModel` exists with the sentence *"test residue makes a real
  figure meaningless, and the residue never stops accumulating"*, because the console counts models
  with no price over the whole catalogue. The same is true of every count, every list and every
  register a suite can grow.

  Two rules came out of cleaning it up, and both are about **what a clean-up may touch**. Remove
  what the run *wrote down*, never what *matches a shape* — a person may name a use case anything,
  and this demo holds two that a person made. And tidy **whether or not the suite passed**: a
  recipe written as two lines is abandoned at the first failure, so the run that leaves the most
  behind is the only one that never cleans up.

  *And the tidying breaks the tests that were living off the mess.* A guard here walked five pages
  of the register, which needed 125 use cases it had never created; with thirteen it went red for
  want of a pager. Rewritten to filter instead, it passed with the fix removed, because thirteen
  short rows are all about as wide as each other. A guard must **make the data it measures** — this
  one creates a single row wider than any column — or it is measuring the database's history.

- **A test whose setup never reaches the path it is named after.** SQLite enforces no column
  lengths; `TestClient` buffers a streamed body before you can hang up; a *cold* budget counter
  seeds from Postgres and hides a missing write; a fixture whose use case may call nothing can only
  ever exercise the exempt test double; an assertion that a line is **absent** passed while a
  mutation rendered the same claim under a different label — assert the element, not the wording;
  a panel test that left the usage map empty proved the line was missing because nothing had been
  *measured*, not because the scope was wrong. Four of these were found by the harness in one
  session, two of them in tests written minutes earlier.
- **A test that assumes what the installation is *not* configured for reads the developer's
  machine too.** *"A model nobody serves says so"* asked about `gemini-2.5-pro`, on the assumption
  that this stack had no Vertex key. One was configured, and the test then asserted the opposite of
  what happens — and worse, a catalogued model becomes servable through its *provider* without
  being listed in `AIRA_VERTEX_MODELS` at all, so **no real model name is safe to assume unserved**.
  Ask about something nothing can claim: a name no adapter registers reaches the same branch and
  cannot be falsified by a credential somebody adds next month.
- **A unit test that reads the developer's machine is a test about that machine** — a `.env` on
  disk, a stack that happens to be running, a Redis holding last run's bucket. *That last one is
  not hypothetical:* `redis_url` defaults to the address `make up` publishes, so the hermetic
  gateway suite shared a **durable** bucket store with the developer and with its own earlier runs,
  and a rate-limit test was refused its first request. Green in CI, which has no Redis, and green
  on a machine with the stack down — so it took the mutation harness's **red baseline** to report
  it, which is the report that says nothing at all. **Make the hermetic layer hermetic by
  construction**, in a fixture, and guard the fixture with a test: one that does nothing visible is
  the kind that gets tidied away.
- **A harness that configures a service differently from production tests a different service.**
- **Assert behaviour, not wire bodies**, and never assert the *model's* answer — that tests the
  model and flakes.
- **A test whose verdict turns on how fast something answered is measuring the machine.** A refill
  rate half a second wide is a coin toss under load.
- **A comparison that answers the same for both sides proves neither.** A test written as *"the
  administrator may, the plain member may not"* read "may not" for both — twice, and for two
  different reasons: `option[value=…]` asks about the attribute where Angular's `[value]` binding
  sets the property, and a `count()` after `goto` samples a page that has not rendered. Both look
  exactly like the rule working. **Assert that the positive half is positive**, in the same test,
  or a comparison degenerates into a tautology the moment its locator stops matching. And when the
  two sides are two people, **log the first one out**: an SSO session hands the second login back
  to the first user, and the test would pass the day the rule broke.
- **`count()` is a sample, not a wait.** Three instances: a helper that branched on it chose a
  selector that could never match and waited 45 s for it; a panel asserted "says neither" while its
  data was still arriving; a heading that renders before the list under it. Wait for the thing —
  `expect(a.or(b)).toBeVisible()` — then count.
- **An unqualified role query is a query about a page that has one of something.** Opening a
  disclosure put a second tab strip on the page and `getByRole('tabpanel')` became ambiguous. Name
  what you mean.
- **An FRD that names its own gap is not a test.** `FRD-504` §5.3 specified two modes and called
  the unbuilt one *"the first honest measurement we would have of whether the injection filter earns
  its place"*. One mode shipped; it was the other one, and for a year the catalogue never exercised
  a filter, a router or a redactor. The document had been accurate the whole time — nothing ever
  read it against the code. A specification describes a gap; only something that runs reports one.
- **A mutation anchor must exist exactly once**, be re-anchored when the code moves, and be
  removed when the rule is deleted — an anchor pointing at a grave reports green about nothing.
  *And a rename is where they go stale in bulk*: changing what a run is *about* moved one anchor
  from `order_by("model", …)` to `order_by("use_case", …)` while the property it guards —  a
  standing is the latest run, never a total — did not change a word.
  **It must also be anchored where the property actually lives**, which for a database constraint
  is the *migration*: two mutations breaking `Meta.constraints` in `models.py` survived, because
  the test database is built from migrations and the model's declaration is never consulted. The
  survival was the finding rather than a nuisance — a `Meta.constraints` entry with no migration
  is enforced by nothing at all, in tests and in production alike, and a mutation anchored there
  would have reported a guard over a rule the database has never heard of.
- **A property guarded twice cannot be a mutation**, and that is not a reason to weaken the guard.
- **Each layer sees what the one below structurally cannot.** A dropped socket *cancels* a task
  where an in-process close raises `GeneratorExit`; two credentials can only disagree where both
  are real — a stubbed validator is exactly where a subject that "looks nothing like a username"
  can quietly come to look like one. Anything needing a user token belongs in `e2e/`.
