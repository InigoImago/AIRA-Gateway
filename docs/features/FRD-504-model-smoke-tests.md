# FRD-504 — Model smoke tests and jailbreak batteries for IT Security

> Phase: 5 (IT Security) · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: the owner's feature definition (PRD §1.1, item 14).
> Related: `FRD-300` (injection filter), `FRD-114` (model catalog), `FRD-403` (cost), `FRD-122`
> (audit), `FRD-502` (IT Security console).

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

## 6. Data Model

Management: `TestBattery` (name, version, restricted), `TestCase` (battery, category, prompt,
system prompt, expectation type, expectation value), `TestRun` (battery version, model, model
version, publisher, region, mode, started, finished, requested by), `TestResult` (run, case,
attempts, matches, outcome, stored responses).

Runs execute against the gateway; results live in Management, because this is a governance artefact
that outlives any individual gateway instance.

## 7. API / Interface Contract

- `GET/POST /api/v1/test-batteries/` and cases — IT Security / Global Admin.
- `POST /api/v1/test-runs/` `{battery, models[], mode, repetitions}` — starts a run, asynchronously.
- `GET /api/v1/test-runs/{id}` — status and results, with the previous run for comparison (FR-7).
- New SPA screen under a **Security** section, which is currently a disabled nav placeholder.

## 8. Security & Privacy

- **The batteries are themselves sensitive.** They state what we test for, so someone who reads them
  knows what to avoid. Restricted to IT Security and Global Admin (FR-8); results visible more
  widely than the prompts.
- Stored responses may contain harmful content (§5.6): own retention, restricted visibility, never
  exposed through use-case-scoped views.
- Runs consume budget and quota like anything else (FR-5); the internal use case is bounded so a
  misconfigured schedule cannot exhaust an organisation's quota.
- The internal use case must not be reachable from outside — no API key may be issued for it.

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
- **Unit (RBAC)** — a use-case administrator can neither read a battery nor start a run; IT
  Steuerung can read results but not the prompts.
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
