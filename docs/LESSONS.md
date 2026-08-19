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

- **Two correct halves and no wire.** Both ends exist and nothing joins them, so every review of
  either end passes. *Five instances:* `record_to_outbox` had no topic for an event type;
  `payload_size` counted bytes into a column nothing wrote; the seed wrote a catalog and emitted
  no event; a `throttle` produced a value the limiter could not consume; `FRD-116` shipped Vault
  and no container was given `VAULT_ADDR` for three days. **Test the wire, not the ends.**

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
- **Recognise a shape, do not remember a list of names.** `strip_attachments` matched wrapper keys
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
- **A default argument is a silent one** — the wire shape's worst variant, because there is nothing
  *missing* to notice. `resolve()` had taken a `direct` argument since the vocabulary was written,
  with tests of its own; both planes called it with two arguments, so a grant naming a person was
  specified, replicated to the gateway and read by nobody (`FRD-209` FR-6). The same day, in the
  fix for it: deleting `username=username` from the write survived the whole suite — the column,
  the grouping and the panel were covered and the step that fills them was not. A missing map entry
  at least leaves a gap somebody can see; an unpassed parameter looks exactly like a call.

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

- **The same column read in two alphabets.** A narrower relative of the above, and it survives every
  test whose fixture makes the two coincide. `use_case_members.subject` holds a **username** —
  Management emits one and the consumer writes it — and `auth/grants.py` read it against
  `principal.username` while `payloads.py` read it against `principal.subject`, which for an OIDC
  token is a directory id. No console user was ever recognised as an administrator of their own use
  case. It passed for months because the test principals carried no `preferred_username`, so
  `person()` fell back to the subject: **a fixture that makes two things equal is a fixture that
  cannot tell them apart.** Give the stand-in the shape production has.

- **Returns silently for something unknown.** *Three instances:* `record_to_outbox` for an
  unmapped event type; a seed loop's `continue` past a rule naming a use case it does not create;
  a missing Kafka topic. **An unknown input is an error, not a no-op.**

- **An instruction with no destination.** The console said a rule "is changed on that use case"
  and there was no such panel; `docs/deployment/showcase.md` ended with `make down-full-volumes`,
  a target that has never existed; `tools/opencode/README.md` named a use case `make showcase`
  did not create. **`FRD-206` one indirection out** — not a button that 403s, a sentence pointing
  nowhere.

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

- **A guard that cannot fail.** *Three, each caught only by breaking it deliberately:* an Angular
  spec using `import.meta.glob` failed to *load* and Vitest reported "0 tests" inside a green run;
  an alignment guard queried a container the case it named did not use; a parser that matched
  nothing. **Every new guard is broken on purpose before it is believed** — all three were
  silently wrong in the same direction: passing.

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
- **A ceiling nobody can account for is a number somebody raises.** Every bound written here says
  what it is made of — the two use-case reads name their two readers, the session ceiling names
  what it was before. A guard whose figure has no derivation is edited the first time it fails
  rather than investigated.
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

---

## 7. Tests

- **A green test proves nothing on its own.** It proves the code and the test agree, which they
  inevitably do when both came from the same idea. **Prove a test can fail**: break the property,
  watch it go red, restore. `make mutants` does this for the properties worth keeping.
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
  reuse the real method where you can, and mark a test double as one.
- **A test whose setup never reaches the path it is named after.** SQLite enforces no column
  lengths; `TestClient` buffers a streamed body before you can hang up; a *cold* budget counter
  seeds from Postgres and hides a missing write; a fixture whose use case may call nothing can only
  ever exercise the exempt test double; an assertion that a line is **absent** passed while a
  mutation rendered the same claim under a different label — assert the element, not the wording;
  a panel test that left the usage map empty proved the line was missing because nothing had been
  *measured*, not because the scope was wrong. Four of these were found by the harness in one
  session, two of them in tests written minutes earlier.
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
- **A property guarded twice cannot be a mutation**, and that is not a reason to weaken the guard.
- **Each layer sees what the one below structurally cannot.** A dropped socket *cancels* a task
  where an in-process close raises `GeneratorExit`; two credentials can only disagree where both
  are real — a stubbed validator is exactly where a subject that "looks nothing like a username"
  can quietly come to look like one. Anything needing a user token belongs in `e2e/`.
