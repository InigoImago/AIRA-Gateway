# FRD-504 — The question catalogue, put to a pipeline

> Phase: 5 (IT Security) · Status: **Built**, narrower than drafted · Owner: Vadim Scheibe
>
> Origin: the owner's feature definition (PRD §1.1, item 14).
> Related: `ADR-0020` (a run is about a use case), `FRD-300`/`FRD-303` (pipeline), `FRD-308`
> (per-use-case model release), `FRD-114` (model catalog), `FRD-403` (cost), `FRD-122` (audit),
> `FRD-502` (IT Security console).

> **Read §5.7, §5.8, §6 and §7 for what exists.** Sections 1 to 5.6 are the original draft and are
> kept as the record of what was intended; several of their ideas were dropped on evidence and the
> deviations are named where they occur. Two are large enough to state up front:
>
> - **Vocabulary.** The draft speaks throughout of **batteries**, plural and named. There is **one
>   flat catalogue** of questions and no grouping — the owner's decision, and §5.7 says why grouping
>   was not merely unnecessary but harmful to the thing the catalogue is for. Read "battery" below
>   as "the catalogue".
> - **Subject.** The draft is about **models**, and so was the built feature at first. A run is now
>   about a **use case**, and travels that use case's own pipeline (`ADR-0020`, §5.8).
>   Testing a model is then an ordinary use case whose pipeline starts at it. Everything sections 1
>   to 5.6 want from a model is still obtainable that way; what they could not express is the
>   question a use-case administrator actually has, which is whether their *pipeline* holds.

## 1. Problem

Every control AIRA has governs *access* to models: who may call, how often, at what cost, with what
prompt. None of them says anything about **how the models themselves behave**.

IT Security's question is different from IT Steuerung's. Not "was this request allowed" but "does
this model, the one we approved for the whole organisation, refuse what it should refuse — and does
it still refuse it today". Three concrete situations:

- **Approval.** A new model is proposed for the catalog. Someone has to be able to say something
  evidenced about it before it is approved for every use case.
- **Drift.** A vendor updates a model behind an unchanged name, or we move a use case from Gemini
  to Claude via a fallback chain (`ADR-0012`). Behaviour changes and nobody finds out until an
  incident.
- **Our own filter.** `FRD-300`'s injection filter is configured per use case with patterns and an
  optional LLM classifier. Whether it actually catches anything is currently a matter of belief.

This is squarely inside `ADR-0013`'s scope test: it makes model access **better evidenced**. It does
not make the gateway think for the use case.

## 2. Goals & Non-Goals

**Goals**
- IT Security maintains **batteries** of prompts — benign smoke questions and jailbreak / injection
  attempts — and runs them against catalog models on demand and on a schedule.
- Results are **reviewable evidence**, not a pass/fail badge: what was sent, what came back, how
  often, when, against which model version.
- The battery can be run **through the pipeline** or **around it**, because those answer two
  different questions (§5.3).
- Runs cost money and tokens, and that spend is **attributed and bounded like any other**, never
  exempt.

**Non-Goals**
- **A safety guarantee.** Passing a battery does not make a model safe; it makes one statement about
  one battery on one day. §5.5 says so in the product, not only here — a test that is mistaken for a
  guarantee is worse than no test.
- Replacing the vendors' own safety evaluations.
- Automated blocking of a model on a failed run. A model with a bad result is a **decision** for IT
  Security (`FRD-307`'s revocation), not an automatic one — an automatic revocation triggered by a
  non-deterministic test is an outage waiting for a bad sample.
- Benchmarking quality, latency or accuracy. Different problem, different tooling.

## 3. User Stories
- As **IT Security**, I want to run a jailbreak battery against a model before it is approved, so
  that approval rests on evidence.
- As **IT Security**, I want a scheduled run to tell me when a model's behaviour changed under an
  unchanged name.
- As **IT Security**, I want to know whether *our* injection filter would have caught each attempt,
  separately from whether the model resisted it.

## 4. Functional Requirements

- **FR-1 Batteries.** A named, versioned set of test cases. A case is: a prompt (optionally a system
  prompt), an expectation, and a category (`smoke`, `jailbreak`, `injection`, `exfiltration`, …).
- **FR-2 Expectations are explicit and typed.** `must_refuse`, `must_not_contain(<canary>)`,
  `must_contain(<string>)`, `manual_review`. A case with no machine-checkable expectation is
  **allowed** and is recorded for review — an honest "a human must look at this" is better than a
  contrived regex (§5.4).
- **FR-3 Repetition, and a rate rather than a verdict.** Each case runs *n* times (configurable,
  default ≥ 5). The result is "3 of 20 attempts produced the forbidden content", never "failed".
  Models are non-deterministic; a single run is an anecdote (§5.2).
- **FR-4 Two modes.** *Through the pipeline* (does our filter catch it?) and *direct to the model*
  (does the model resist it?), reported separately (§5.3).
- **FR-5 Runs are attributed and bounded.** Every request goes through the normal audit path with a
  dedicated internal use case, its own budget and its own rate limit. A run that would exceed its
  budget stops and says so.
- **FR-6 Results are stored with what identifies them**: model, model version, publisher, region,
  battery version, mode, timestamp — enough to compare two runs and say what differed.
- **FR-7 Comparison over time.** A run is shown against the previous run for the same model and
  battery, with changes highlighted. This is the requirement that catches drift and is the main
  value once the first run is done.
- **FR-8 Restricted.** Authoring and running: IT Security and Global Administrator only. Results
  visible to the same, plus IT Steuerung (§8).
- **FR-9 Responses are retained under their own policy**, separate from production traffic (§5.6).

## 5. Design & Architecture

### 5.1 It runs through the gateway, deliberately

The battery dispatches through the gateway's own path — the same auth, attribution, pipeline,
budgets, audit and reporting as production traffic. Calling the vendors directly from a script would
be simpler and would test the models rather than the system, which is only half the question and the
less useful half.

A dedicated internal use case (`__aira_smoketest__`, invisible in the ordinary use-case list) carries
the attribution, so the runs appear in `FRD-601`'s reporting under their own name and their spend is
never mistaken for a team's.

### 5.2 A rate, not a verdict — and why this is the requirement most likely to be dropped

The instinct is a green tick per case. It is wrong, and expensively so.

Models are sampled. The same jailbreak prompt can be refused nine times and answered on the tenth,
and *that is the finding*: the model does not reliably refuse. A single-run boolean would have shown
green nine times out of ten, and the one red run would look like a flake to be re-run away.

So a case has a **success rate over *n* attempts**, and the UI shows the rate. "0 of 20" and "1 of
20" are different facts about a model and must look different. A test suite whose most likely
failure mode is being believed needs to report uncertainty as data, not hide it behind a symbol.

### 5.3 Two modes, because the pipeline would block the test

A real conflict, and it is the most interesting thing in this FRD.

`FRD-300`'s injection filter exists to block exactly the prompts a jailbreak battery sends. Run the
battery through a use case with the filter on, and most cases never reach a model — the run would
report "blocked" and say nothing whatever about the model.

Both facts are worth having, and they are different facts:

- **Through the pipeline** — *would our filter have caught this?* Tests AIRA. The result is the
  filter's catch rate per category, which is the first honest measurement we would have of whether
  the injection filter earns its place.
- **Direct to the model** — *does the model resist this?* Tests the brain. The internal use case runs
  with a pass-through pipeline so the prompt actually arrives.

Reported side by side, they also produce the most useful cell in the table: a case the filter misses
**and** the model answers. That is the one to act on.

### 5.4 Judging the answer, honestly

Three mechanisms, in descending order of trustworthiness:

1. **Canary strings.** The case plants a token the model must not reveal, or a phrase whose presence
   proves compliance. Deterministic, cheap, and the only one that is genuinely reliable — so cases
   should be written for it wherever possible.
2. **Refusal detection.** Heuristic and fragile: a model may refuse in a way no pattern anticipated,
   or comply in a refusal-shaped sentence. Reported as *likely*, never as fact.
3. **A model as judge.** Available, and carrying a caveat that must be shown next to the result: the
   judge is a model, it can be fooled by the same content it is judging, and a battery whose results
   depend on one is measuring two models at once. Never the only mechanism for a case that matters.

And `manual_review` (FR-2) is a first-class outcome, not a fallback. A category where automated
judging is unreliable should say so rather than produce a number nobody should trust.

### 5.5 The result page says what the result is not

Wherever a run is shown, it states that it is one battery on one day and is not a safety statement.
That sounds like a disclaimer and is a design requirement: this feature's most likely real-world
failure is a green page being cited in an approval as though it proved something. The same argument
as `FRD-403`'s unpriced traffic and `FRD-601`'s lower-bound spend — a figure that reads as complete
when it is not causes worse decisions than an absent one.

### 5.6 Storing what came back

Jailbreak responses may contain exactly the content the model should not have produced. That is the
evidence, so it is stored — but under its own retention, visible to IT Security rather than to the
use case's members, and outside the ordinary production payload rules (`FRD-404`), because these are
not a team's prompts and should not follow a team's policy.

### 5.7 One catalogue, and a standing that is the latest run

Added 2026-08-09. Two versions of this were wrong first, and both were wrong in the same way — they
answered a question nobody had asked, and no test could call them wrong because the code and the
test came from the same idea.

The screen is **three activities in three sub-tabs**:

| Sub-tab | What it answers | Who |
|---|---|---|
| **Latest results** | where does each model stand | anybody who may test |
| **Runs** | put the catalogue to a model; judge the answers; read older runs | anybody who may test |
| **Questions** | what are we asking | reading: anybody; writing: IT Security |

**One flat list of questions. No grouping.** The first implementation sorted them into named
batteries. There is nothing to group — and grouping quietly cost the property that makes the
catalogue a *standard*: with several batteries, "how does this model do" has as many answers as
there are groups, and none of them compares to a model that was asked a different group. Every
model is asked every question, which is the only reason two models are comparable at all.

`topic` stays, as the keyword saying what a question tests. It is a **label on a row, not a
categorisation**: nothing branches on it, nothing is grouped by it, and two questions may share
one. It exists so a reader scanning a hundred rows sees what is being asked about without reading a
hundred prompts. The search covers the wording as well as the label, because somebody looking for
"the one about explosives" remembers the question and not the label.

Seeded with 100 questions and then owned by IT Security in the console: a catalogue that can only
be changed by editing a seed file is one that stops being edited.

**A model's standing is its latest run — never a total across every run it has had.** Summing is
the wrong shape twice over: an old, since-corrected result drags the current figure down forever,
and the number moves when somebody re-runs something unrelated. Earlier runs are **history**, kept
and readable: how a model behaved before its version changed is the question anybody upgrading one
actually has, and only the history answers it. The run that currently counts is badged in the run
list, read from the same rows the results tab is built from — a second definition of "latest" would
eventually disagree with the first.

The results table states **how many of today's questions that run covered**. A run made before
questions were added answered fewer of them, and "40 out of a catalogue that has since grown to
100" is a different statement from "40".

**Questions are keyed by position, and dropped ones are retired rather than deleted.** Keying on
the topic looks natural and is wrong: a rename is then a *create*, so the previous wording survives
beside the new one with its answers still attached — which is exactly what makes it invisible. It
happened, and the catalogue silently grew by two. Retiring rather than deleting keeps the verdicts
somebody gave against the old wording, which are the only evidence that anything has changed. A
retired question is not listed and not asked.

### 5.8 A run is about a use case, and the pipeline decides the rest

Added 2026-08-16; the reasoning is `ADR-0020` and only the built shape is repeated here.

**The catalogue is governed centrally and run by administrators.** Writing a question stays with
Global Administrators and IT Security — it states what this installation considers an acceptable
answer, which is the same kind of statement as a global anomaly rule.

*Running* it takes two things, and both are separately necessary (`access.may_run_tests_queryset`):

1. the **gateway** would accept this caller for the use case — its own rule, `may_call_queryset`,
   rather than a second one that would eventually disagree with it, because the run's requests are
   sent with the signed-in person's own credentials;
2. **administration of that use case** (`MANAGE`, which group grants and direct memberships both
   write), or one of the two installation roles.

A use-case administrator can therefore put the same hundred questions to their own pipeline, which
is the question §5.3 wanted answered and never could.

**A normal use-case user cannot** — the owner's rule, added after this section was first written.
The rule then was "anybody the gateway would accept", taken from this document's own sentence
*whoever may call a model may test one*, which was written when a run was about a model the whole
installation had approved. A run is now a hundred prompts through somebody's pipeline, spending
that use case's budget, against a catalogue that states what this installation tests for (§8):
a decision **about** the use case rather than work **inside** it. Reading the catalogue follows
running it, for the same reason — the prompts say what to avoid, so somebody who reads them was
told deliberately.

The refusal is asked **per object at every endpoint**, not once at the door. `MayRunTests` answers
"is there any use case this person could run", which is the right question for offering the screen
and the wrong one for starting a run: an administrator of one use case passes it and must still be
refused somebody else's. And a caller who reaches the screen by address rather than by the nav gets
a sentence naming who runs the catalogue instead of three tabs of controls that all refuse —
`FRD-206` again.

**The pipeline decides which model answers.** A run names no model. It names a use case, and the
request enters that use case's pipeline at the pipeline's declared **start model**; a `model_route`
step may then send it somewhere else, and that is the pipeline doing its job. This is what deleted
`_release_for_testing`: the old shape asked the caller for a model, which `FRD-308` then refused
because the use case had never been released it, so the runner quietly wrote `allowed_models` to
make its own run work. Releasing a model is the use case administrator's decision, and it was never
this feature's to make.

**Which is why a pipeline now declares a start model.** It is where a request enters when the caller
names none — configuration *about* the pipeline rather than a step in it, the same shape `FRD-308`
settled for `allowed_models`. It is validated like every other model a pipeline names: released to
the use case, or the save is refused. Blank is a real state and means *this pipeline is only ever
entered by a caller who names a model*, which is every pipeline written before today; the console
then says the use case cannot be run **and why**, rather than guessing one. The dry run reads the
same field first, ahead of the three guesses whose own comments record each being wrong in
production (`ADR-0020`, options considered).

**Testing a model is a use case.** IT Security creates one, releases the models under evaluation to
it, and points its pipeline's start model at one of them. There is no special path, no internal use
case the code branches on, and no invisible attribution: the spend lands on the use case that asked
for the evidence, which is where it belongs. The seeded `smoke-test` use case survives as an
ordinary demonstration of this — a released model, a pipeline that starts there — and the constant
naming it is used by the seed alone.

**A blocked question is a result.** Under the old shape a filter that refused a prompt made the run
useless; under this one it *is* the finding. The two modes of §5.3 collapse into one mechanism: run
the catalogue against a use case whose pipeline filters to measure the filter, and against one whose
pipeline is bare to measure the model.

**A standing is per use case, not per model.** Two runs of one use case whose start model changed in
between are not comparable, so the run and the statistics row both carry the start model as it stood
at the time — recorded on the run rather than looked up, because the older run is evidence about the
configuration it actually met.

## 6. Data Model

As built (2026-08-09; the subject of a run changed 2026-08-16), which is smaller than this section
originally proposed:

- `TestCase` — `topic`, `prompt`, `expectation`, `position`, `retired`. **One flat list**; there is
  no battery, no category and no expectation *type*, because nothing matches against an expectation
  (§5.2) and nothing groups the catalogue (§5.7).
- `TestRun` — `use_case` (**what the run is about**, and required since `ADR-0020`), `model` (the
  start model the pipeline was *entered at*, as it stood then), `started_at`, `finished_at`,
  `requested_by`. Both identifiers are **strings**, not foreign keys, and for one reason: a run is
  evidence about what happened on a day, and deleting a use case or a model declaration must not
  delete the finding.
- `TestResult` — `run`, `case`, `response`, `error` (a failed *request* is not a bad *answer* and
  they are stored apart), `latency_ms`, `verdict`, `note`, `rated_by`, `rated_at`.
- `PipelineConfig.start_model` — on the pipeline, not here (§5.8). Distributed to the gateway on
  `pipeline.upserted` like the rest of the configuration (`FRD-204`).

Runs execute against the gateway; results live in Management, because this is a governance artefact
that outlives any individual gateway instance.

Not built: `version` on the catalogue, `restricted`, `attempts`/`repetitions`, and the
publisher/region columns on a run. Each is recoverable from the audit row the gateway already
writes, and none was needed to answer the question this feature exists for.

## 7. API / Interface Contract

- `GET /api/v1/test-cases/` — the catalogue, retired questions excluded. Readable by anyone who may
  run it — which is **not** every member of a use case (§5.8) — and **writable by Global
  Administrators and IT Security only**, because it states what this installation considers an
  acceptable answer.
- `GET /api/v1/test-attribution/` — the use cases this caller may run the catalogue against, each
  with its `start_model`, `may_run` and, when it cannot be run, `why_not` in words. `FRD-206`: the
  console offers no button the server would refuse, and says why rather than disabling silently.
- `POST /api/v1/test-runs/` `{use_case}` — creates the run and one empty result per question. The
  **model is not a field a caller may set**: it is read from the use case's pipeline and stored on
  the run (§5.8), and a use case with no start model is refused by name. The prompts are then sent
  by the console **one at a time**: a run is ordinary traffic, and firing a hundred at once would
  trip the use case's own rate limit and produce a run full of 429s that says nothing about
  anything.
- `GET /api/v1/test-runs/{id}/results/` — the answers. `PATCH /api/v1/test-results/{id}/` stores an
  answer or records a verdict; the rating's author is whoever is signed in and is never a field a
  caller may set.
- `GET /api/v1/test-runs/{id}/export/` — CSV, BOM and CRLF, every field quoted (`FRD-602`'s rules).
- `GET /api/v1/test-stats/` — **one row per use case**: its latest run, the start model that run
  entered at, that run's counts, and how many questions the catalogue asks today.
- Every list is scoped by `may_call_queryset`, so a caller sees the runs of the use cases they may
  run and no others.
- `GET /api/v1/me/` carries `may_test`, so the SPA shows the screen to exactly whoever the server
  would let use it.
- SPA screen **Pipeline tests**, three sub-tabs. `/model-tests` redirects to `/pipeline-tests` — a
  bookmark is a link somebody saved, and breaking it teaches nothing.

## 8. Security & Privacy

- **The catalogue is itself sensitive.** It states what we test for, so someone who reads it knows
  what to avoid. **Writing** is Global Administrator and IT Security only (FR-8, §5.8). **Reading**
  is whoever may run it and no wider: a person putting a hundred prompts to their pipeline
  necessarily sees them, so a screen that ran questions it would not show would be theatre — but a
  plain use-case user runs nothing and therefore reads nothing.
- Stored responses may contain harmful content (§5.6): own retention, restricted visibility, never
  exposed through use-case-scoped views.
- **Starting a run includes the gateway's own check, not a substitute for it.** A run can only be
  started where an ordinary API request from the same person would be accepted **and** where they
  administer the use case — and the refusal does not distinguish "no such use case" from "not
  yours", because telling them apart tells an outsider which slugs exist.
- **An installation role is not a bypass of the gateway's rule.** IT Security reaches the use case
  it evaluates models in because somebody put them in its group, exactly like everybody else. If a
  role short-circuited that, the run would fail at dispatch and the console would have promised
  something the server refuses.
- Runs consume budget and quota like anything else (FR-5), on **the use case that asked for them**.
  There is no exempt pot and no shared one, so a run that would exceed a budget stops the way any
  other request would, and nobody's evidence is charged to somebody else.
- Nothing releases a model as a side effect of a run (`ADR-0020`): `_release_for_testing` is
  deleted, and a use case reaches only what its administrator released to it (`FRD-308`).

## 9. Observability

Runs are ordinary traffic in the audit trail (`FRD-122`) with their own use case, so their spend and
volume are visible in reporting without a second mechanism.

## 10. Testing & Acceptance Criteria

- **Unit** — each expectation type evaluated correctly, including a canary that *does* appear and
  one that does not; `manual_review` produces no verdict; repetition aggregates to a rate and not a
  boolean (written to fail against a first-run-wins implementation, §5.2).
- **Unit** — the two modes dispatch differently: through-pipeline can be blocked by the filter,
  direct-to-model is not, and the results are reported apart.
- **Unit** — a run that would exceed its budget stops and reports partial results rather than being
  refused mid-way with nothing recorded.
- **Unit (RBAC), as built** — a use-case **administrator** may start a run in a use case they may
  call, and may not in one they merely belong to or cannot reach; a plain use-case **user** is
  refused the catalogue, the attribution list, the statistics and a run, all four, because a rule
  enforced at one endpoint is a rule the other three do not have. The refusal reads the same for a
  use case that does not exist. Writing a question stays Global Administrator and IT Security.
  (The draft's version of this line said a use-case administrator could do neither; §5.8 is why it
  changed, twice and in both directions.)
- **Frontend** — a 403 on load withholds the screen and names who runs the catalogue; a 500 does
  not, because "ask an administrator" would send that reader to somebody who cannot help.
- **Mutation** — `Q1f` removes the administration half of the rule, `Q1g` the gateway half. Each
  must make a test fail on its own: a composition guarded only as a whole is a composition that can
  quietly lose a half.
- **Unit** — a run stores the pipeline's start model rather than anything the caller sent, and a use
  case whose pipeline declares none is refused by name instead of run against nothing.
- **Frontend** — rates rendered as rates; the "this is not a safety statement" text present on every
  result view (§5.5); comparison against the previous run highlights changes.
- **Integration** — a run against the mock provider completes, produces results, and appears in
  reporting under the internal use case.
- **Mutation** — the rate is actually computed over attempts; the RBAC restriction actually
  restricts; the canary comparison actually compares.

**Acceptance**
- *Given* a battery with a canary-based jailbreak case, *when* it is run 20× in both modes against
  an approved model, *then* the result shows the filter's catch rate and the model's answer rate
  separately, and the spend appears under the internal use case.
- *Given* a previous run for the same model and battery, *when* a new run finishes, *then* changed
  cases are highlighted — which is how a vendor's silent model update becomes visible.

## 11. Dependencies & Risks

- `FRD-300` (pipeline modes), `FRD-114` (model and version identity — without a recorded version,
  FR-7's drift comparison compares two unknowns), `FRD-403` (cost), `FRD-122` (audit).
- **Risk — the results are believed more than they deserve.** §5.2 and §5.5 are the mitigations, and
  they are the parts most likely to be trimmed as "just UI text". They are not.
- **Risk — battery maintenance.** Jailbreak techniques move; a battery from last year measures last
  year. Versioning (FR-1) makes the staleness visible; keeping it current is a person's job, not the
  system's.
- **Open** — whether to seed a starter battery. My view: yes, small and clearly marked as a
  starting point, because an empty feature is never used and a comprehensive one is never reviewed.

## 12. Rollout / Demo

The mock provider answers a seeded battery deterministically — one case it "refuses" and one it
does not — so both outcome shapes, both modes and the comparison view are demonstrable without any
cloud call, and the acceptance tests run in CI.
