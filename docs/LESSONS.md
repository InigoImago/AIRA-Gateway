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
- **A read path that writes is a read path that cannot scale.** Reconciling a caller's groups on
  every request wrote eight rows in the steady state, for a `GET`. Compare first and write only the
  difference; agreeing with the database should be free.
- **An ambiguous routing table refuses to boot.** With three adapters, last-registration-wins is a
  silent choice of region and credential.
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

---

## 7. Tests

- **A green test proves nothing on its own.** It proves the code and the test agree, which they
  inevitably do when both came from the same idea. **Prove a test can fail**: break the property,
  watch it go red, restore. `make mutants` does this for the properties worth keeping.
- **A test that skips when the data is inconvenient reports green about nothing.**
- **A subset that passes is not a suite that passes.**
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
