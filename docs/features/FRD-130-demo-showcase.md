# FRD-130 — A demo somebody can walk through

> Phase: 0 (extension) · Status: **Done (2026-08-07)** · Owner: AIRA · Last updated: 2026-08-07
> Related: `FRD-002` (seed & demo mode), `FRD-123` (local models), `FRD-201` (RBAC),
> `FRD-402`, `FRD-405`, `FRD-601`

## 1. Summary

`seed_demo` created the five roles and one user each. You could log in as every role — and look at
five empty screens. The seed proved the _accounts_ worked; it demonstrated nothing about the
product.

This adds a showcase contribution that gives each role something to see, and picks the content so
the **differences between the roles are visible rather than described**.

## 2. Goals & Non-Goals

**Goals**

- Every role can be signed into and shows a screen that is different from the other roles' screens.
- The three use cases each make one governance decision concrete, rather than being three copies.
- Traffic is **real** — driven through the gateway against the local model — so the figures are the
  product's own output.
- Re-runnable: running it twice changes nothing, and `--fresh` resets the demo without destroying
  anything outside it.

**Non-Goals**

- Not a load generator and not a fixture library for tests.
- Not enabled outside `local`/`demo` (inherited from `FRD-002`; `seed_demo` refuses elsewhere).

## 3. Functional requirements

**FR-1** — Three use cases: `kundenservice` (payloads stored, shortest retention that still
supports an incident review, heuristic injection filter), `entwicklung` (higher volume, rate limits
rather than a tight budget), `personalwesen` (**payload storage off** — the figures are still
collected, the prompts are not).

**FR-2** — `ucadmin` administers **two** of the three. Switching to that account and finding two
instead of three is the fastest demonstration that the scoping is real rather than a filter in the
frontend.

**FR-3** — Budgets across every axis the UI offers: cost, tokens and requests; use-case and member
scope; day and month. Sized so the consumption bars show a _reading_, not 0.02%.

**FR-4** — One API key per use case, **re-derived deterministically** rather than regenerated. A
demo that mints a new secret on every run is a demo whose printed examples stop working the second
time. This is explicitly _not_ how a real key is issued (`FRD-205`: shown once, never again), and
the seed says so where it does it.

**FR-5** — `tools/demo_traffic.py` drives real requests through the gateway, including one
prompt-injection attempt that the filter refuses, so the pipeline decision appears in the audit
trail and the reporting screen.

**FR-6** — Ollama joins the `demo` Compose profile with a **separate pull step**, so the server's
health check stays honest: a container that reports "healthy" only after a multi-hundred-megabyte
download makes every restart look like a hang.

**FR-7** — The dev Keycloak realm's groups match the demo use-case slugs, and `ucadmin`/`ucuser`/
`itgov` are in them. The gateway takes membership from those groups, so without this the
consumption figures are invisible to exactly the people the demo asks you to log in as.

## 4. Decisions worth keeping

**Real traffic, not inserted rows.** Inserted rows would have been consistent. They would also have
been a story _about_ the product rather than the product: every figure in the demo is one the
gateway itself produced, through the same pre-dispatch gate, pricing and audit path as production
traffic.

**The seed reconciles, it does not merely add.** Asking the running stack who could manage what
found `itgov` still administering `personalwesen` and `itsec` still belonging to `kundenservice` —
both from declarations that no longer existed. A membership left behind is not a stale row, it is
**live permission on a use case**, and a seed that only ever adds cannot be re-run to a known
state, which is most of what a seed is for. Memberships of a demo use case that the declaration
does not name are now removed, and the guardian permissions they granted are revoked with them.

**An oversight role administers nothing.** `personalwesen` was given to `itgov` so the demo could
show a use case `ucadmin` cannot touch. It bought that at the cost of teaching the opposite of what
the role is: PRD §154 gives IT Steuerung every figure and no write anywhere, and a walkthrough in
which it renames a use case demonstrates a boundary that does not exist. The global administrator
owns it instead; the point survives.

**`--fresh` resets the demo, and only the demo.** An early version deleted every use case, and
deleting a use case revokes its keys **terminally** (`FRD-205`) — a reset is not a retirement. It
now removes only the demo slugs.

## 4a. A coding assistant, from `make showcase` (2026-08-10)

The showcase seeded three chat-shaped use cases and no agent, while `tools/opencode/README.md` had
pointed at a `coding-assistant` use case since `FRD-132` — an instruction with no destination,
`FRD-208`'s finding in another file. Four things were missing and each failed differently:

1. **No use case had `tools_enabled`.** Correct as a default (`FRD-131` FR-3), and it means the
   demo could not reach the capability at all.
2. **The Management-side model seed did not declare `tools`.** The gateway-side one
   (`tools/seed_local_catalog.py`) has since `FRD-131`, and the two carry the _same measurement_ —
   so a fact fixed in one file stayed wrong in the other, which is the failure this pair has now
   produced twice (the first was `minimal`, corrected in Management on 2026-08-06 and left in
   `tools/` until 2026-08-08). The consequence was total and silent: an assistant was refused by
   name and every explanation pointed at the client.
3. **Limits sized for a chatbot.** One human instruction becomes many model calls, so an assistant
   trips a chatbot's limit in its first minute and reads as a broken gateway. The seed's limit and
   budget for this use case are sized for what an agent actually does.
4. **No hand-over.** `tools/showcase_agent.py` writes an OpenCode configuration naming the model
   the demo actually serves, and `make showcase` prints it. A demo that ends one manual step short
   of working is a demo that gets described rather than shown.

**Prompt caching stays off on it, deliberately**, and the description says why: the local runtime
reports no cached tokens, so a switch turned on here would show a control doing nothing —
`FRD-125`'s absent control wearing a present one's badge, in the one place a reader is most likely
to believe it.

The demo key is **re-derived** by the hand-over rather than read from the database, because it runs
outside Django — so the salt exists twice, and a test compares the derived key against the stored
**hash**. A drifted copy would produce a config that looks right, an assistant that starts, and a
401 the reader blames on the gateway.

## 4b. Two things that made it work only the first time (2026-08-10)

Both found by running `make showcase` after a `docker compose down`, which is what a colleague
does, and neither visible by reading the target.

**The dev Vault forgets.** It runs `server -dev`, which keeps everything in memory, so recreating
the container loses `secret/aira`. `load_secrets()` then fails closed — correctly (`FRD-116`: a
store that answers "no such path" is a permission problem, not an outage to shrug off) — and every
application container refuses to boot. The showcase therefore depended on somebody having run
`make vault-init` _after_ the current Vault container started: **follow our own documentation, then
bring the stack down, and the one command that must always work stops working.** It is a `vault-init`
service in the demo profile now, with the two migration jobs waiting on it, because ordering belongs
in the file that owns ordering rather than in one of four entry points.

Two mistakes while building it, both instructive. It first wrote all three known secrets
unconditionally — and Vault **ranks above the environment**, so an empty value does not mean
"nothing here", it means "the value is the empty string" and it silently wins. The stack came up
with `no password supplied`. _Absent and empty are different answers_, the same rule as "unpriced is
not free". Then, with nothing set to write, `vault kv put` failed with `Must supply data`: an empty
write is not a write, so the path still did not exist. A stack with no secrets configured now gets a
note on the path saying exactly that, in the one place somebody looking at an empty Vault would go.

**The demo spent its own budgets.** They are calibrated so a handful of requests moves each bar
into the middle of its range, so the second run of a day found them spent: **six of ten requests
answered 429**, including the prompt-injection case, whose entire point is to be refused by the
_pipeline_. Still true, and about yesterday. `make showcase` now clears what earlier runs
**consumed** — `budget_usage` in Postgres and the shared Redis counters, both, since clearing one
leaves the other refusing for a period nobody can see — and nothing that the demo **is**:
configuration, keys and the request log are untouched, because a spend report reading zero after
every showcase run is the opposite defect. `make showcase-traffic` deliberately does _not_ reset:
filling the bars until a limit is reached is exactly what that target is for.

## 4c. On a machine that had never run it (2026-08-10)

`.env` deleted, database and Kafka volumes removed, `make showcase`. It reported **success** and
served **nothing**: ten requests, ten refusals, `400 … 'qwen3:0.6b' is not in the model catalog`.

**The seed wrote the catalog and never announced it.** `local_models` created both `Model` rows and
emitted no event, so Management's catalog filled up and the gateway's read-model stayed empty. Only
the viewset emitted — a model declared through the console reached the gateway, a model declared by
the seed did not. Invisible until `FRD-307`, which made a catalogued, approved model the _only_ kind
that may be served: from then on an unannounced catalog refuses everything. The fourth instance in
this repository of two correct halves and no wire between them, after `record_to_outbox`, the
missing Kafka topics, and `payload_size`. It emits `_payload(model)` — **the viewset's**, because a
second hand-written payload is a second place to forget that prices travel as decimal strings.

**And `make showcase` reported success over it.** The traffic script only failed on a 5xx, and every
one of those refusals was the gateway behaving correctly. So the target printed its login table over
a demo whose every screen read zero. Nothing served is now a failure with a message naming the two
logs worth reading, and the Makefile no longer swallows the traffic script's exit code — the traffic
is what decides whether this showcase is worth showing.

**The model pull deserved better than `set -e`.** A large download from a registry a corporate
network may block, failing on one attempt, produced a bare non-zero exit — and because the seed
waits on it, compose then blamed `management-seed`, a service that never ran. Three attempts now,
and a final failure says what it means: no local model, nothing to serve, and a catalogued model
nobody can reach fails every request made against it. Failing is still correct (§4 records why an
unpulled model must not be catalogued); what changed is that the reason is legible.

Re-run from the same empty state: **ten served, one refused by the injection filter.**

## 4d. One failed download cost the entire demo (2026-08-10)

Reported as two unrelated things — "the ollama pull blows up" and, later, "the console is empty" —
and they were the same event. `management-seed` waited for `ollama-pull` to complete **successfully**,
so a blocked registry or one flaky minute meant the seed never ran at all: no demo accounts, no use
cases, no budgets, no keys. The console then came up, listed nothing, and said _"No use cases yet"_.

`FRD-130`'s rule is the one that matters — a model nobody pulled must not be catalogued, because
every request against it fails with `model_not_found` — and it is enforced by **evidence** now
rather than by ordering: the seed runs regardless, asks the endpoint which models it actually
serves (`/v1/models`, the dialect this catalog is written against), and declares only those. An
endpoint that cannot be asked declares **nothing**: unreachable is not "serves nothing" and is
certainly not "serves everything", the same rule as `FRD-114` FR-7 one layer out.

That check introduced a regression of its own, caught by running it: the catalog says `all-minilm`,
the endpoint answers `all-minilm:latest`, and comparing them as plain strings dropped the embedding
model. An absent tag means `:latest` — the same family as the colon that once split `qwen3:0.6b`
into a model nobody served.

**`make showcase-doctor`** exists because of this class of failure. It reports the chain link by
link — Keycloak accounts and groups, Management's use cases and the Django groups each account
resolved to, the gateway's read-model — and names the first broken link with the command that
fixes it. It is what turned "the console is empty" into "the seed never ran", in one line. It is
deliberately **not** part of `make showcase`: a demo that runs a diagnostic every time is a demo
that has given up on working.

## 4e. A revoked demo key cannot be reissued, and that is correct (2026-08-10)

The next run came back **401** on every request, and the diagnosis printed for it blamed the model
catalog — which is an _authentication_ failure and has nothing to do with the catalog. A diagnosis
confidently about the wrong thing sends somebody looking in the wrong place, so the traffic script
reads the status codes now and says what each one means.

The cause is a rule working as designed. Deleting a use case revokes its API keys, and revocation
is **terminal** in the gateway's read-model: no `api_key.created` may resurrect one (`ADR-0007`).
The demo's keys are deterministic, so re-running the seed re-announces the _same prefix_ and
changes nothing — the stack looks perfect and every request is refused for ever.

`make showcase-reset-keys` removes those four rows so the announcement lands again, waits for it by
**polling** rather than by guessing an interval, and reports through the doctor. It is deliberately
its own command: deleting rows from the read-model authorization is drawn from is not a habit to
encode into a target that runs every time.

Found while proving it: the traffic tally counted the embedding call only when it _succeeded_, so a
refused one appeared in no column and eleven requests reported as "9 served, 1 refused". The same
shape as the refusals `FRD-122` found leaving no audit row, in a script whose only job is to report
what happened.

## 5. Testing

`management/backend/tests/test_showcase_seed.py`: idempotence (a second run creates nothing new),
the membership asymmetry of FR-2, storage off for `personalwesen`, that `--fresh` leaves non-demo
use cases alone, that a membership the declaration no longer names is removed **and its permission
revoked with it** (shown to fail against the add-only version), and that no oversight role
administers anything. Since 2026-08-10 also: that **exactly one** use case may declare functions,
that a model declaring `tools` exists for it to use — with the local endpoint _configured for the
test_ rather than skipped over, because the condition that hides the defect is the condition that
would hide the test — and that the hand-over derives the key the seed actually stored.

## 6. Open

- The traffic script needs the local model running; without it, it reports why rather than seeding
  figures that never happened.
