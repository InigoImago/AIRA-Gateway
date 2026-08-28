# AIRA Gateway — Development Log

A running, dated log of meaningful changes and decisions. Newest entries on top.
Keep entries short; link to ADRs/FRDs/commits for detail.

---

## Every view of the console, checked the same way (2026-08-27)

The third plane, and the question has to be translated before it can be asked. A console enforces
nothing — the server decides, every time, and a disagreement here shows up as a refusal rather than
as access. So *"wrong roles, no roles, invented security"* becomes: **does each view decide what to
offer from the system that decides what happens, and does every load say what became of it?**

Twenty-five components over nine routes. **The mechanical half came back clean**: no signal
rendered in a template without its call, no `[(ngModel)]`, no mutable component state that is not a
signal — the three zoneless defects `FRD-203` §4 names, each of which only a browser would show.
And every authority switch already reads the server's own answer: `permissions.can_admin` /
`can_manage` / `is_member` / `may_call` per use case, `me.roles`, `me.may_test`, and `may_run` per
use case from `/test-attribution`.

**Four findings, and the first two are one shape.** `core/auth/roles.ts` exists because of a
measured defect — on 2026-08-07 `it-steuerung` could stop traffic in the gateway while Management
refused it a global rule, two planes and two answers — and its own first paragraph warns that *"a
console that restates the list a third time is the same defect with a longer fuse: nothing fails
when the server's list changes."* Two components restated one anyway (`model-catalog.canEdit`,
`installation-budget-card.canManage`), and **nothing compared the file's four lists with the
server's** — the warning had no counterpart, which is `LESSONS.md`'s *a named bound that nothing
reads*. They agreed that day, which is precisely what makes it worth a guard rather than a
correction.

Third: `app.hasRole(role: string)` was called by nothing — not by its component, not by its
template, not by any file. An unreachable helper is a rule the code claims and does not have, and
this one was the **generic** role check sitting beside two that correctly go through `roles.ts`.
The next contributor reaching for the obvious-looking one writes the fourth copy.

Fourth: `smoke-tests.refreshRuns()` swallowed two load failures, alone among the loads on that
screen — the two beside it in the same `ngOnInit` each report through `PageFeedback`, one with a
403 branch of its own. It is called from `ngOnInit`, so a failed **first** load left the Runs and
Results tabs empty with nothing saying why — and empty is what that screen looks like before
anybody has run anything. *An empty state that states the wrong reason is worse than one that
states none*, written in `reporting.py` about the other plane and true here.

Plus two justifications that stated a **retired** rule. `mayRun` explained itself with `FRD-504`'s
*whoever may call a model may test one*, and `mayTest` named `MayTestModels` asking for
`view_usecase` — the server withdrew both on 2026-08-16, and `MayRunTests` says so in as many
words. Both gates were right, because both ask the server; the reason beside them described the
rule the server no longer has, which is the more dangerous half — nothing fails, and the next
reader reasons from it.

### The new test failed its own first audit

`test_the_console_and_the_server_agree_about_roles.py` reads `roles.ts`, compares each list against
`aira_common.roles` and against `IsGlobalAdmin`/`IsITSecurity`, checks every role it names exists,
and refuses a role literal anywhere else in the console — `.html` as well as `.ts`, because a
`@if (hasRole('it-security'))` in a template is the same decision in the same console. 4 mutations
(**649**), each observed `caught`, including the template one.

The DOM test for the silent loads is not one the harness can run, so it was broken by hand — and
**it did not go red.** The stub failed both calls at once and the assertion only looked for
"unreachable", which the *figures* also say, so the runs handler could be put back to swallowing
its error with the test still green. A test that cannot tell which of two calls reported is a test
of neither. The stubs now fail separately and each assertion names its own source; measured again:
947 pass with the fix, exactly one fails without it.



The same treatment for Management: every surface, every role, and none. Real Keycloak tokens rather
than fixtures, so the roles arrive through the `groups` claim and the configured mapping
(`ADR-0017`), and the tenancy positions are real object grants reached through a real group
(`FRD-209`). **Two findings, one class.**

### An identity that crosses a boundary was mutable on one side

A use case's `slug` and a catalogued model's `name` are both editable strings on this plane and
**primary keys on the other**. Management owns a row; the gateway owns a different database, fed
over Kafka, keyed on the string this plane sends. A rename here renames nothing — it abandons one
object and starts another, and only this side is told.

Measured against the running stack. One `PATCH` by a **use-case administrator** on their own use
case, holding no organisation-wide role at all:

| | |
| --- | --- |
| Management | knows only the new slug — `404` for the old |
| gateway `use_cases` | **two rows**, the old one intact and with **no tombstone** |
| gateway `api_keys` | still bound to the old slug, still active |
| gateway `pipeline_configs`, `rate_limits` | still on the old slug, still enforcing |
| the key issued beforehand | **still served, `200`** |

So one field moves a fully provisioned use case out of the control plane's sight while it goes on
serving traffic. Retirement cannot reach it: `FRD-607` writes the tombstone for the *new* slug, so
`refuse_if_retired` never fires — which is the one thing that feature exists to prevent, and its
own docstring says so (*"retiring a compromised use case has to stop the traffic, or it is a filing
action"*). Nor can a key revocation, a budget, a limit or a purge. The audit trail splits and
reporting follows half of it. And the Keycloak group `/use-cases/<old>` keeps granting data-plane
access to the orphan, because that convention resolves from the token alone.

One `PATCH` on a model, by a Global Administrator: the gateway ends up holding **both** names, the
old one still `approved`, while Management answers `404` for it. That reopens the loophole
`FRD-307` closed — its docstring records the first version's mistake, *deleting a declaration made
a model usable again*, and an orphan is worse: catalogued, approved, and permanently beyond the
reach of the plane that could un-approve it.

Refused rather than made read-only, because `read_only` answers `200` with the old value and a
caller who patches a slug and reads `200` believes they renamed it (`FRD-124`).

### What held

The role matrix itself, over 164 live cells — sixteen use-case routes and six installation
surfaces across eight positions. The oversight roles read everything and write nothing (PRD §154),
including no API key: that is data-plane access, deliberately withheld from the roles that see
every use case. An outsider gets `404` for every route of a use case rather than `403`, so the
refusal does not confirm the slug. Field-level authorisation holds too: releasing an unapproved
model is refused **by name** (`FRD-307`/`FRD-308`), `retention_days` is bounded at both ends, and
`deleted_at` and invented fields are ignored. The three delete-by-id routes all filter by use case,
so there is no IDOR on a nested id. And the question catalogue asks per object —
`_use_case_the_caller_may_run` exists precisely because a class permission cannot see one.

### Two things the round established rather than fixed

**`IsGlobalAdminOrUseCaseAdministrator` is not a role gate.** It asks whether somebody administers
*any* use case, which is a fact about the whole installation's grants. The first version of the
live matrix granted `/aira/it-security` administration in one cell and asserted two cells later
that IT Security is excluded from the directory — the test disproved its own premise. The matrix
now grants a group nobody in it holds, and the directory row asserts only the two stable ends.

**IT Security cannot see the retired list** (`403`), while IT Steuerung can. The gateway made the
opposite correction on 2026-08-08 — *"`is_oversight`, not `is_governance` … the role whose job is
investigating an incident saw an empty screen"* — but `FRD-607` FR-4 says *visible to governance
roles* in as many words. That is the specification, so it is now asserted rather than widened:
changing who may see it is a decision somebody takes, not a line somebody edits.

4 mutations (**645**), each observed `caught`.



The previous round walked every test against the code it defends. This one asked the opposite
question — *what gets through* — and answered it by building a governed world in the hermetic app
and firing at it: two use cases, seven API keys (bound, unbound, revoked, expired, future-dated,
one bound to a retired use case), memberships by group, by name and by the `/use-cases/<slug>`
convention, and a real `JwtVerifier` behind an RSA key pair. **Roughly 230 cases across nine
groups**, all measured rather than reasoned about. The tabular result is below; what follows first
is the three that were not as designed.

**Most of it was.** Seventeen classic JWT attacks — `alg: none`, the RS256→HS256 confusion with the
public key as the HMAC secret, a forged `iss`, a wrong audience, a missing `exp`, a future `nbf` —
all refused, and a token claiming realm B while signed with realm A's key is refused by the
verifier its own `iss` selected. Twenty-seven key-misuse cases: a revoked key, an expired one, a
key whose prefix is A's and whose secret is B's, a key naming somebody else's use case by header
**and** by path, a key bound to a retired use case. Nineteen bearer/membership cases including the
two the round was asked for — somebody who was never in the use case (`403`) and somebody removed
from the group or from the member list that granted it (`403`, both routes). Eighty-four
route×credential cells on the evidence surfaces, and with real traffic in both use cases, every
credential saw exactly its own rows and `in_scope: false` for the rest. Brute force: five refusals
then `429`, with a valid credential served throughout.

### A pipeline step reached a model nobody chose

`_default_model()` answered *"the first model in the registry"* for any step whose configuration
named none, and three steps asked it. What it returned is a model not released to the use case
(`FRD-308`), not necessarily approved for the installation (`FRD-307`), in whatever region its
adapter serves (`FRD-115`) — and **no gate sits on a step's own model call**. The exemption in
`test_every_dispatch_applies_the_conditions.UNCONDITIONED` justifies itself with *"the model it may
use is bounded by the release, which the pipeline serializer validates every named model against"*,
which is a true sentence about a named model and a silent one about an unnamed one. Management
validates the models a pipeline **names**; a step naming none names nothing to refuse, so the
console's own builder can save it.

Measured: a use case released **only** `mock-embed`, a `pii_filter` with `config: {}`, and the
trace came back `"classifier": "mock-1"` with the caller's text redacted by it — a `200`. Naming
`mock-1` in that same step is a `400`. The whole difference between refused and served was whether
the escape was written down.

Removed rather than governed, because each step already had a defined answer for "no provider
resolved" and each is the safe one: the redactor **blocks** (`FRD-309`), the LLM filter falls back
to the **heuristic**, the router uses its configured `default_model`.

### The dry run had the use case's gate and not the installation's

`released_for` answers `None` for a use case nobody has described — no read-model row, or a row
written by a Management that predates `FRD-308` — and falling through on that is right: an absent
answer is not an absent release. What fell through with it was `FRD-307`, which has **no third
state**. So in exactly the window the third state exists to survive, a caller could name an
unapproved model as a classifier and the endpoint called it: `200` with the model's reply inside
the dry run's own trace, against `400` for the same model on `:generateContent`.

The approval is now asked of the models the endpoint will **call** — a filter's classifier, a
router's classifier, a redactor — which is a smaller set than the release covers, because a dry run
dispatches nothing and never reaches a category's target or the fallback chain. Two gates, two
questions, and the difference is written down rather than left to be rediscovered.

### A router searched its classifier's reply instead of reading it

`name.upper() in answer` — a substring, anywhere, first category in the operator's list wins.
Measured:

| the classifier replied | it routed to | why |
| --- | --- | --- |
| `NONE` | `one` | `ONE` is inside `NONE` — the protocol's own word for *no category* named one |
| `not code — use general` | `code` | list order beat the sentence, and chose the category the model **rejected** |
| `The answer is general or code` | `code` | list order again |

No security hole — the release and the approval still bound where a routed request lands — and the
feature defeated: a `model_route` exists so a cheap question reaches a cheap model. Now: the exact
answer first (a category may be named `c++`, which no word boundary can express), then a
**whole-word** search that must match **exactly one**. A reply naming two has not answered, and
gets the same honest outcome as a reply naming none.

### Closed the same day: an embedding runs no pipeline

`prepare_for_dispatch` runs the pipeline where there is a canonical *generation*, so
`:embedContent` and `/kira/api/external/embed` run no steps at all. `FRD-300` recorded *"Embeddings
filtering"* as a non-goal when the steps were a filter and a router — both about a prompt a model
will answer, where the reasoning holds. `pii_filter` arrived into the same branch a fortnight later
and it is **not the same decision**: its contract is about where the caller's text goes and what is
stored, and an embedding sends the same text to the same class of upstream and writes it to the
same audit row. Measured: one use case, one `pii_filter`, the same sentence — redacted on
`:generateContent`, sent and stored untouched on both embedding verbs, on both surfaces.

First written down and pinned, on the reading that closing it is a feature — an embedding carries
*N* texts (`FRD-113` FR-6), so applying the step is *N* redactor calls per request, which is a
cost, latency and batching decision. The owner's answer was that the control has to be there, so it
is: `TEXT_ONLY_STEPS` (`FRD-309` FR-9 to FR-11).

**A step about the text runs wherever text is sent; a step about the answer does not.** A router
chooses a model to *generate* with and an embedding is not generated; an injection filter is about
a prompt that will be **obeyed**, and an embedding never is — blocking there would refuse a corpus
for quoting the phrases it exists to index. Only the `pii_filter` qualifies today, and the rule is
a named set rather than an `if`, so a fourth step has to answer the question rather than inherit an
answer, which is how this gap arrived in the first place.

The steps are **the same objects** the generation path runs, evaluated over a one-message request
per text: a stand-in more permissive than the thing it replaces is a defect this project has
already paid for, and the redactor's failure rule, its `changed` test, its model, its instruction
and its thinking all stay in one place. Every text is offered, a bounded eight at a time — a batch
may carry 256 and `asyncio.gather` keeps them in the caller's order, which matters more here than
anywhere else in that file because a redaction applied to the wrong text would be silent. One text
that cannot be redacted refuses the whole request: half a batch of vectors is not an answer, and
serving the texts that redacted while dropping the one that did not would send exactly the content
the step exists to withhold, with a 200.

Two shapes are deliberately *not* per text. The decision is **one** for the step, carrying `texts`
and `changed` — counts about the request's shape and never its content, which is what admitted them
to `SAFE_DECISION_KEYS`; a 256-entry column describing one step buries the fact somebody opened the
row for. And the *N* model calls are summed into **one** priced `pipeline:pii_filter` row: the same
figure of money, and a caller's own row not buried under 256 others.

### And an older hole beside it, on both paths

Closing the first turned up the second. `FRD-309` FR-3 promises *"where the substitution cannot be
applied the payload is **dropped**, never kept"*, and only half of it was built: `_rewritten_body`
drops a payload whose text it cannot **match**, and nothing dropped one where the redaction never
**happened** — which is the commoner case by far.

Measured with an unreachable redactor: `400 blocked_by_pipeline` on `:generateContent` and on
`:embedContent`, nobody served, and `request_logs.request_payload` holding the caller's name and
address on both rows. The same shape as F2 the day before — personal data kept in the audit row of
a request nobody was served — arriving through the other door.

`on_failure: allow` drops it too, and that is the part worth stating: the operator who set that
flag chose to keep **serving** when the redactor is down. Keeping **storing** is a second decision,
nobody made it, and one flag meaning both is how a control comes to do something nobody asked for.
The decision row still records the step, the action and why, so the choice stays reviewable; what
goes is the content the step exists to remove.

Thirteen further mutations (**641**). Two survived their first run, and the second is the useful
one: `_worst` — a batch reports the **least good** of its texts — was defended only by a
single-text case, where the first evaluation *is* the failure. The case it exists for is
`on_failure: allow` over a batch whose first text redacts and whose second cannot: taking the first
would report `redacted`, nothing else on the row would say otherwise, and the payload carrying the
text the redactor could not clean would be kept.

### What was measured, by group

| Group | Cases | Result |
| --- | --- | --- |
| JWT verification | 17 | as designed |
| several realms, routing by `iss` | 4 | as designed |
| roles and groups from claims | 6 | as designed (`realm_access` confers nothing, a prefix or a subgroup is not the group) |
| API-key misuse | 27 | as designed |
| bearer membership, incl. removed members | 19 | as designed |
| pipeline combinations, single and multi-stage | 38 | as designed |
| the same pipeline on every verb and both surfaces | 21 | **embeddings run none** |
| dry run against release and approval | 13 | **two escapes, both closed** |
| router reply parsing | 11 | **three misroutes, closed** |
| evidence routes × credentials | 84 | as designed |
| cross-use-case disclosure with real rows | 25 | as designed |
| governance switches, budget, suspension, brute force | 20 | as designed |

9 mutations, each observed `caught`. Two survived their first run and are the reason the harness
exists: a property is only defended where a test looks, and the test for a redactor lived in the
engine's file rather than the redactor's.



Asked whether the product is ready for a real integration, and how to proceed. The first half was
answerable by measurement rather than opinion: `config/integrated.example.yaml` rendered — 86
settings, `environment: production`, a real issuer and audience, Kafka `SASL_SSL`, TLS on OIDC,
JWKS and Vault — and handed to **both planes' own `unsafe_settings`**. With Vault declared and no
secret-id, both fail closed with a message naming what they looked for. Without Vault, exactly two
refusals: `AIRA_SECRET_KEY` and `AIRA_POSTGRES_PASSWORD`. With those supplied, both accept.

Two refusals, both secrets, both of them names the renderer *refuses to write* into a config file.
That is the strongest statement available about such a file: complete, and missing only what it
must never hold.

**The gap was not the product, it was the document.** `docs/DEPLOYMENT.md` — the file somebody
opens for exactly this — knew nothing about the configuration file, `make config-verify`,
`make up-apps` or the compose split. `SETUP.md` §5 had been kept in step; this had not, which is
the two-copies problem in its ordinary form: the copy that is edited stays true and the one nobody
opens rots. So the runbook went in there as **§0**, not into a seventh document.

**And the runbook needed a command it did not have.** Its central step is *check the configuration
before it reaches a machine* — and that existed only as a probe run by hand. A runbook step with no
command is a knob wired to a name that does not exist, which this round had already found twice in
the Makefile. `tools/config_check.py` and `make config-check CONFIG=…` now do it: render the file,
hand it to each plane's own checker in a subprocess holding **only** that environment, and keep
three outcomes apart —

| | |
| --- | --- |
| `!` | the file's own problem, exit 1 |
| `·` | a credential the file deliberately does not carry, split on `config_render.FORBIDDEN` so the two lists cannot drift; Vault's half, not counted against the file |
| `cannot use it` | Vault declared and unusable here — exit 3, neither a pass nor the file's fault |

The third one was folded into the second in the first version, which reported *"2 things this file
has to answer for"* about a machine that simply had no secret-id. Keeping it apart is the whole
value: a check that blames the file for the checker's environment is one a reader learns to ignore.

6 mutations (**613**), each observed `caught` — including the two that matter most, a credential
counted against the file and a subprocess inheriting this process's environment. The second is the
one that would make the check worthless while looking green: `env=None` instead of the rendered
mapping, and a value the deployment will never have fixes the answer.

The runbook names what it does not cover, with the reason: `FRD-127` (several gateway instances —
read it before planning a cutover if availability is required), `FRD-610` §3.2 (a cost budget is
blind to a model with no price) and a stream that cannot fall back once a chunk is on the wire.

---

## `make showcase` on a stack that is not the default one (2026-08-26)

Asked whether the previous round meant `make showcase` now covers everything. It did not, and the
distinction matters: what had been verified was the *sequence of steps the target performs*, run by
hand. The target itself does more — `wait-healthy`, the pull-wait loop, the second seed,
`demo_wait_ready`, `demo_reset_usage`, `demo_traffic`, the realm report, `showcase_agent`,
`showcase_try_it` — and four of those had never been run in this round at all.

**Reading it found two defects before running it.**

- `KEYCLOAK_URL=http://localhost:$${AIRA_KEYCLOAK_PORT:-8080}`. `AIRA_KEYCLOAK_PORT` occurred
  **once in the entire repository**: in that line. Nothing sets it, no Compose file interpolates
  it, no settings class has it. The realm report therefore always went to `8080`, and on a stack
  publishing Keycloak anywhere else it reached nothing — silently, because the line is
  `-`-prefixed. There is an owner for this (`$(KEYCLOAK_URL)`, via `tools/stack_addresses.py`) and
  it was two characters away.
- `docker inspect … aira-ollama-pull` in the pull-wait loop, plus `docker exec aira-ollama` twice
  and `docker exec aira-kafka` once. On a stack with a prefix, `docker inspect` finds nothing, the
  fallback says `gone`, the loop breaks on its first pass and the second seed runs while the model
  is still downloading — **which is exactly what the comment above that loop exists to prevent**,
  reintroduced by the literal underneath it. The result is a demo with no models in it, and it
  starts.

Two guards, both broken on purpose first: an `AIRA_*` name the Makefile reads must be defined by
something (a Compose `${…}`, a settings class, an assignment), and no Makefile or Compose line may
address a container by a literal name — the second had to learn that `image: aira-gateway:${…}` is
an image, not a container, and images deliberately do not move with the stack.

**Then the target itself, twice.** On the default stack: every step, `served 10, refused 1, failed
0` — the refusal is the prompt-injection block, which is the point of it. Then on a **moved** stack
from nothing (`AIRA_STACK=airamoved`, all fourteen ports moved): the same, with the printed URLs
following the move, the realm report reaching `18080`, and — the proof the pull-wait fix works —
**2 models catalogued and approved**, which only happens when the second seed runs after the
download rather than during it. 12 request rows behind the demo traffic. The default stack was
untouched throughout.

3 mutations added (**607**), each observed `caught`.

---

## The second stack that was the first one (2026-08-26)

Asked whether the showcase had been *fully* verified after the compose split. It had not: the
evidence was a `docker compose config` diff, a warm stack that came back up, `showcase_doctor`,
and 161 browser tests — all against a machine whose volumes had been filling for weeks. Nothing
covered the run a colleague actually makes, which is the first one.

So a second stack was brought up beside the first: own project name, own container prefix, all
fourteen ports moved. It came up healthy, every one-shot exited 0, and its Management database was
**empty** while the seed reported success. It had read and written the *first* stack's Postgres.

**`AIRA_STACK` prefixed every container name and nothing else.** `docker-compose.yml` pinned both
the Compose project (`name: aira`) and the network (`networks: aira: name: aira`), and a fixed
network name is shared by every project on the machine. Both stacks joined one bridge, where
`postgres`, `kafka` and `keycloak` each answer for two containers. The variable exists for exactly
one purpose — run a second stack — and it did the visible third of the job. `docs/CONFIGURATION.md`
described it accordingly: *"prefixes every container name"*, which was true and not enough.

A previous round had "proven" the second stack by rendering it and starting one image. That is the
half that cannot fail. The half that fails needs two stacks resolving the same service name at the
same time, which is only visible when both are running and one of them writes.

Both now follow `${AIRA_STACK:-aira}`; without the variable nothing changes, and the running stack
still resolves to `aira`. Three mutations (**604**), each observed `caught` — and the first version
of the guard let one through, because `"name: ${AIRA_STACK:-aira}\n"` matched the *network's*
indented line as a substring: a guard reading its neighbour's evidence.

**Then the cold start, properly isolated, end to end.** From nothing: realm imported with redirect
URIs following the moved console port to `14200` without being told; Vault provisioned; both
schemas migrated; the seed filling its own database (5 use cases, 6 memberships, 4 keys); the model
pulled and catalogued on the second seed; a real generation through the Gemini surface to a real
local model — `promptTokenCount: 23, candidatesTokenCount: 2` — and a complete audit row behind it
(subject, `api_key`, use case, model, status, latency, trace id, stored payloads, `cost_nanos`,
credential fingerprint, provider/publisher/region, `outcome: served`) with the spend charged to a
budget. Then 12 browser tests against the cold console: real Keycloak login, logout, silent token
refresh, a gateway call with the browser's own session token.

**And the teardown was checked rather than trusted.** It removed five use cases from the cold
stack, one of them `demo-uc` — which the *suite* had created there, because `ensureUseCase` only
registers a slug when it had to create it. On the warm stack `demo-uc` predates the suite and was
untouched, as were `kai-test` and `matrix-test`. The scoping holds in both directions.

---

## The compose file that was three files in one (2026-08-26)

The request was plain: the Compose file had grown too big with demo data loading, could the core
be one file and the showcase another. `docker-compose.apps.yml` was **625 lines**, and a third of
it existed for the demo — a development Keycloak realm, a `-dev` Vault refilled on every start,
five seeded accounts. Somebody deploying onto their own Keycloak, Vault and Postgres had to work
out which half applied to them.

**Three files now**, and `deploy/compose/README.md` says which is which: `docker-compose.yml`
(infrastructure), `docker-compose.apps.yml` (**the product**, `make up-apps`), and
`docker-compose.showcase.yml` (the demo around it, `make up-full` / `make showcase`).

Two mechanisms made it possible, both measured on a scratch pair before a line of the real files
moved, because guessing at either would have been expensive:

- **`depends_on` merges additively across `-f` files.** So the two migrations' wait on `vault-init`
  moves *with* `vault-init` into the showcase file, and the core file no longer names it. This also
  answers something the old file complained about in its own comments — that a profile was
  impossible for `vault-init` "because a service two non-profiled services depend on cannot itself
  be behind a profile". True within one file.
- **`extends` reaches across files and carries `environment`.** `management-seed` needs
  `*management-env`, and a YAML anchor does not cross a file. It extends `management-migrate` and
  not `management`, because `extends` carries `ports` too and the seed would have taken a second
  claim on 8002.

**Proof the split changed nothing**: `docker compose config` for every profile, before and after,
differed by three lines — `management-seed` now also waits for `postgres: healthy`, inherited and
correct. Then the stack was actually started: all six one-shots exited 0, `management-seed`
included.

**Two defects the split exposed**, neither of them introduced by it:

- `test_the_stack_comes_back_whole.py` merged services with `merged.update(...)` under a docstring
  claiming "the way Compose merges them". It agreed with Compose only while every service lived in
  exactly one file. The overlay's four-line `gateway-migrate` threw the real definition away and
  two migration jobs read as having no restart policy.
- `test_showcase_is_repeatable.py` had the subset backwards — `theirs <= mine` where the rule is
  *every profile that enables the dependent must also enable the dependency*, `mine <= theirs`. It
  passed because the only pair that could tell the two apart is `management-seed` (`demo`)
  depending on `ollama-pull` (`verify` **or** `demo`), and `ollama-pull` lives in the
  infrastructure file, which that test did not read. Both directions now have a probe.

**Sixteen places named the Compose files by hand** — the Makefile, `config_render`,
`stack_addresses` and thirteen tests. `tools/compose_files.py` is now the single owner (`CORE`,
`SHOWCASE`, `ALL`, `DEMO_ONLY`), the same rule as `tools/stack_addresses.py` one layer up. A
missed caller would not have failed loudly: it would have read two files where three exist and
reported that a variable reaches no container when it reaches one in the file it did not read.

**And the comments came out.** Asked for mid-round: the DEVLOG and FRD material in the Compose
files interests nobody doing an integration. It was 38% of the core file — dates, defect counts,
"missing until 2026-08-10", FRD citations, the story of four wiring bugs. Every block was rewritten
to the operational half: what the setting does, what breaks if it is wrong. The three files went
**997 → 916 lines**, the core file **625 → 402**, and `docker compose config` is byte-identical.
This is the `CLAUDE.md` §6 shape again — the narrative has one home, and a second copy in the file
everybody reads first is the copy that stays true while the real one rots.

8 mutations added (**601**), each observed `caught`, including the one for the merge the split
rests on: layering the showcase must add the dev-Vault edge without dropping the Postgres one, or
the migrations race a database that is not up — intermittently, on a cold start only.

---

## The config file that was first, not above (2026-08-26)

> *"It is important that the configs rank above, so that the integration happens without states
> where the `.env` or the compose file unexpectedly, or through human or configuration error,
> takes over unnoticed."*

The previous round had wired all 86 variables through Compose and stated a precedence — Vault,
then the config file, then a `${VAR:-default}`, then the settings class. That precedence was a
**claim in a README**. Rendering writes the file's values into `deploy/compose/.env`; Compose then
fills every gap it is given. Four ways the deployment ends up running on something nobody chose,
all of them silent, all of them producing a stack that starts and looks healthy:

| | |
| --- | --- |
| a value left empty in the file | Compose's `${VAR:-default}` fires on empty as well as unset |
| a variable the file names that no service takes | the knob turns nothing |
| `.env` edited after rendering | the readable file is the one people edit |
| the source edited without re-rendering | both files are internally consistent, one is stale |

**What was built.** `as_env_file` now stamps the rendered file with its source and a SHA-256 of it,
and `tools/config_render.py --verify` compares source, rendered file, and — through
`docker compose config`, so the answer is Docker's rather than a re-implementation of Docker's
substitution rules — what each container would actually receive. `make config-verify` wraps it and
`make up` / `make up-full` run it before starting, prefixed with `-`: a stack somebody starts by
hand must still start, the message is the point. Two variables the deployment is allowed to decide
are named in `COMPOSE_DECIDES` with their reason, because an exemption is a decision.

**The probe that mattered was the one that stayed quiet.** Replacing
`AIRA_ENFORCE_BUDGETS: ${AIRA_ENFORCE_BUDGETS:-true}` with a literal `"true"` produced no output —
and that was the check being right: the values agreed, so nothing was wrong. Re-run with the config
file saying `false`, the literal was caught twice over, and the static guard that needs no daemon
(`test_every_variable_an_example_renders_reaches_a_container`) named it too. Two nets, no daemon
required for one of them.

**The correction inside the round.** The first version refused *every* unstamped `.env` — including
the demo's, which is hand-made on purpose and has no config file above it to disagree with. That
is a warning on every `make up` of the supported path, which is how a warning stops being read.
But the takeover cannot be told from the demo by looking at the file: the stamp leaves with the
file that carried it. So rendering now also drops `deploy/compose/.aira-config-source` — one path,
never a secret, git-ignored — and the two cases separate: *nothing above me claims otherwise*
versus *config/showcase.example.yaml no longer decides anything*.

**Measured, against the running stack rather than a fixture**: the demo's hand-made `.env` → a
note and exit 0; a fresh render → clean; a literal in the compose file disagreeing with the config
→ `1 difference(s)`, exit 1, `make` reporting `Error 1`. 13 mutations added (**593**), each
observed `caught`, including the two that would make the check silent in exactly the direction that
looks safe. `!integrated.example.yaml` was added to `config/.gitignore`, which had been tracking
the third example while naming only two.

---

## The suite that filled the demo it ran against (2026-08-25)

> *"Clean up the test suite after it has run; the existing use cases that do not belong to the
> suite must not be changed. …And I want a configuration file that points at all the external
> systems that can be connected, as deep as possible but without secrets."*

The first half came out of the previous round, where the showcase was found buried under **1734
use cases in Management and 1946 in the gateway's read-model**, four of them the demo's. The
browser suite calls `createUseCase` ninety times and removed nothing. This project had learned
that lesson for **models** — `removeModel` exists with the sentence *"test residue makes a real
figure meaningless, and the residue never stops accumulating"* — and had not learned it for the
object the suite creates most.

**Remembered, never matched.** `created.ts` writes each slug down at the moment of creation, and
the teardown removes exactly those. No pattern, no prefix, no "everything that looks generated": a
person may name a use case whatever they like, and this demo holds two that a person made. That is
also why `seed_demo --fresh` is not what runs — it deletes everything that is not one of the
demo's own six, which would have taken them.

Written down **before** the attempt, because a creation that succeeds and then fails its URL
assertion has still made a use case, and a register written only on success misses exactly the rows
a failing run produces.

**Retire is the product's path; purge is a demo-only step.** `FRD-607` puts thirty days between
them, and that rule is untouched: the endpoint still refuses, because *"the party who might want
the record gone is not the party who may remove it"*. The teardown retires, which takes the rows
off every screen that lists live use cases. A new management command purges the tombstones, guarded
exactly as `seed_demo` is, and reachable over no URL at all.

### Three things the first version got wrong

- **Make abandons a target at the first failing line**, so the purge was skipped on precisely the
  run that leaves the most behind — the red one. Measured: 68 tombstones and a cleared register,
  which is nothing anybody could name again. The recipe now captures the status, tidies, and
  re-raises it.
- **The register was cleared by the teardown**, one stage too early: the purge is a separate
  process and had nothing left to read. It survives until the purge has run.
- **My own pager guard died of the tidying.** `paging the register moves no column` walked five
  pages, which needed 125 use cases — and the demo had them only because nothing cleaned up. With
  thirteen it went red for want of a pager. Rewritten to filter instead, it went **green with the
  fix removed**, because thirteen short rows are all about as wide as each other. So it brings its
  own row now: one use case with a name far wider than any column, created by the test. Red without
  the fix, green with it, and the same on an empty installation as on a busy one.

  That is this file's own lesson, repeated by the person who wrote it down: *"the pager browser
  guard passed against the unfixed console — it depended on 917 accumulated demo use cases."*

### More than half the file was inert (2026-08-26)

> *"Check how the docker compose and our new configuration relate — are they separate, or do they
> affect each other?"* …*"Everything must be tested and checked for whether it has an effect,
> otherwise there is no point testing it differently."*

They relate through exactly one file, `deploy/compose/.env`, and the coupling was **partial and
silent**. There is no `env_file` anywhere: each service receives a curated list, so a variable the
compose files never interpolate reaches no container at all.

Of the **86** an example renders:

| | |
| --- | --- |
| honoured | 39 |
| never interpolated — reach nobody | 45 |
| assigned a literal in compose, overriding the file | 11 (9 of them also in the row above) |
| **inert** | **47 of 86** |

Measured on the running gateway: `AIRA_CURRENCY`, `AIRA_ENFORCE_BUDGETS`, `AIRA_REQUIRE_USE_CASE`,
`AIRA_LOG_LEVEL` and `AIRA_MAX_REQUEST_BYTES` were simply **absent from the container**, and
`AIRA_POSTGRES_HOST` was hard-coded to `postgres`. Somebody could set `enforce_budgets: false`,
restart, and watch budgets go on being enforced.

**This is not a new failure here.** `docker-compose.apps.yml` complains about it four times in its
own comments — the Ollama timeout, the Gemini model list, two more timeouts, and then the whole
Vertex adapter, which the shipped stack could not configure at all — each time with the same
sentence: *a knob that is not wired is worse than an absent one, somebody turns it and believes the
result.* `test_compose_passes_the_settings_it_names.py` guards it for credentials and upstream
addresses, *"deliberately narrow enough to be true rather than aspirational"*. A file offering 86
knobs is the reason to stop meeting it one variable at a time.

43 variables passed through to the plane whose settings class declares them, each with that
class's own default, so nothing that was true stops being true. Seven literals turned into
overridable defaults. And three the new guard found on its first run: both nginx upstreams, hard
coded in the frontend service, and `AIRA_POSTGRES_USER`, which compose read from a **different
variable** (`POSTGRES_USER`, the Postgres container's own) — so a file naming the setting was
ignored.

**Proved by effect, not by presence**, which is what the owner asked for. `AIRA_MAX_REQUEST_BYTES`
was one of the dead ones: set to 2048 the gateway answers a 5 000-byte body with **413 Request body
too large**; without it the same body reaches authentication and comes back **401**; and on the
commit before this one the name appears in the compose files **zero times**. Then the whole set: a
config with every number and flag given a non-default value, rendered, and `docker compose config`
asked what each service would receive — **86 of 86 carried through**, the one apparent exception
being `AIRA_OIDC_JWKS_URI: ''`, where empty deliberately means *use the in-network default*.

That last one is a trap for somebody else's Keycloak — the issuer is what the browser saw, the JWKS
is fetched by the container, and leaving it empty on your own infrastructure silently points it at
the demo's. Now said in all three examples rather than only in a compose comment.

### Applying the file, which is what actually found the rest (2026-08-26)

> *"I did also ask you to change the variables and run e2e — then you would have noticed."*

Correct, and the correction is the point of this entry. Every check up to here validated the
examples: names against the settings classes, values against the parsers, 196 mutations. **None of
it started the product from the file.** Rendered to `deploy/compose/.env`, the stack brought up on
it alone and the browser suite driven at it, three things came out that no validation could reach.

**1. The example walked into the trap its own repository documents.** The shipped `.env` sets **no**
`AIRA_OIDC_AUDIENCE` — permitted under `AIRA_ENVIRONMENT=local` — and the file sets one. The dev
realm has **zero audience mappers**, so every token carries `aud: ["account"]`, every one is
rejected, and the console loops between the authorization endpoint and the redirect for ever. That
is `INTEGRATIONS.md` §2's *"the audience mapper Keycloak does not add by itself"*, written three
days earlier, walked into by its author.

Fixed by giving the realm the mapper rather than by weakening the example: the realm is ours, and a
demo that relies on `local` being lenient teaches the wrong configuration.

**2. The repair tool made the realm worse.** Forcing a re-import, `keycloak_demo_realm.py` posts the
file to the **admin API**, which stores whatever JSON it is handed — while Keycloak expands
`${NAME:default}` only when *it* reads the file at start-up. The realm came back holding

    redirectUris: ["http://localhost:${AIRA_CONSOLE_PORT:4200}/*"]

literally: authorization answers `400` and the login page never renders. A tool whose purpose is
repair, leaving the realm less usable than it found it — and reachable only by driving a browser at
a realm it had just "fixed". It expands the placeholders itself now, by Keycloak's rule, default
included.

**3. The third category is Compose's own defaults, and that is why none of this fails loudly.**
Enumerating all 139 `AIRA_*` names in tracked code against the 89 settings and the 86 the examples
name leaves *deployment* variables — `AIRA_BIND_HOST`, `AIRA_STACK`, `AIRA_PUBLISH_*_PORT`, the two
worker intervals — and a fourth group with no `AIRA_` prefix at all: what the third-party containers
need for themselves (`POSTGRES_DB`, `KEYCLOAK_ADMIN`). The stack came up regardless, because every
`${VAR:-default}` in the compose files catches the absence. **Nothing fails; something else is used
than what the file says** — which for the console was the realm its image was built against.

`AIRA_OIDC_ISSUER_BASE` and `AIRA_OIDC_REALM` looked like the find and are not: derived properties
of the issuer, with a docstring that says why two settings for one server is two settings to get out
of step.

Ended green: **161 passed** against a stack configured from the file and nothing else, `69 removed`
by the teardown and `69 purged` after it.

### And a second category the same scan could not see (2026-08-26)

Asked rather than reviewed: *"does the frontend also draw its configuration for Keycloak from the
config?"* **It did not.** The issuer was in the file, and only because both planes happen to need it
too; everything that configures the console alone was missing — `AIRA_OIDC_CLIENT_ID`,
`AIRA_CSP_CONNECT_SRC`, the two nginx upstreams and `AIRA_DNS_RESOLVER`.

The consequence is the one `docs/INTEGRATIONS.md` §7 already warns about, arrived at from the other
side: somebody fills the file in, configures both APIs completely, and gets a console that signs
people in at whatever realm its **image** was built against, with a content policy naming the wrong
host — a login that fails in the browser and nowhere else.

The guard was blind for the same reason it was blind to `vault:`. It compares against the settings
classes, and the console is a static bundle behind nginx whose entrypoint writes `runtime-config.js`
at container start: these are real `AIRA_*` variables that no Pydantic class declares. **Two such
categories in one file, each found from outside** — one by the owner's mutation sweep, one by the
owner's question.

`test_the_examples_configure_the_console_too` reads the expectation out of
`10-runtime-config.envsh` and `default.conf.template` themselves. `AIRA_ISSUER_ORIGIN` looks like a
sixth and is not: it is a placeholder inside `index.html` that the entrypoint substitutes with the
origin it derives from the issuer, and it is listed with that reason rather than left to be
rediscovered.

Re-swept afterwards: **196 mutations over 86 variables, all noticed.**

### What the mutation sweep and the coverage figure each turned up

The owner asked for the file to be gone through variable by variable — *"change the variable and
see whether it hits, so we know you have covered everything"*. **186 mutations over 81 variables**:
rename the key, remove it from every example at once, give it a value of the wrong type.

**179 were noticed and seven were not, all of them the `vault:` section.** The cause was a correct
decision with an unnoticed consequence: `VAULT_*` is exempt from the reality check because those
names are read *before* the settings exist, and the completeness check walks the settings classes —
so between them, the one section pointing at the secret store was unguarded. It could have vanished
from both examples with nothing to say so, leaving an integrator who had configured everything
except where the credentials come from. Four checks close it, each reading its truth out of
`secrets.py` and `VaultConfig.from_env` rather than from a list kept here. Second run: 186 of 186.

*And the sweep cost a file.* Its first version wrote the YAML in place and kept the original in
memory, restored in a `finally` — which a `SIGKILL` walks past. It did, and 250 lines of comment
went with it, which git could not return because `config/` is deliberately untracked. Rebuilt from
the intact sister file; the second version copies to disk first. The docstring had named the danger
without guarding it, which is the shape this repository calls a written-down danger.

**And the coverage figure found the untested destructive command.** 95.72% → 95.27% after
`purge_test_use_cases` was added, which is the only reason anybody looked: a command whose one
safety rail is a demo-mode guard, and nothing broke that rail on purpose. Eight tests now do.

Writing them found something larger. `test_a_retired_use_case_is_purged_and_announced` passed alone
and failed in the suite, with `_subscribers` **empty**: two fixtures ended with
`events._subscribers.clear()` to remove their own spy, taking the outbox subscriber
`OutboxConfig.ready()` registers at start-up with it — permanently, for the rest of the session. So
in a full run, no use-case event reached the outbox at all. A teardown removes what it added.

## One file that points at every external system (2026-08-25)

`config/`, with two examples and everything else invisible to git — `*` with named exceptions,
which is the safe direction: a `.gitignore` that lists what to hide misses the file created last
Tuesday. An installation file holds hostnames, project ids and account names; none of that is a
secret in the sense Vault means, and none of it is anybody else's business.

Two levels, and the shape is the mapping: a section is a prefix, a key completes it, so
`postgres.host` is `AIRA_POSTGRES_HOST`. `core:` is unprefixed for settings that belong to no
system, and `vault:` becomes `VAULT_*` because those are read before any settings object exists.
**78 variables out of one file.**

`tools/config_render.py` renders into `deploy/compose/.env` — into the contract both planes already
read, rather than beside it. A YAML settings source would have been a *fourth* answer to "where
does this value come from", read at a different moment on each plane.

**Secrets are refused, not requested.** Asking for that in a comment is what `LESSONS.md` calls a
written-down danger; the renderer raises, by name, with where the value belongs instead.

The guard checks the examples against the settings classes at four levels, and found **seven real
errors in the file its author had just written**: two invented keys and five settings the file had
never heard of. The fourth level is the one that makes this a working file rather than a document —
the rendered environment is handed to both planes' settings objects with the environment otherwise
**empty**, so a port that is not a number or a role map the parser refuses fails here instead of at
somebody's first boot. Empty on purpose: merging the developer's own `AIRA_*` is how *"it works on a
machine that has already done the thing by hand"* gets shipped.

## One setting, three contradictions, and a field invented as a null (2026-08-25)

The two the previous round recorded as noticed-and-not-fixed. Both turned out to be the shape of
the round that found them: **a fact with two statements of it.**

**`AIRA_CURRENCY` had exactly one reader in the whole system** — the reporting CSV's column header,
on the gateway — while three console screens said *US dollars* in so many words: the model catalog's
price paragraph, the use-case budget window, and the installation's. The setting defaults to `EUR`.
A German installation on defaults therefore invited somebody to type dollars into a form and handed
them back a file that said euros.

The console's argument was written down and is a claim about **vendors** rather than about an
installation: *"every provider on this gateway prices in dollars"*. A reseller contract in euros
makes it false, and nothing would have said so.

`/v1/me` carries it now, beside the API-key policy that is already there for the same stated reason
— *"so the console states the numbers the server enforces instead of carrying its own copy"* — and
`MeService` holds it as a signal three screens read. Empty until the first response renders **no
unit at all**: an unlabelled amount is a reader who asks, a wrongly labelled one is a reader who does
not. The generated OpenCode configuration uses the same fact to decide something else — prices are
written only when the installation prices in USD, because OpenCode prints its running total with a
`$` and `AIRA_CURRENCY`'s own comment refuses exchange rates.

Two things worth keeping from the doing of it. **Seven spec files stub `MeService`, and every double
was less capable than the thing it replaced** — the moment the service grew a member, 144 tests
failed on a template error a long way from anything they were testing; `CLAUDE.md` §3 warns about a
stand-in that is *more* permissive, and this is the same trap facing the other way. And the label
came out as `Spend limit  (CHF)`, two spaces, because `Spend limit` and `({{ unit }})` across a
control-flow block render with the block's own whitespace: **a label is one string**, and a template
should interpolate it rather than assemble it.

**`thoughtsTokenCount` was sent as `null`** on the buffered exit, where the field documents itself as
*"omitted when zero … because Google omits it for a model that did not think and a compatibility
surface should not invent a field the original leaves out"*. A null is that invention wearing a
different value.

The existing test dumped a hand-built `UsageMetadata` with `exclude_none` and checked the key was
gone — which proves the *schema* can do it. Its own comment records the previous version of the same
mistake, *"the first version of this asserted that the field exists and is omitted when empty — both
true with the mapping handing over `None`, so the mutation that stopped filling it survived"*. This
is that lesson one level further out: what a caller receives is decided by the **route**, so the
route is what a real request asks now. It is the third exit of that file to need `exclude_none` —
streamed since `FRD-100`, the model list yesterday — which makes "a fact stated at one exit and
missing from another" the file's oldest recurring shape rather than an observation about any one of
them.

Three mutations red before either was believed: the buffered exit sending null again, `MeView`
answering a literal `USD`, and the budget label hard-coding its unit. Frontend 946; Python 95.7%.

## The gauge was reading a number nobody had written down (2026-08-25)

> *"When I start OpenCode and connect it to AIRA, I can see that I am talking to AIRA over the
> Gemini interface, but I cannot see how many tokens were used, and the limits are always at 0%. Do
> we have the possibility of supplying that information over the official Gemini interface? …The
> interface should stay like the official one — if it cannot be done, it cannot be done."*

Answered by installing OpenCode and reading both sides, which was the right call: reasoning about it
gave the wrong answer twice, and the second wrong answer was mine after I had already measured once.

**The tokens were never missing.** A real `opencode run` against `qwen3:0.6b` through the Gemini
surface — AIRA's audit row `2050 / 26 / 2076`, and OpenCode's own store
`{"input": 2050, "output": 26, "total": 2076}`. `usageMetadata` is emitted on both exits,
`@ai-sdk/google` reads it, and the two planes agree to the token.

The streamed shape *is* wrong against Google and turned out not to matter. With an OpenAI-dialect
upstream the totals arrive on a trailing chunk with no `finishReason`, and every earlier chunk
carries zeros — the dialect's own convention relayed through a Gemini-shaped fassade. I was ready
to call that the defect. The SDK takes the last usage it sees, so it is not; fixing it means holding
the finish chunk, and a latency cost on every stream to tidy a shape no client misreads is the wrong
trade until one does. Recorded in `FRD-132` §11.1, not changed.

**What was zero was everything OpenCode never asks the API for.** Its resolved model read
`limit: {context: 0, output: 0}` and `cost: {input: 0, output: 0}` — both from its *configuration
file*, and the file the console generates at key issuance carried only a display name. A context
gauge is `used / limit.context`. The hand-written harness in `tools/opencode/` has had
`"limit": {"context": 32768, "output": 4096}` since `FRD-132` stage A; the generated one never
inherited it, and nothing compared the two.

So the honest answer to the owner's question is that for this client it does not arise: the fix is
in a file, not in the surface, and the constraint they set is met by construction.

**But the interface had drifted, and that half is real.** The official Gemini model resource carries
`inputTokenLimit` and `outputTokenLimit`. AIRA published the second under an **invented** name,
`airaMaxOutputTokens`, sitting next to the standard one it was not — and the first not at all. A
client written against Google reads neither. Both standard fields are now filled, `airaMaxOutputTokens`
stays beside its replacement (withdrawing a field a caller has read since `FRD-114` is not a tidy-up
a compatibility surface gets to perform), and the list endpoint dumps with `exclude_none` — because
Google omits a limit it has no figure for and a `0` is not "unknown" to a client, it is a full
context window. The streamed exit has done that since `FRD-100` and this one had not: the same fact
at two exits disagreeing, one more time.

**And a field that did not exist.** Neither plane had a context window, so there was nothing to fill
either `limit.context` or `inputTokenLimit` from. `context_window` now travels the ordinary route —
model editor → serializer → model event → read-model → Gemini resource → generated config. Nullable,
nothing backfilled, **not enforced**: the upstream refuses what does not fit, and a second copy of
that ceiling here would be one more thing to keep true. One rule comes with it, `max_output_tokens`
may not exceed it, because the answer is drawn from the same window as the prompt.

The local seed declares `40960`, which is the number its own comment had already been explaining:
on Ollama the window *is* the output ceiling, and until now there was nowhere to say so.

Proved end to end rather than argued: Management row → outbox → Kafka → gateway read-model → the
Gemini resource answering `inputTokenLimit: 40960`, and OpenCode then resolving
`limit: {context: 40960, output: 40960}` where it had resolved zeros.

**The dependency the owner asked me to check turned out to be missing entirely.** Management's
`_payload` and the consumer's `_DECLARATION_DEFAULTS` are hand-written lists in two languages, and
**nothing compared them** — so adding a field to one and forgetting the other is completely silent:
console offers it, database stores it, Kafka carries it, read-model does not have it.
`tools/tests/test_a_model_event_is_applied_whole.py` now fails in both directions, and its own
vacuity assertion caught the first version reading nothing at all, because `async def` is a
different AST node than `def`.

Two things noted and not fixed, both in `FRD-132` §11.5. The model editor tells whoever types a
price it is **US dollars**; `AIRA_CURRENCY` defaults to **EUR** and labels the same numbers in every
report — a contradiction older than this round. And `thoughtsTokenCount` is sent as `null` on the
buffered exit where the field's own comment says it is omitted, which is the same `exclude_none`
question one response along.

Python 95.72%; frontend 941; three mutations on the new properties all went red, plus both
directions of the event guard.

## A class that is spelled wrong renders (2026-08-24)

> *"We have had a few problems in the interface — try to smooth them out. In some places elements
> in windows are put side by side instead of packing them underneath one another, in some places
> elements are so close together that they have no margin. Try to find all of these and smooth
> them out."*

A sweep, measured in a real browser rather than looked at: every route at four widths under three
roles, and every window opened by its own trigger. Two probes — one grouping a window's `.field`
elements by the line they sit on, one comparing the boxes of adjacent siblings.

**Side by side in a window: exactly one dialog, and it took two fixes.** Eight of the nine windows
stack. The ninth is `rule-form`, which appears twice — the use case's rules tab and the global rule
editor — and it laid out *Watch for* 551px beside *Raised about* 263px, then *Above*, *Over
(minutes)* and *Smallest sample* all on one line.

The interesting part is why the rule that was supposed to prevent this did not, because it failed
twice for different reasons:

1. The selector read `.modal__body form.form-inline > .field`. The `form.` was written to exempt a
   `.form-inline` nested in a fieldset — a nested one is a `div`, HTML forbids nesting forms — and
   it therefore exempts *every* form whose fields live one level down. `rule-form` is
   `form.form-stack > div.form-inline`, and the outer column exists for a good reason recorded in
   the stylesheet: it keeps "Create rule" off the field row. **The one window that separated its
   actions properly was the one window whose fields were never stacked.** The exemption now names a
   `fieldset`, which is a thing somebody wrote on purpose, rather than `form` versus `div`, which is
   a coincidence of nesting.
2. Widening it changed nothing, and the browser said why: `.form-stack .form-inline` is a **grid**,
   and a grid item ignores `flex-basis`. That grid was itself the fix for *"a rule editor put five
   controls in a row on a wide screen"* — right on a page, and in an 880px window it is the same
   complaint one size down. So the grid keeps its equal widths and gets one column in a window.
   Then it *still* did not stack, and the browser said why again: `.field.grow` asks for
   `grid-column: span 2`, which beside a single-column template does not widen anything — it makes
   the grid invent an implicit second track. `grid-template-columns` computed as
   `527.703px 286.297px`. Three rounds, each one measured, none guessable from the source.

**No margin: three, and then a whole class of them.** The gap probe found `/requests` and
`/pipeline-tests` with their heading flush against the content at 0px, and on the second the tab
strip flush against the card as well. The cause was not a missing margin. Both pages open with
`<div class="page"><header class="page__head">` and an `<h2 class="page__title">` — **none of the
three exists in any stylesheet**. Every other page uses `.stack`, which gives `gap: 1rem`; these two
were laid out by whatever the browser does with a bare `div`.

Which is a mistake nothing here could catch. A misspelled class does not fail — it renders, and the
page looks *nearly* right. Angular does not warn, `tsc` never sees a `class` attribute, and this
project has no ESLint. So the scan became a test, and it found five more: `.hint` twice where the
console's small muted line is `.field__hint`; `.badge--ok` on the *Active* rate-limit badge where
the green one is `.badge--success`; `.table-scroll` twice on the connection panel where the scroll
container is `.table-wrap`, so two tables had none; and `.right` on a cell of actions where the
right-aligned one is `.table__actions`. Six names, four files, all rendering.

`tools/tests/test_every_class_the_console_uses_exists.py` now fails on a class no stylesheet
defines, with an `ALLOWED` list of the fourteen names that are on an element for some other reason
— each with that reason, and a second assertion that deletes itself when an entry stops being used.
An allow-list of fourteen on day one is a weak guard and a reviewed one, which is the whole
difference from the state before it.

The third finding was a red warning callout 4px under its heading on the model-release panel. Fixed
as a rule — `.section-title + .callout` — rather than as a margin on that paragraph: a heading sits
close to its caption on purpose, and a bordered box at caption distance reads as attached to the
heading instead of introduced by it.

**What the sweep did not find**, worth recording because it was looked for: no overlapping elements
at any of four widths under any of three roles, and no other touching pair. The ten remaining
zero-gap pairs are a window's head, body and foot, which carry their own padding and a rule between
them. The six remaining four-pixel pairs are all a heading and its `p.muted` caption, which is the
distance that is correct.

Both guards were broken on purpose before being believed: the window guard goes red with either
half of the grid fix removed, and the class guard goes red both on a reintroduced `.badge--ok` and
on an `ALLOWED` entry no template uses.

## Nine columns, and the jiggle that was not where anybody looked (2026-08-21)

> *"For me the register UI is a bit too cluttered. I would like collapsible elements like the
> requests have — only the most necessary information on top, and everything visible when expanded.
> But it is also important that the elements do not jiggle when expanded and that everything is
> stable."*

The register shipped the same morning and was reported the first time it was opened. Both halves of
the report were right, and the second half was right about something other than what it named.

**The clutter.** `FRD-608` §2.1 lists eleven columns a governance reader needs, and I rendered all
eleven. That is the wrong reading of my own argument: the section's case for the screen is that
governance is a **comparison** activity, and eleven columns is not more information — it is the same
information arranged so that none of it can be scanned. The row now carries what rows are compared
*by* — use case, purpose cut to a line, whether prompts are kept and for how long, and any finding —
and the rest opens in place, on the request list's mechanic (`FRD-505`). Several rows open at once,
where the request list keeps one: opening a request fetches its payload, and here everything is
already loaded, so *these two side by side* is a question the screen can now be asked.

The findings column stays on the closed row. A finding you have to open a row to see is a finding
nobody reads.

**The jiggle, measured.** Three plausible causes, and the one everybody assumes is not the one that
happens. Each was measured in a real browser, with the fix removed and the frontend rebuilt.

1. *An opened detail resizes the table* — **no.** A cell spanning every column widens a table only
   when it is wider than their sum, and a detail of wrapped prose never is. The guard I wrote for
   this passed with `table-layout: fixed` removed, then passed again with the `<colgroup>` removed
   too, then passed with all twenty-five rows open instead of one. Three rebuilds to establish that
   the test proved nothing — which is exactly the trap `CLAUDE.md` names: *a test whose setup never
   reaches the path it is named after.* It is kept, and its comment now says it guards a property
   rather than a mechanism.
2. *Paging resizes the table* — **yes, and this is the defect.** An automatic table sizes its
   columns from the rows it is currently holding, so twenty-five different use cases give
   twenty-five different column widths. Measured: `[52, 266, 152, 199, 185]` on page one against
   `[52, 240, 159, 209, 194]` on page three. Every column jumps on a click of Next, on the one
   screen whose purpose is reading down a column. A `<colgroup>` and `table-layout: fixed` fix it —
   either alone is sufficient, and they are both there because they do different jobs: the colgroup
   is where the proportions live, `fixed` is what stops an unbreakable model id overriding them.
3. *A page that gains a scrollbar reflows narrower* — **yes, and not provable here.** ~15px lost the
   moment a short page grows past the viewport, re-laying out every percentage width on it; the same
   failure `InfoHint`'s panel was rewritten for, where it could also loop. `scrollbar-gutter: stable`
   on the root fixes it for the whole console. Headless Chromium draws **overlay** scrollbars, so
   `clientWidth` measured 1280 on the register at viewport heights of 400, 3000, 6000 and 9000
   alike — there is no width to lose and nothing for an assertion to catch. It is guarded in
   `tools/tests/test_a_growing_page_does_not_reflow.py`, which asserts the declaration and states
   plainly that it asserts the declaration.

The second guard is the one that earned its place: it also only worked after being sharpened. The
first version clicked Next **once**, and pages one and two happen to hold use cases of similar
length — green against the broken build. It walks five pages now, and names the page it parted on.

The caret is one glyph rotated rather than `▸`/`▾` swapped, because the two do not measure the same
in the fonts a console is read in. The request list swaps them; that is a defect there too, one row
wide and constant.

Five unit mutations were run against the new guards before any of this was believed — the row
carrying the detail again, the caret swapped, a `<col>` removed, the detail spanning the wrong
number of columns, and one-row-at-a-time — and all five went red. Frontend 932 (branch coverage was
the thing that noticed the detail's own branches had gone unrendered: 91.71% against a gate of 92%,
fixed with four tests that open a row rather than by moving the gate). Browser 159. Python 95.71%,
unchanged and untouched.

## A register of processing activities, and residency that is measured (2026-08-21)

> *"There is still no overview for IT Steuerung where they can list use cases, the description in
> them, the models used, all the controls like how many days data is stored and so on, and generally
> how the data processing happens. I do not know yet whether read access to all use cases is enough,
> or whether a separate view or CSV export would do."*

`FRD-608`, built. The owner's uncertainty was the right one to have, and the answer was unusual:
**two of the three things they were unsure about already existed.** IT Steuerung already saw every
use case (`OVERSIGHT_ROLES`), and every field was already stored — `description`, `processing_notes`,
`store_payloads`, `retention_days`, `allowed_models`, the three capability flags,
`restrict_members_to_own_requests`. What did not exist was the **shape**: `use-case-list.html`
rendered two columns, and everything else was one click deeper, one use case at a time.

That distinction decided the whole design. Governance is a **comparison** activity — *which use
cases store prompts? which keep them longer than thirty days? which were processed outside the EU?*
— and none of those is answered by opening forty detail pages. So the register is a *reading* of
data the system already holds, and not a new datapath: nothing in it writes, nothing is authored,
and no field exists because the register wanted it.

**Served by the gateway, although Management authors every configuration field in it.** The gateway
is where the two halves meet: `UseCaseRead` already carried the whole configuration, and the audit
trail is the measurement. Assembling it in Management would have meant shipping the audit trail
across the planes to reach a half that was already on this side. The route sits behind
`api/reporting.py`'s heading for the reason the suspension endpoints were moved **out** of it, read
the other way: those left because they are bounded by *role*; this stays because it is bounded by
**use case**, by the very same `visible_scope`. Two ways of being safe do not share a file; two
endpoints safe the same way should.

**The measured half is the point.** Every audit row has carried the region a request actually went
to since `FRD-115` FR-10, and nothing read it. The register now puts *where processing happened*
beside *where the configuration says it may*, per use case and installation-wide, and names the
difference: `unexpected_regions`. `FRD-611` closed the **configuration** door on `global`; this is
the **measurement**, and a model catalogued in a permitted region and served from another would
still be invisible without it.

Two rules the measurement needed, both the same rule this project keeps applying to money. *Unknown
is not a violation*: a row with no region is reported under its provider and never counted as a
transfer, because most dialects address a model by name and the mock and local providers run in the
container — counting those would make the finding column always red, which is the reliable way to
have a finding ignored. And *nothing to compare is not a finding*: a use case whose models name no
region has no configured residency to disagree with.

**Erasure as evidence** (§2.4). `RetentionService.prune` has returned `payloads_cleared` and
`rows_deleted` since `FRD-404` and **nothing read them** — they went into a log line and out of
reach. A new `retention_runs` table (migration `0041`) records each pass, written inside the same
transaction as the deletions it describes: a record of an erasure that could commit while the
erasure rolled back would be worse than none, because somebody would believe it. The register prints
the last pass, or says there is no record — never a zero, which would read as *the sweep ran and
found nothing*.

**And the two planes compared** (§4). Both keep a catalogue, one feeds the other over Kafka, and
nothing compared them: the pass that wrote this FRD found `mock-1` in the gateway's read-model with
no row in Management — a model it could serve that no screen showed and no role could remove. The
register reports the gateway's list, the console holds Management's, and the screen says when they
disagree in either direction. Use cases are **not** compared, and the FRD says why: at the 917 rows
one installation already has, that is a paging question rather than a rendering one.

Three of the owner's five open questions are answered in the FRD, two from the document's own
argument. Retired use cases are in the register by default and marked — they are still processing
records for as long as their payloads exist. The exports are produced on demand rather than kept:
keeping them would make the system the custodian of its own compliance record, which has a retention
question of its own. *Per organisational unit* stays open and is **not buildable as asked** — there
is no such field in the data model, and a search box over name, id, purpose and processing is what a
deployment gets until there is.

**One thing the browser layer taught rather than confirmed.** A use case created a second ago is in
Management and not yet in the register, because the register reads the gateway and the gateway
learns over Kafka. That is not a defect to paper over: for a register it is the more honest of the
two readings — it describes what is *in force* rather than what was last typed — and the test polls
for it and says so.

**And a guard caught me writing the exact defect it exists for.** The four info hints on the new
screen were written as `text="…"` attributes, which Angular ignores, so every one of them would have
opened an empty panel. `LESSONS.md` §1 lists that under *a badge-wearing absent control*, and
`test_console_info_hints.py` was written after it happened. It failed on the first run of the new
page — which is what a guard is for, and the reason this one is worth the twenty lines it costs.

Gateway: `reporting/register.py`, `reporting/register_csv.py`, one route, one table, one migration.
Console: a `Register` screen for an oversight role. 28 hermetic tests, 15 component tests, 6 browser
tests; the whole browser suite, the Python suite and both linters green.

---

## Half a stack, and a form that re-packs under the reader (2026-08-21)

> *"Can you go through all the fields in the UI, type something in and look for odd behaviour? We
> once had the button layout change in a strange way when the elements in a field were adjusted."*
> And: *"I cannot look at the interface right now, because Keycloak is not reachable."*

**Keycloak was not broken. Half the stack was missing, and the half that was there is the half that
answers.** Every infrastructure container had `Exited (255)` at `16:20:08.93Z` — the same
millisecond, which is what a daemon shutdown looks like — and the gateway had started again at
`16:20:11.13Z`. `docker-compose.apps.yml` has always said `restart: unless-stopped`;
`docker-compose.yml` said nothing at all, and Compose's default is `no`. So the console answered,
both planes answered, the gateway's container reported `healthy` for five hours without a database,
`gateway-consumer` crash-looped against the absent Postgres, and nobody could log in.

The asymmetry was **already written down** — `test_compose_lifecycle_covers_the_stack.py` says *"the
application services carry `restart: unless-stopped` while the infrastructure does not, so they also
come back"* — as an aggravating detail of the `make down` defect. Nobody asked what the same
asymmetry does to a **host restart**, which is the more ordinary of the two events. A fact noticed
in passing is not a fact anybody is holding. All nine long-running infrastructure services declare
the policy now, and `test_the_stack_comes_back_whole.py` derives both halves of the rule from the
compose model's own dependency graph: a service that comes back may not depend on one that stays
down, transitively; a **job** is stepped through in that walk and must stay `no`, because a
migration that re-ran on every boot is a different bug. What a job *is* is read off the graph too —
every edge pointing at it carries `service_completed_successfully`.

**Then the interface, swept rather than read.** Every field on every route, every tab of a use case
and every window reachable from a button: five values into each text field, every option of each
select, every checkbox — with the geometry of every visible button measured before and after. Two
findings, and the second is the reported symptom exactly.

**A form that re-packs when a note appears.** Choosing a per-head scope in the budget window
inserts an explanatory paragraph, and `.form-inline` is a *wrapping* row: the paragraph does not
push the fields down, the row re-packs around it. Measured — `Period` and `Spend limit` **404 px to
the left**, `Token limit` 350 px to the right, `Applies to` from 394 px to 617 px. The rate-limit
window does the same to `Burst`. Nothing is misplaced at either moment; the form answers a different
wrap question before and after, and the reader's target moves out from under them.

There was already a rule against this — *"One question per row, in a window the width of a form"* —
written after the model editor was reported twice. **Both halves of its selector were narrower than
the rule.** `.modal--steady` appears **once** in the entire console, and `.modal__body > form`
matches only a window that writes its own markup: every window built the intended way, through
`<app-modal>`, projects a `<div modal-body>` wrapper and has no input for a class, so it was outside
the rule *by construction*. The rule's own comment explains that the per-field version failed
because it "has to be remembered at every field added afterwards" — per-window is the same sentence
one level up, and it was forgotten at every window there is. It is `.modal__body form.form-inline >
.field` now: a property of *a form in a window*, which is something the stylesheet can see without
being told. `.modal--steady` keeps only what it is really about, which is a **tabbed** dialog not
resizing as its tabs switch.

**And the button that moves out from under the cursor that pressed it.** `Check reachability` puts
its verdict in the footer beside itself; with `justify-content: flex-end` the new badge pushed the
button **101 px left**. Cancel and Save were fine — they are the two the alignment pins. The console
already had the answer, `form-actions__spacer`, which the smoke-test footer uses to keep a progress
message off its buttons; this footer did not use it. An existing answer applied in one place and not
the other.

**What held.** The footer defect fixed in August stays fixed: a validation message, a cleared field
and a sixty-character model name move `Cancel` and `Save` by `dx=0, dw=0`. The pipeline builder
survived adding all three step types and long values in every field with nothing moving. The
reporting table's column hints do move when the breakdown changes — that is a table whose columns
size to their contents, and it is not a defect.

**Found while running the checks rather than by looking for it:** `make lint-frontend` was **red on
`main`**. The browser suite's `tsc --noEmit` was wired up on 2026-08-18 for exactly the reason it
exists — *"a rule only a reviewer enforces is one the next file breaks"* — and
`installation-budget.spec.ts` landed on 2026-08-20 calling `expectNoHorizontalOverflow(page)` with
one argument of the two. The gate was added and then not run. One call site, one context string.

**What the fix cost, and where it was paid.** Making the stacking rule unconditional took the
budget window out of `every inline form lines its controls up` — the guard reported *"nothing with
two controls on a line was found to compare"*, which is exactly what it is written to say when its
subject stops having rows. That test has been here twice before: the create form and the issue-key
window both became stacks and both left it with a comment saying so. The budget window leaves the
same way, and the reporting filter row — a preset, two dates, a search and a breakdown on one line,
the widest inline form the console has — takes its place, so the guard keeps something wide to
compare instead of shrinking. Pointing it there needed an explicit wait for `#export-breakdown`:
the helper's own wait is satisfied by the preset form above it, and the breakdown picker renders
once the figures arrive. That is the race its docstring warns about, met from the other side —
because it refuses a vacuous pass now, it failed instead of quietly checking nothing.

**Three failures in the same run were the machine, and were checked rather than assumed.**
`catalog-import` twice and `agent-traces` once, and neither touches anything this round changed:
`/readyz` said `degraded: true — upstream reachability: unreachable: local`. `make up` brings up
infrastructure and observability; **Ollama is behind the `demo` profile**, so a stack recovered with
`make up` alone has no local model, the provider listing is empty and a generation is a `502`.
Started it, re-ran the three, all green. Worth writing down because the symptom points at the
console — an empty picker and a failed request — and the cause is a profile that was not asked for.

**The sweep was then re-run under every role**, because the first one proved less than it looked
like: it printed findings and not coverage, so *clean* and *never reached* were the same output.
Rebuilt to list every control it touches, it covered **192 contexts** — four roles, two widths,
every route, all eight use-case tabs, every window reachable from a button, and the model editor's
three tabs. Nothing moves anywhere except the reporting table's column hints, which is a table
sizing its columns to its contents.

Two things came out of that pass. One was checked and dismissed: a use-case **user** is offered an
editable *Issue key* window while every other panel is read-only for them, which looks like an
authority hole and is a decision both planes state — *"only members of this use case may issue one
— seeing a use case is deliberately not enough"* — with revocation kept to administrators.

The other was real and is the third of the family: **the pager's position label sat between its two
buttons.** The group is pinned right, so `Next` held still and a page count that gains or loses a
digit pushed `Previous` — 8 px, on the use-case list under a role that sees enough use cases to have
more than one page. The label reads before both buttons now, and `.pager__controls` gets
`margin-left: auto` so the anchor is the same one when the row wraps onto its own line.

**Its guard is a component test, and the reason is the more useful half of this entry.** The
browser version was written first and *passed against the unfixed console on the first try*:
reproducing the pixel needs a list long enough to page **and** a search term that changes the page
count's digit width, which is a fact about how much demo data the machine happens to hold — 917 use
cases here, all of it debris from earlier runs. That is `LESSONS.md`'s *"it works on a machine that
has already done the thing by hand"*, arriving in the guard rather than in the code. What the fix
actually establishes is that **nothing sits between the two buttons**, which is true of the markup
whatever the data, so that is what is asserted. The pixel measurement stays in this log, where a
measurement belongs.

Three browser guards added and one component guard, each watched go red against the unfixed console
and green against the rebuilt one; one compose guard, watched red against the original file. The
sweeps themselves were exploratory and are not kept — what they found is.

---

## What a value nobody meant to send does: seven findings (2026-08-20)

> *"Go through the tests systematically and check against the code whether they cover real
> functionality. If you find a bug, actually try to break the place; if you manage it, fix it and
> prove it with tests. No functionality may be lost. Be very careful."*

A second pass the same day, and deliberately a different **question** from the one before it. The
previous round asked *does the code hold the rules it states*. This one asked *what does each
boundary do with a value nobody meant to send* — and that question is answered by driving values in,
not by reading. Seven findings, none of them visible to the green hermetic suite the entry below
signs off, and five of them were found by a **sweep** rather than by a hunch.

**One malformed rule switched the whole detector off, permanently.** `evaluate_rule` guards `kind`
against a word this build does not implement, at length and for the right reason — *"a newer
Management can publish a kind this gateway does not implement"*. `target` and `action`, which
`consumer.apply` writes out of the same Kafka payload with no enum and no default, were coerced
unguarded: `_GROUP_BY[RuleTarget(rule.target)]` at three call sites and `RuleAction(rule.action)` at
one. A `ValueError` out of any of them leaves `tick` — which has no per-rule boundary — so the round
dies, the watermark deliberately does not move, and the **next** tick re-reads the same row and dies
in the same place. Every other rule in the installation goes unevaluated for ever, with
`anomaly_tick_failed` in a log and a console still showing every rule as enabled. The two guards are
now beside each other; the finding is still written, and an action nothing can carry out is recorded
as `detected_not_enforced`, which is what `_enforce` already says for that case.

**A chain that changed the model name and not the address.** `dispatch_with_fallback` re-points a
request with `model_copy(update={"model": model})`, and `addressing` — filled once, before the
chain, from the *routed* model's declaration — travelled unchanged to every hop after the first.
Invisible in every dialect where a model name is the whole address, and on Vertex `addressing` is
the **region list**: a fallback catalogued in `europe-west4` was addressed at the primary's
`europe-west1`, answered *not deployed here*, and the failover loop then walked the primary's
remaining regions, all equally wrong. The other direction refuses instead of misrouting — behind a
primary that carries no addressing at all, a Vertex fallback the catalogue names a region for was
refused with *"catalogued for this platform and says no region"*. `declared_provider` already
fetched that declaration and returned two thirds of it, so the fix is the same shape as its own
docstring: one `Routing` value carrying provider, publisher **and** addressing, named
`declared_routing` because it is no longer about a provider.

**Two exports a spreadsheet would execute.** A cell beginning with `=`, `+`, `-` or `@` is a formula
to Excel, LibreOffice and Google Sheets, and the `key` column of the usage export is caller content:
`AuditTrail.served_model` falls back to `requested_model` for a request that never reached a model,
so a `404 model_not_found` carries the string out of the URL. Measured: one refused request for a
model named `=1+1`, and the month's export by model carries `=1+1,1,0,…` as its first data row — a
file every oversight role can download. Management's smoke-test export has the same hole with a
lower bar: its `response` column is a **model's own answer**, and a model asked for a spreadsheet
formula gives you one. That file was written after the usage export and says so — *"the same
conventions `FRD-602` had to get right once already"* — and it copied the BOM, the CRLF and the
quoting, because those were there to copy. `aira_common.spreadsheet` owns the rule for both now:
prefixed with `'`, which no spreadsheet displays, and deliberately neither stripped nor refused — a
row quietly missing from a governance document is the worse failure.

**Four caller values that arrived as `500`.** Found by sweeping every console endpoint with the same
short list of wrong values rather than by asking about a field:

- `POST /v1beta/suspensions` and `:checkThinking` read their body with a bare `await
  request.json()`. A stray brace answered `500` on the two endpoints somebody reaches for while
  something is going wrong — while `api/pipeline.py` and both API surfaces already spell out the
  guarded form. The rule was stated three times and held in three places; these were written after.
- The same endpoint's two numbers were `int(...)` with nothing in front: `throttle_rpm: "many"` is a
  `ValueError`, `minutes: 10**30` is `OverflowError: Python int too large to convert to C int`. Both
  are bounded now — `throttle_rpm` by the ceiling Management applies to a rate limit, because a
  throttle *is* one — and a zero or negative `minutes`, which wrote a suspension that had expired
  before it was stored, is refused rather than accepted as a kill switch that stops nothing.
- `DELETE /api/v1/installation-budgets/<id>` with a non-numeric id raised
  `ValueError: Field 'id' expected a number` while *building* the query. Every `ModelViewSet` here
  is covered by DRF's `get_object_or_404`, which exists for exactly this; the installation budget is
  the one hand-written route that resolves an id itself, and `pk or 0` guarded the empty string
  alone.

**Two smaller ones, both a rule applied at each step and missing from one of them.** `storable`
exists to make a payload something a `json` column will take, and sanitised what a mapping *held*
while copying its **keys** through untouched — so `{"k\ud800ey": "v"}` came out of it still unable
to be encoded, and cost the whole row, which is the one outcome it is there to prevent. Both of its
tests hand it a well-keyed dict. And `to_nanos` promises a `ValueError` for a bad amount and does
not deliver one for `"Infinity"`, `"NaN"` or `"1e400"`: `Decimal` constructs all three happily and
`quantize` then raises `InvalidOperation`, an `ArithmeticError` a caller following the contract does
not catch. `1e309` is exactly how `Infinity` gets into this system, and `LESSONS.md` §1 already
records that value costing an audit row one door along.

**Open, and stated rather than half-fixed.** `thinking.permitted_by` returns *permitted* for
`disabled` whatever the candidate declares, so a fallback that cannot express *off* is sent
`thinkingBudget: 0` / `reasoning_effort: "none"` — which `thinking.py`'s own measurement records as
a 400 from Google for every model whose thinking cannot be switched off. It is reachable only
through a chain whose primary declares `disabled` and whose fallback does not, it degrades a request
rather than answering it wrongly, and the honest fix is per-hop **resolution** rather than a skip:
skipping would refuse a candidate that is perfectly able to serve the request, which is a narrowing
this round did not have the owner's leave to make. `FRD-111` §7 carries it.

Method note, because it decided what was found: **the sweep is what found five of the seven.** A
per-field test asks whether the field behaves; a sweep asks whether the *file* does, and it is the
only thing that could see that two endpoints written later did not inherit a rule stated three
times. It is kept as `gateway/tests/test_a_callers_value_is_never_a_server_error.py`, with a vacuity
guard reading the published OpenAPI document so a sweep whose requests all miss cannot pass.

12 mutations added (580), each observed `caught`; every fix was written as a failing test first and
each new test was watched go red with the fix reverted. Hermetic suite green at 95.7%.

---

## A review pass: one identity, one discriminator, one pinned version (2026-08-20)

> *"I have repeatedly hit simple but critical mistakes — hard-coded strings instead of variables,
> security holes. Functionality must not disappear; weaknesses have to be found and fixed."*

A read of the request path, both control planes and the console, looking for the shapes this
project already knows it produces. Nine findings, all of them **rules the code states and does not
hold** — which is why a green suite of 2646 hermetic tests could not see any of them.

**The one that was visible to a user.** *"Only my own requests"* was compared as
`row.subject == principal.subject` in three places: the payload authority (`payloads._authority`),
the trace list's restriction, and the `mine=true` filter. The two credentials answer *who is this*
in different alphabets — an API key's subject **is** its owner's username, an OIDC token's is the
directory's user id — and `scopes.person` exists because of exactly that; `_member_key` already
uses it for membership. The console is always OIDC and the traffic is usually a key, so the
comparison was a directory id against a username and **could never match**: a member of a use case
that restricts members to their own requests saw an empty trace list with their own rows in the
table, and `403 others_request` on their own prompt. The whole payload matrix missed it because
every principal in it has `subject == username`, which is what no real token looks like — the trap
`CLAUDE.md` names as *a test whose setup never reaches the path it is named after*. One owner now,
`payloads.own_requests`/`is_own_request`, in both a predicate and a query form, and the widening is
strictly additive: the raw subject still matches, so rows written before `FRD-606` stay visible.
Anomaly findings got the same treatment — `target_value` is grouped from `RequestLog.subject`, so a
reader signed in with one credential saw half of their own findings.

**A rule stated one layer up and not held below.** `record_request`'s docstring explains at length
that `api` deliberately has no default, because `"gemini"` *"made a caller that forgot it right on
one surface and silently wrong on every other"*. `PendingLog.api` and `RequestLogService.record`
both still carried the default, and the body-size middleware builds a `PendingLog` directly — one
forgetful edit from the same defect. Required in all three layers now.

**Two definitions of one pinned value.** `DEFAULT_API_VERSION = "2024-10-21"` in the Foundry adapter
was read by nothing while `GatewaySettings.foundry_api_version` carried the same literal, so bumping
the constant would have changed the comment and not the wire — and `test_foundry.py` spelled it out
a third time. The adapter now falls back to it (empty means unset: Compose passes `${VAR:-}`, and
Azure refuses `api-version=` with nothing after it), the test reads it, and a new test holds the
settings default to it — the treatment `DEFAULT_GEMINI_BASE_URL` already had.

**A bound that was declared and never applied.** `pipeline/config.py` declares `MAX_MODEL_LENGTH`
"the same ceiling Management's serializer applies", beside a comment naming three ways into this
parser that bypass Management. `from_dict` bounded how *many* fallback models a chain may name and
not how long each name may be — and an unresolvable candidate is written onto the audit row as
`{"to": <name>}` in a `json` column and named back to the caller in the `NoCapableModel` message.

**A field silently dropped on two dialects out of three.** `FRD-135` makes the model's reasoning a
use case's decision and its acceptance criteria say *"with it on, thoughts reach the caller"*. Only
the Gemini mapper read `include_reasoning`; a use case with it on that routed to a Claude model or
any OpenAI-dialect server was answered `200` with no thoughts. Both halves were invisible because
the **counting** worked on all three, so the reporting screen showed thinking being paid for that no
answer ever carried. Both mappers now return it — and, more carefully, withhold it by default:
these providers send reasoning whether or not it was asked for, so the *off* direction is the one
that needed a test.

**Three columns nothing bounded, one of them the identity.** `auth/oidc.py` cuts
`preferred_username` to 150 and the client id to 64, on a comment saying the claim is "bounded like
every other claim that reaches a stored field". `sub` — the column every audit row is keyed on —
was not among them, and `_fits` exists precisely because SQLite enforces no width while Postgres
fails the INSERT *after* the request has been served.

**Five definitions nothing reached**, each of them a rule the module appeared to have:
`ratelimit._capacity` ("the tests and the refusal message both ask" — neither did),
`state.model_catalog_of` (worse than merely unused: it handed back the app-wide catalog where every
reader wants the per-request one), `audit.is_pipeline_operation`, `reporting._EMPTY`,
`attempts.WINDOW_SECONDS`, and `pipeline/config.TEXT_KEYS`/`CATEGORY_TEXT_KEYS` — the last pair
describing a per-field allow-list while `_bounded` clips **every** string, so a reader adding a
field would have added it to a list that decides nothing.

**Two false claims in comments**, both fixed where they were written: Management's installation-budget
delete said *"the gateway removes the row and its counters"* and the gateway removes only the row
(consumption is keyed by `(scope, period)`, not by a budget id — so recreating a budget inside the
same period does **not** hand it a fresh allowance, which is the sentence a reader actually needs);
and `roles.ts` opens by naming the incident where two planes answered one question differently, then
restates all three role sets with nothing comparing them. A new `tools/tests` guard compares them
now, in both directions.

**A tolerated event that says nothing.** `apply_event` learned on 2026-08-18 that *tolerance does
not require silence* — an unapplied config event is a control an operator believes is in force.
`evaluate_rule` has the same two branches for a rule kind this build does not implement, and both
returned `[]` without a word: the console shows the rule enabled, the rule measures nothing, and
nothing anywhere connects the two. One log line each.

One guard had to be rewritten rather than satisfied. `test_app_state_is_typed` asserted
`len(reads) >= 8` on a sentence claiming `state.py` reads "most" services; it read 8 of 16 and went
red for the removal of one dead accessor. A literal floor makes deleting dead code look like
breaking a guard, and the pressure that creates is to keep the dead code — so it now asserts what it
meant: every `*_of` accessor in that module is one the parser can see. Proven by breaking it.

**And the harness found two of its own.** Run in full — 568 properties, the first complete pass in
this round — it reported two survivors, both in code this pass had not touched, and they are
different failures wearing the same word:

- **`M29`** is a real gap. Its anchor is in `_purge_usecase`, whose docstring promises *"`request_logs`
  still stay … and outlive its record too"*, and nothing checked it: a mutation making the purge
  sweep the audit rows passed all 29 tests in the file it names. The description said *"deleting a
  use case keeps its request log"*, so a reader matching it to a test found the one about
  **retirement**, which never reaches that code. `FRD-607`'s whole design is that the party who
  might want the record gone is not the party who can remove it — a purge that took the evidence
  with it would make the second step a longer path to the same erasure. Test added; description
  corrected to say *purging*.
- **`Y6`** is an inert mutation, which is the worse kind of report. It edits
  `mode is not ThinkingMode.DISABLED`, written when `Thinking.mode` was a `ThinkingMode`;
  `ADR-0021` made it a plain `str` — a level is the vendor's own word — and from that day the
  comparison was **always true**, so the mutation changed nothing and reported `SURVIVED` about a
  property that is fully defended. Verified by hand in both directions before touching it. `!=`
  now. A mutation nobody can distinguish from a missing test sends the next reader to write one
  that already exists — the harness's own version of a badge-wearing absent control.

The same identity-comparison mistake was then searched for in the source: every `is <StrEnum>.member`
in the three packages compares a value that really is an enum member (a typed pydantic field or an
explicit conversion), so there is none of it outside the harness.

Fourteen mutations added, three re-anchored, one corrected. The full pass ran at 568 properties
and reported exactly the two survivors above; both were then fixed and re-verified on their own,
and nothing else in the set is touched by either fix. Every fix was written as a failing test
first: five of the six own-request tests were red before the
change, and the Foundry, reasoning and console-roles guards were each broken on purpose and watched
go red.

---

## Writing down what four commits had only told the DEVLOG (2026-08-20)

> *"Document everything that was not documented."*

A pass over the last five commits against the documents each should have touched. Two of them had a
DEVLOG entry and **nothing else** — which is the failure mode `CLAUDE.md` §4 exists to prevent, and
it had happened twice in two days.

- **`FRD-611`** — *a region the policy forbids is refused where it is typed*. Built on 2026-08-20
  and until now recorded only as a commit message: the removal of `global` from this deployment's
  `AIRA_ALLOWED_REGIONS`, the console control that refuses an impermissible region as it is typed,
  and the design that keeps **one owner** for the policy (Management holds no copy; the gateway
  publishes the list on the answer the editor already fetches). The edge case is written down where
  a reader will meet it: an older gateway sends no list, and **absent is not empty** — a console
  that read it as an empty allow-list would refuse every region during a rolling update.
- **`FRD-612`** — *a declaration the console accepts and the dialect cannot say*: the `500` that a
  ticked `auto` produced, the refusal both surfaces now name, the adapter declaration that nothing
  had ever read, the demo's move to the predecessor's own id `1004`, and the showcase printing a
  command for a model it does not own.

Both ADRs those features rest on **claimed something that was not true**, which is the more useful
half of this pass:

- `ADR-0021` §5 cited `Upstream.thinking_modes` as an existing fact. It existed and was read by
  nothing — so the ADR described a control the code did not have. Amended, with the consequence
  spelled out under *Negative*.
- `ADR-0012` §6 said residency is *"one allowed-region list across every transport"*. True, and it
  said nothing about **when** anybody finds out. Amended: one owner, now two moments — the console
  informs, the gateway enforces, and a console that was told nothing says nothing.

Six reader-facing documents were stale in ways that would have cost somebody time:
`REQUEST-LIFECYCLE.md` §8 had no row for the new refusal; `TESTING.md` carried coverage measured on
the 19th and no mutation count at all; `GAP-ANALYSIS.md` row 15 described budgets as if unattributed
spend still passed the gate untouched, and its open-gaps section named neither of `FRD-610`'s two;
`INTEGRATIONS.md` told an integrator that a model outside the list *"refuses to start"* without
mentioning that the console now refuses one earlier, or that `global` is deliberately absent;
`ARCHITECTURE.md` listed the console's screens without the installation budget; and
**`CONFIGURATION.md` documented `AIRA_ENFORCE_BUDGETS` with an empty cell** — the one switch
`FRD-610` §3.3 is about, described nowhere, while the document that lists it is the one an operator
reads before turning it off.

`CLAUDE.md` gains the two open items that came out of this week — `FRD-610` §3.2–3.3, and the fact
that a model reachable only at `global` is not usable under an EU requirement. Both are policy
outcomes stated on purpose, not defects waiting to be fixed.

Nothing here changes behaviour; the suites are re-run because a documentation pass that breaks a
guard is a documentation pass that lied. Re-running them found one thing: `ruff check` had not been
run over yesterday's new test file, and a 101-character line went in with it. `make ci` runs
`ruff check` *and* `ruff format --check`, and only the second of those was run before that commit —
two commands, one of which is easy to believe covers the other.

---

## A declaration the console accepts and the dialect cannot say (2026-08-20)

> *"You broke my showcase — if you now change something on `qwen3:0.6b`, it cannot cope with
> thinking methods. And set the KIRA model id to 1004."*

`make showcase` itself ran green, so the report was reproduced the way it was described: open the
model in the console, tick a box, save. Ticking **`auto`** for `qwen3:0.6b` — a model served by an
OpenAI-dialect endpoint — takes ten seconds, is offered without a word of warning, and turns every
thinking request into:

    {"error": {"code": 500, "message": "Internal error while processing the request."}}

**`DialectUnsupported` was not in `REFUSALS`.** That list is the single place both surfaces catch,
written so *"a new control cannot be caught by one surface and escape the other"* — and this
exception escaped both. Its own docstring explains why nobody noticed: *"It should be unreachable
in practice — a model that cannot do a thing does not declare the capability."* The console is
where a capability is declared, so *unreachable in practice* rested on nobody making an ordinary
mistake in it.

Both surfaces now name the reason. Verified live, same request that produced the 500:

    400  FAILED_PRECONDITION  "This dialect has no way to say 'the model decides':
                               `reasoning_effort` is always a level…"
    400  VALIDATION_ERROR     the same sentence, in the predecessor's envelope

and the audit row says `invalid_request` rather than recording an outage that did not happen.

**The declaration nobody read.** Every adapter has always carried `thinking_modes` — the OpenAI
family excludes `limited` and `auto`, Anthropic excludes `auto` — and **no code on any path
consulted it**: four adapters declaring, one test asserting, nothing asking. So the console's *Ask
the model* button now asks about the ticked modes as well as the level words, and answers them from
the dialect for nothing: whether a wire format has a field for *"you decide"* is not a question
about the model or the region it runs in, so no request leaves the gateway. The ✗ arrives before
the save, with the sentence that matters — *"every request that asks for it is refused; the model
is never reached"*. It **informs, never blocks** (`FRD-506`), because the runtime refusal above is
the backstop and a console that refuses a save is a console that is sometimes wrong about a model
it cannot reach.

**The KIRA id is now `1004`.** `ADR-0010`, `docs/MIGRATION-KIRA.md` and the hermetic suite have all
used the predecessor's own chat id as their example since the surface was built, and the demo
answered to `9001` — so the one runnable command in a migration guide used a different number from
every sentence around it. `FRD-107`'s promise is that a client migrates by changing a base URL; the
demo is the first place that should look like an installation that kept its clients' ids. Six
integration tests carried the literal; they now import it from the seed that writes it.

**And the showcase was printing a command for a model it does not own.** `showcase_try_it.py`
takes the first chat model the KIRA catalog lists — which, on a machine with cloud credentials, is
a cloud model. It printed `model_id: 9504` for `gemini-2.5-flash`, a model the demo never seeds,
never releases and never sends a request to, and running it returned `200` with an **empty** body:
24 output tokens, all spent thinking. The file exists for one sentence in its own docstring — *"what
was missing was one command that works"* — and *works* has to mean *answers with something*.

One mutation survived and was **deleted rather than defended**: an early return for a modes-only
check that both following branches already produced. A rule written twice is one that can be
corrected in one place. And one console property was broken by hand and *did not go red* — the test
never set a verdict before failing the next question, so there was nothing stale to leave behind.
It sets one now.

7 new mutations (557) · showcase green end to end · hermetic, console and browser suites green.

---

## The installation's own budget: the bucket for spend that belongs to nobody (2026-08-20)

> *"Build the installation budget."*

Yesterday's concept named three holes of one shape — *spend no allowance can see*. This closes two
of them, which turn out to be the same one: **there was no bucket for spend that has no owner.**

A budget was anchored to a use case, by two scopes and by the query that fetches them. So the
gateway's guard opened with an early return for a request naming none — written when there was
genuinely nothing such a request could be counted against, and left in place after there was.
Removing it is most of the feature; the rest is making sure the thing it now finds is the right
thing.

- **`Scope.applying` gains one branch**, in the one place a scope is added, so the budget path and
  the rate limiter follow together. An `installation` row binds a request that names **no** use
  case, and only those — a row naming one as well would be two owners for one spend, which is why
  the database refuses that combination where it is authored.
- **The counter gets its own prefix**, `installation:`, deliberately not `uc:` with an empty name.
  A usage key is *stored*, so an empty one would be indistinguishable from a use case whose slug
  somehow emptied — and `_delete_usecase` sweeps counters by the `uc:{slug}` prefix, which with an
  empty slug is **every counter there is**.
- **The refusal names the allowance that ran out.** It said *member*. Caught by a test written
  while building, before anybody saw it: naming the wrong bucket in a 429 sends an administrator to
  edit a limit that was never the one binding.
- **The console** gets it on the reporting screen, above the report, because the figure it bounds
  is already there — the `(none)` row of *By use case* — and a control must be findable in a period
  that returned nothing at all. A Global Administrator sets it; every oversight role reads it
  (`ADR-0007`: `IT Steuerung` oversees and acts in nothing).

**What did not change, and the reason is not symmetry.** The pipeline's own calls still require a
use case. A pipeline **is** a use case's configuration — there is no installation-level pipeline
call — and widening those two methods "for consistency" broke four test doubles, which was the
first hint that the change was about a word rather than about a rule.

**Two constraints, in the database and not only in a form.** `use_case` had to become nullable, and
**a NULL is not equal to itself in SQL**: the existing `uq_budget` therefore stops policing exactly
the rows this feature introduces, so two installation budgets for one period would both be accepted
and the gateway would enforce whichever it read first. A partial unique constraint covers what the
first cannot see; a check constraint refuses a row whose scope and owner disagree, because
`clean()` runs for a form and not for a fixture, a shell or a migration.

**Two mutations survived, and the reason is the finding.** `IB5` and `IB6` were anchored in
`models.py`, where the constraints are declared — and nothing noticed them being broken, because
**the test database is built from migrations**. A `Meta.constraints` entry with no migration is
enforced by nothing at all, and a mutation anchored there tests nothing at all. Re-anchored onto
the migration, both are caught. Now `LESSONS.md` §7.

**Three guards this repository already had fired on my own work**, which is the point of having
them: the console's *every creator opens a window* test rejected the inline form I had written
(five other screens open one; the reader learns the pattern four times and meets an exception on
the fifth), the property count in `CLAUDE.md` was stale by eight, and a mutation anchor went stale
the moment the budget query changed shape.

**And one defect of my own, found by a test I wrote for coverage**: `remove()` had no `canManage()`
check while `setEnabled()` did. The server refuses either way, so it was never a hole — it was the
difference between a no-op and a red banner for a reader who was never offered the control.

**Running the live layer found three reds that had nothing to do with this feature**, which is
the argument for running it rather than the four hermetic suites alone:

- **The gateway's models and its migrations disagreed.** `0040_use_case_tombstone` creates
  `ix_use_cases_deleted_at`; the model declared the column without `index=True`, so
  `alembic --autogenerate` wanted to *drop* the index on every run. One word, and the integration
  guard that compares the two is the only thing that could have said so.
- **`minimal` was still on the list of thinking modes that must be served.** It was put there on
  2026-08-18 because a dialect mapping translated it to `"low"`; `ADR-0021` deleted that mapping
  two days later, and the parameter went red against the real server — *"which is exactly how the
  entry was removed the first time"*, as its own docstring predicted. Moved back to the modes that
  are refused by name.
- **A test that read the developer's machine.** *"A model nobody serves says so"* asked about
  `gemini-2.5-pro` on the assumption that this stack had no Vertex key. It has one now, and a
  catalogued model becomes servable through its *provider* without being listed in
  `AIRA_VERTEX_MODELS` at all — so the test asserted the opposite of what happens. No real model
  name is safe to assume unserved; it now asks about a name no adapter can claim, which reaches the
  same branch and cannot be falsified by a credential somebody adds.

`FRD-610` §3.1 built · 8 new mutations (551) · hermetic, live-stack and browser suites green
(148 e2e passed, 1 skipped).

---

## The checks that spent money invisibly, and where else it leaks (2026-08-20)

> *"How does it look with the budget? Are these test requests counted and taken into the budget —
> every request should be auditable and budgetable."*

**Measured before answering: 1491 audit rows before pressing the button, 1491 after.** The console's
*Ask the model* check spent real tokens and left no row, no budget entry, no trace.

That exemption was mine, written the day before, and its argument was that the spend is tiny,
bounded and role-gated. All three true, and an answer to a different question. **A small amount
nobody can see is not a small amount — it is an invisible one.**

**Every probe now writes a row**: one per region for the free reachability check (the zero is the
point — *"who probed this, and when"* now has an answer), one per word per region for the paid one,
with the real usage and the real price. Verified live: `prompt 11 · completion 1 · cost_nanos 1500`.
No use case, and **none invented** — the check exists for a model nobody has released, so there is
nothing to attribute it to, and inventing an owner so a row has somewhere to sit is the failure
`FRD-403` names. `Outcome.DIAGNOSTIC` rather than `served`, or these would inflate every request
figure with traffic no use case made.

**And the seed was writing a declaration that answers 400.** `qwen3:0.6b` was seeded with `minimal`
among its thinking modes, justified by a comment citing `FRD-111`'s translation of `minimal` →
`"low"` — which `ADR-0021` deleted the day before. Ollama refuses the value by name. Both seeds now
carry what was measured, including `max`, a word no vocabulary in this project ever had.

**Then: where does money leak?** The concept is `FRD-610` §3 and its finding is that three holes
are one shape — *spend no allowance can see*:

- **diagnostics** — now visible, still unbounded;
- **unbound traffic** (break-glass keys, demo) — 59 rows, owned by nobody;
- **an unpriced model under a cost budget** — and this is the uncomfortable one. A cost limit
  compares `usage.cost_nanos`; an unpriced model contributes **nothing** to that figure, because it
  is counted separately as `unpriced_requests`. Right for reporting (*unknown is not zero*), and
  for enforcement it means *unknown is unbounded*. A use case whose models are all unpriced can
  spend without limit against a budget that looks configured.

Proposed: an **installation allowance** as the residual bucket, so that *a request that fits no
bucket does not run*; **unpriced refused under a cost limit** rather than priced by a guess — the
thing `ADR-0021` just spent a day removing; and the cheapest first, a banner when
`AIRA_ENFORCE_BUDGETS` is off, because today budgets keep their figures and bars and simply stop
stopping anything.

**A mutation caught a test double, again.** `D22` — *the row carries the usage the answer reported*
— survived, because the stand-in returned `object()` where the real adapter returns a response with
usage: the property could not be lost because the harness never produced it. The same trap this
project has recorded twice before, in its third costume.

`FRD-610`, `Outcome.DIAGNOSTIC`.

---

## A model in several regions, and three things that already depended on there being one (2026-08-20)

> *"In the catalogue I would like to be able to enter several regions, and they should also be
> checkable — whether the model is reachable, and the same for the thinking methods. And check what
> else could depend on this."*

The last clause is the one that paid. Twelve places read a model's region; **three of them were
already wrong**, and the feature would have made each systematically wrong rather than
occasionally:

- **The audit row named the region from configuration, not the one that answered.**
  `provenance_for(provider)` returns the region of the first *configured* model on that adapter.
  For a catalogued model in a different region that was already a false residency claim; with a
  failover chain it would have been one on every request that used a fallback. `FRD-115`'s whole
  argument is the difference between *"the configuration says EU"* and *"this request went to
  `eu`"*, and the row had the first while reading as the second. **A wrong claim is worse than a
  blank one** — a blank column is neither a claim nor evidence.
- **A leaked stream on every refused connection.** `_StreamContext.__aenter__` raises after the
  inner httpx context is entered, and Python does not call `__aexit__` when `__aenter__` raises. One
  leak per refused stream since the transport was written, invisible until `429`s arrive often
  enough — and failover turns that path from the unlucky end of a request into something a healthy
  request walks through.
- **A guard claiming `addressing` was read by nothing**, true until the day before, and unfalsifiable
  by construction: the check subtracts the payload keys *and* the exemption list, so a field that is
  genuinely offered is subtracted twice and the stale claim reads as current.

**The feature itself** is an ordered list, and the design is entirely in *which failures move on*:
`404 408 429 5xx` are facts about a **place** and fall through; `400 401 403 422` are facts about
the **request**, identical everywhere, and retrying them triples the wait before the same refusal —
and, on a `403`, triples the failed-auth count somebody is alerting on. A `200` never falls through:
the model answered, and asking a second region is shopping for a verdict.

Two things worth stating because they were choices:

- **The failover loop keeps no copy of `AIRA_ALLOWED_REGIONS`.** It learns that a region is
  forbidden by addressing it and being told, so residency still has exactly one owner and a model
  catalogued in `europe-west1, global` simply works on an installation that permits only the first.
- **A stream that has sent a byte is committed.** The chain is walked while opening; after the first
  chunk every failure propagates. A client with half an answer cannot have it continued by another
  region.

**Checks are per region, both of them** — the owner's choice over a cheaper approximation, and the
right one: a vendor rolls a family out region by region, so `thinkingLevel` can work in one region
and answer *"not supported by this model"* in another. A declaration checked in one place would be
a claim about the others. The summary is the **best** region with the failures named beside it,
because a model that answers in one of its three *is* reachable.

**Sixteen mutations.** `V26` — the audit row preferring the region that answered — survived its
first form, which is how the residency-evidence test came to exist: the code was right, and nothing
would have noticed it becoming wrong.

`FRD-609`, migration `catalog/0006`.

---

## A region the policy forbids is refused where it is typed (2026-08-20)

> *"global out of allowed regions, and refuse the ones that are not permitted in the console."*

Both halves, and the second is the one with an argument behind it.

**`global` is out.** It was in this deployment's `AIRA_ALLOWED_REGIONS` and not in the shipped
default, added for Google AI Studio — whose key is empty, so it bought nothing. It **names no
region and guarantees none**, which is the whole difference from Vertex, and the audit trail showed
two requests already processed there. The file now matches `.env.example` exactly.

The consequence is stated rather than worked around: `gemini-3.5-flash` is reachable with this
credential **only** at `global` (measured across five endpoints on 2026-08-19), so under an EU
residency requirement it is not usable here yet. That is the honest answer, and it retires the
advice I gave that morning.

**The console refuses an impermissible region as it is typed.** The gateway has always enforced
residency — at the moment it *addresses* a request, which is correct and **late**: a model is
catalogued in Management, and whoever did it hears nothing until a caller gets a 4xx, possibly
weeks later.

The interesting part is where the list comes from. Management must not hold a copy: residency is
one policy with one owner (`ADR-0012` §6), and a second copy is how two planes come to disagree
about what an installation may do. So the gateway **publishes** it, on the answer the console
already fetches when the model editor opens — the two facts are used in the same breath, *which
provider* and *where*, and a second request would be a second thing to fail.

**The edge case worth the most thought**: an older gateway sends no `allowedRegions`. A console
reading that absence as an *empty allow-list* would refuse every region anybody typed — enforcing
a policy it has never heard. Absence means *this gateway did not say*; the console then declines to
have an opinion and the gateway refuses at request time exactly as before. Informing where it can,
never blocking on its own ignorance.

Seven properties broken by hand and seen to fail, including that one twice — once for the empty
region and once for the unstated list, because a single condition guards both and only two
mutations can tell them apart.

`ADR-0012` §6, `FRD-115`.

---

## Deleting a use case stops it and destroys nothing (2026-08-19)

Asked as a regulatory requirement and stated as a threat:

> *"Since we work with regulation it would be good if we did not do a full delete but a soft
> delete, and only at some point a full delete after a deliberate decision. Prompts should then
> still be deleted after the defined time — but not in a way that lets somebody use a use case for
> the wrong purposes, compromise it, and delete the use case."*

The system satisfied that exactly backwards. `DELETE /use-cases/<slug>/` was open to a **use-case
administrator** — the party an investigation would be about — and it was a hard delete. The traffic
survived, on purpose (`FRD-404` §4.1), and survived **context-free**: an audit row names a use case
by slug, and what that slug *meant* — purpose, processing notes, released models, whether prompts
were stored and for how long, who its members were — lived in Management and went with the row.

**Retire, never remove.** `deleted_at` + `deleted_by`, the same `usecase.deleted` event so access
ends exactly as before, and a **second** act — `purge`, Global Administrator only, only for an
already-retired use case, only after 30 days — that emits `usecase.purged`.

**The tombstone is load-bearing, not bookkeeping**, and this is the part I did not expect:

- **It closes a hole retirement would otherwise have opened.** API keys stop working and group
  grants go with the read-model rows — but a Keycloak group `/use-cases/<slug>` resolves **from the
  token alone** and touches no AIRA table. Every OIDC member of a retired use case could have gone
  on calling it with all of its controls deleted underneath them: no budget, no rate limit, no
  pipeline. And the refusal is *only possible* because the row survives: the gateway deliberately
  has no existence check, because Kafka orders nothing and a use case that has not arrived yet
  looks exactly like one that was deleted. A tombstone is not absence.
- **It keeps the retention promise.** `retention.py` reads a use case's own `retention_days` from
  that row. While the row was deleted, retired use cases fell to the *installation default* — wrong
  in both directions: 90 days promised and destroyed at 7, or 3 promised and kept for 30. Both are
  now pinned.

**Tests**, since the ask was explicitly for scenarios and edge cases: twelve in Management, five in
the gateway's serving path, four in retention, two in the consumer, and four existing tests
rewritten where the property genuinely changed. The refusal-before-the-rate-limiter one is the one
I would keep if I could keep one — a limit of one request, called twice: if the check ran second,
the second call would be a 429 and the allowance of a use case nobody may call would be spent.

**And a mutation that survived taught something.** `SD3` — *a use case must already be retired
before it can be purged* — could not be broken, because the rule existed **twice**: a queryset
filter and an `if`. That is not defence in depth, it is two copies where nothing says which is
load-bearing, and a later reader deleting "the duplicate" has even odds. One copy deleted, and the
mutation caught immediately.

`FRD-607`, migrations `usecases/0013` and gateway `0040`.

---

## Reading the documentation against the code (2026-08-19)

Asked directly: *"can you read the documentation and check it, so we do not have unverified things
lying around?"* Done mechanically where that is possible and by reading where it is not. Six claims
were wrong; the mechanical sweep found two of them and reading found four.

**What machines could check.** Every relative link in every document resolves. Every `make X` the
docs name exists, except in `DEVLOG.md`, which quotes dead targets *because* they were dead — a
guard for that already exists and already excludes it. `CONFIGURATION.md` is checked against the
settings classes by its own test.

**What was actually wrong:**

- **`TESTING.md` claimed a "100% coverage gate".** The gates are floors: Python 90%, frontend
  90/92/93/75 for statements/branches/lines/functions, and the measured figures sit just above
  them (branches has 0.05 points of headroom). *Enforced 100%* and *a floor that fails on a drop*
  are different promises, and the second is the one CI makes. Replaced with the table.
- **`FRD-111` §9 named two span attributes that do not exist** (`aira.thinking.mode`,
  `aira.thinking.budget`) and *the resolved budget* on the audit row. Neither was built. What is
  there is better and the section now says so: `reasoning_tokens`, what the model actually **spent**
  (`FRD-135`). A budget would have recorded a month's *permission* — it is a ceiling, not a spend —
  and after `ADR-0021` a level carries no budget at all.
- **`FRD-101` §9 named `auth.method` / `principal.subject`.** Built as `aira.auth_method` /
  `aira.subject` when `FRD-102` made attribution first-class. A document naming an attribute nothing
  sets sends somebody to query for it.
- **Two FRDs promised custom metrics** — a counter for auth failures by reason (`FRD-101`), a token
  count metric (`FRD-100`). **This project defines no custom OTel instruments at all**: a meter
  provider and auto-instrumentation, and everything per-feature is a span attribute or an audit
  row. Both said so now, with what does exist named.
- **`GAP-ANALYSIS.md` row 24 said `FRD-118` was *missing · need unclear · not scheduled***, and it
  had shipped the day before. The document is a snapshot written 2026-08-07 and nothing re-reads
  it, which is how a gap analysis becomes the opposite of what it is for. It now carries the date
  it was last read against the code.
- **`FRD-114`, `FRD-119` and `FRD-111` §5.2 still described the `level → budget` table** that
  `ADR-0021` deleted the same afternoon. §5.2 is rewritten rather than deleted — the argument it
  replaced (*"`high` means nothing to an HTTP call"*) was true of the vendors in 2026-08 and stopped
  being true when they converged on words.

**What I could not verify and am not claiming:** `ADR-0010`'s *"fourteen MIME types"* describes the
**predecessor's** contract, not ours, and there is no copy of that contract here to check it
against. Ours is 15 in the outer bound and **14 in a model's declaration** — `application/x-javascript`
is refused by Vertex and stays in the bound because another provider may take it. Verified against
the running catalogue: `gemini-2.5-flash` declares 14 and not that one.

The shape worth keeping: **the claims that rotted were the ones no test could reach.** Link
targets, make targets and setting names all have guards and all were clean; span attribute names,
metric promises and a snapshot's date have none, and every one of those was wrong.

---

## Thinking levels stop being numbers nobody can know (2026-08-19)

The owner catalogued a real model and objected to the form:

> *"If I now pick medium or low, you ask me how many tokens that should be. You do not even find
> these parameters on the vendors' own pages. How am I, cataloguing the model, supposed to know it
> when the vendor never stated it? … Your story with the percentages is nonsense. And on the
> thinking limit, I do not find that on the vendor pages either — for agentic coding it would be
> fatal."*

Every clause holds. The console's own field label, read out by a screen reader, was *"How many
thinking tokens `medium` means"* — a question with no source. My intermediate proposal, deriving it
as a fraction of the model's range, was worse rather than better: a fraction of a range is an
invented number with a formula in front of it. And the number was not inert — it went upstream as
`thinkingBudget`, a **ceiling on the model's reasoning**, so a typed `medium = 2000` truncates an
agentic run that needed twenty thousand, with a `200` on it.

A second correction followed a draft that mapped every level to *"let the model decide"*:
*"for a chatbot it would be different, and you are focusing very hard on gemini 2.5, which is a
very old model and is being retired in the EU soon."* Right on both counts — the whole point of
`low` is that it is not `high`, and designing around the dying model was designing backwards.

**So: the vendor's own words, typed freely, checked against the model.** `ThinkingMode` keeps the
three settings the gateway owns (`disabled`, `auto`, `limited`) and loses the four levels;
`Thinking.mode` is a `str`; the `level → tokens` table is gone from the catalogue, the console and
the resolution path. A model whose dialect takes only numbers simply does not offer the words.

**The split that made it possible.** `Thinking.tokens` was doing two jobs — what goes on the wire
*and* what the budget reserves against — which is exactly why a level had to invent a number: so
the reservation had something to read. It is what goes on the wire now, and `reserved_tokens` asks
the declaration for the model's ceiling instead.

**The check.** Free text needs an authority and no rule here can be one. Measured before building:
`:countTokens` is free and useless (it never reads `generationConfig` and answers 200 to an
unsupported level), and `generateContent` capped at one output token judges — a refused word costs
nothing, an accepted one costs a token. So the console has *"Ask the model"*, and it found
something on its first press: the migration carried `gemini-2.5-flash` across as
`levels: [low, medium, high]`, and all three came back red in Google's own words.

**And two things the owner found by using it**, neither about thinking:

- *"I cannot call any 3.5 models to test them."* Probed across five endpoints: this credential
  reaches `gemini-3.5-flash` and `gemini-3-flash-preview` **only at `global`** — and `host_for()`
  built `global-aiplatform.googleapis.com`, which **resolves** and answers 404. A host that fails
  DNS is obvious; one that resolves and 404s reads as *"the model is not available there"*. Fixed,
  and with it the measurement that had been missing all along: on `gemini-3.5-flash`, `minimal`
  spends **0** thinking tokens, `low`/`medium`/`high` all answer, and a typo (`hight`) is refused
  by the vendor with the value named.
- *"gemini 3.5 flash does not work in the interface, I get the error message."* The provenance had
  been corrected in the form — `vertex`, `google`, `global` — and *Check reachability* still
  answered *"Declared, but nothing serves it"*, because the check read the **saved** catalogue row.
  A correct answer about the declaration being replaced. The same shape as a verdict left standing
  from a previous model, one step along: right, wearing the wrong label. Both checks now take the
  provenance from the form, and the same case answers **Reachable**.
- *"When an error is thrown in the interface, the button layout at the bottom breaks."* The
  footer's message is `.grow` — `flex: 1`, so `flex-basis: 0` — which **contributes nothing to the
  wrap calculation** and is squeezed instead of taking a line. The same defect
  `.form-inline > .callout` carries a comment about, one container along, and latent until the
  editor's window was narrowed to a form's width the same afternoon. A rule that only holds at one
  width is not a rule.

`ADR-0021` rewritten (it now supersedes its own first version), migration `0005`, seven frontend
properties and five gateway ones broken by hand and seen to fail.

---

## Adding a model asks two questions instead of eight (2026-08-19)

The owner declared a real Vertex model through the console for the first time, and reported the
screen rather than a bug:

> *"I find the options for adding a model too complex by now, it is very confusing, too many
> options where you do not know exactly what you are doing. If somebody other than me is to add a
> model, that person will not understand the screen. A further point is the Add model button, where
> you have to type everything in yourself — that is by now unnecessary, since we have adding from a
> provider."*

Measured before touching anything, because *"too complex"* is an impression until it is a number:
the editor renders **61 controls** with every capability ticked, and its identity tab asks **eight
questions**. Five of them — provider, publisher, platform, hosting, the KIRA id — are answered by
choosing the provider one screen earlier. The empty form asked them anyway.

So the two complaints are one: **a screen that offers "type it all yourself" beside "let the
software fill it in" is not offering a choice, it is offering a way to get it wrong.** There is one
entrance now (`+ Add a model`, which opens the provider window), and where something already knows
the provenance the editor states it in a sentence with a **Change** button instead of asking. The
identity tab asks two questions on the common path: the model id and the region. The empty form
survives where it is the honest option rather than the tempting one — a gateway with no upstream
configured, and (see below) a provider whose listing does not carry the model you want.

**A gap the change created, closed in the same pass.** Removing the page-level button left a
provider that *can* be listed with no way to name a model the listing does not carry — a deployment
of your own on an OpenAI-compatible server, a model too new for the vendor's index. The old button
covered that by accident, from outside the flow; *"Not listed? Name it yourself"* now covers it on
purpose, from inside it, with the provider's facts still filled in. Recorded because it is the
shape of most simplifications that go wrong: the removed thing was reachable for a reason nobody
had written down.

**Two defects, both found by the tests for the change rather than by using it.**

- *The block collapsed under the reader.* Shown-when-known, applied literally, means the empty form
  opens the fields, the reader picks a provider from the select, and *"a provider is now known"*
  takes the select out of the DOM under the pointer that had just used it. A rule about what a form
  knows, firing while somebody is telling it. It latches open now.
- *One model's verdict on another model's form.* `add()` clears the reachability verdict and
  carries a comment saying why; the manual route opened the same window beside it and did not, so
  checking a row and then adding a model showed a green badge about a different model. Two
  entrances to one window and only one swept the floor — which is also the argument for there being
  one. The manual route now goes *through* `add()`.

**Tests.** Seven new frontend properties, each broken by hand and seen to go red: one entrance and
what it opens; provenance summarised when known and asked when not; the latch; **Change** revealing
the fields; the verdict cleared; a way in with no providers configured. `make mutants` is pytest
only and does not reach them, so the break-and-restore was scripted and its output kept. Nine e2e
sites that clicked *"Add model"* now go through one `openModelEditor()` helper — a sequence written
out in nine specs is nine copies of a decision.

**And then the owner looked at it**, which is the part no assertion replaced. Two things a green
suite had nothing to say about:

- *"the fields do not stand under one another, they are stretched."* `.form-inline` is a wrapping
  flex row — right for eighteen fields, wrong for two: the model id and the region shared a line
  and each took half the dialog.

  **I fixed a third of it and said it was done.** A class on each field reached the identity tab
  and left capabilities and price exactly as they were, and the report came back within the hour:
  *"the window is still stretched."* Both halves of that are fair — the other two tabs still had
  side-by-side fields, and an 880-pixel dialog holding a stack of fields is a form using the left
  half of a window. One rule scoped to the window now
  (`.modal--steady .modal__body > form.form-inline > .field`), the dialog narrowed to 620, and the
  nested fieldsets left in rows on purpose: fifteen media-type token fields in a column is a page
  of scrolling.

  A per-field rule is a decision that has to be remembered at every field added afterwards, and it
  had been forgotten at two thirds of the window before anybody looked at it. So the guard follows
  the window too: the alignment test — *do the controls on a line start at the same height* — had
  no line with two controls left to check and would have passed by finding nothing. It asserts the
  editor **stacks**, on all three tabs, and was broken on purpose and seen to fail naming the tab.
- The sentence rendered as `Lives on **mock** , speaking the aira dialect .` The markup was
  correct; `.field__summary` was a flex row with `gap: 0.5rem`, and a `<strong>` in the middle of a
  sentence is a flex item. **The unit test asserting the exact sentence passed** — a flex gap is
  not a character, so `textContent` cannot see it. A screenshot could, which is the only reason it
  was found.

A third came out of the same look: the blue import note opened with *"Filled in from mock:
provider, dialect"* — the sentence one line above, in the same words. Two statements of one fact
read as two facts. The note now says only what the sentence cannot: what the import left for you.

`FRD-507` §4.6 (stage D), `LESSONS.md` §6 (*a question is a claim that the answer is unknown*).

---

## A second model, a model that does not exist, and a suite that grew the catalogue (2026-08-19)

*"Test it against Gemini 3.5 as well."* It does not exist here: Vertex answers `404` for
`gemini-3.5-flash` and for `gemini-3-flash` in `europe-west1`, and the catalogue entry names AI
Studio as its platform while `AIRA_GOOGLE_API_KEY` is empty — so nothing serves it either way. A
caller asking for it gets a clean `404`/`422` on both surfaces, which is the right refusal.

Worth saying plainly: it is **catalogued and approved**, and the console has no passive marker for
*"approved, and served by nothing"*. The reachability check exists and is a button somebody has to
press (`FRD-506`, deliberately informing rather than blocking). A use case could release this model
and every request against it would fail. Recorded rather than changed — a badge in the list is a
design decision, not a defect fix.

The question behind the request was whether the media-type result is a property of the *model*, and
that is answerable with a model that does exist. `gemini-2.5-pro`, asked directly at Vertex so this
installation's configuration stayed untouched: **14/15, the same fourteen, the same refusal on
`application/x-javascript`**. So it is the platform's answer rather than one model's, which is what
makes the trimmed declaration worth keeping.

**And the catalogue had thirteen models nobody declared.** `cost-budgets.spec.ts` saves two models
per run and removed neither, so the browser suite grew the catalogue on every pass — and the
console's own warnings count over the *whole* catalogue ("N models have no price on file"), so the
residue turns a real figure into noise. `removeModel` in the e2e support module now cleans up;
verified flat over a full file run, 13 before and 13 after.

That helper needed two attempts and both failures were the same shape. The first searched and
clicked a control that lives inside the **expanded** row, so it matched nothing and returned
happily. The second waited for nothing: removing one model reloads the table, so the check for the
next one ran against a table that was not there yet. *A locator that matches nothing and a cleanup
that is not needed look identical* — the leftovers going 12 → 13 after the run meant to fix them is
the only reason either was noticed.

A third instance of that shape, in my own tooling on the same afternoon: a shell helper counting
models parsed the API response without checking the status, so an **expired token read as zero
models** and I briefly believed thirteen leftovers had vanished. A count that cannot tell "none"
from "could not ask" is not a count.

---

## Fifteen media types, one real model, and the first attachment this gateway ever sent (2026-08-19)

*"Have you tested all fifteen data types against the real Gemini API? Generate test files — with
content, so we are sure the model actually sees them."* Fair, and the answer was no: the list had
only ever been a list.

One real file per declared type, each carrying a nonsense marker that appears nowhere else —
`KOBALTFISCH` in a hand-built PDF, `PLATINMOOS` **drawn as pixels** in the PNG, `JADEHALM` in the
HEIC. A model that cannot read the attachment cannot produce the word, so an evasive answer is
distinguishable from a real one, and for the images a correct answer is proof the picture was
looked at rather than that a header was parsed. The PNG and the HEIC were opened and read by eye
before a single call was made, because a test whose fixture is illegible measures nothing.

**The first finding came before the first call.** `gemini-2.5-flash` declared
`["generate", "tools", "thinking"]` and no attachments, so the gateway would have refused every one
of these at the gate. No attachment had ever reached a real model through this product.

Declared through the Management API, then measured:

    application/pdf · text/javascript · text/plain · text/html · text/md      marker returned
    text/csv · text/xml · text/rtf                                            marker returned
    image/png · image/jpg · image/jpeg · image/webp · image/heic · image/heif marker returned
    application/x-javascript                                                  Vertex 400

Fourteen of fifteen, every marker exact. The declaration was then trimmed to the fourteen — the
rule this project already states about thinking modes, applied to media types: *a declaration is
evidence, not a claim*. The refused type stays in `DEFAULT_MEDIA_TYPES`, which is the outer bound
across every provider and not a statement about one.

**Both input surfaces, separately.** A run against one says nothing about the other: the Gemini
surface wraps binary in `inlineData`, and the KIRA surface puts `mime_type` and `data` **on the
part itself** — the shape whose stripping had been missed. Fifteen files through each. The results
are identical down to the token counts (1311 for the PDF, 2343 for every image), which is the
evidence that both map to the same canonical request rather than two paths that merely agree today.

And the audit closes the loop: twenty-eight rows, every one stripped, 359–394 bytes each — for
attachments up to 22 KB of base64. The refused type is now refused at the gate on both surfaces,
before an API call costs anything.

**The run found a second defect on its way past.** The gateway's answer for the failing type
was `Vertex upstream returned 400.` — nothing else. Vertex had said *"it has a mimeType parameter
with value application/x-javascript, which is not supported"*, and the transport discarded it: the
OpenAI dialect carries a provider's reason for a `400` and this one did not, its comment reasoning
that "a Vertex error can quote the request". True of the response **body** and not of
`error.message`. Two adapters, one question, two answers — `upstream_reason` is the one owner now,
capped, `400` only, never a credential error. Confirmed live afterwards on a different fault:
*"temperature value of 9 but the supported range is from 0 to 2.0001"*, where there had been
silence.

---

## The attachment stripper knew one surface's spelling (2026-08-19)

`strip_attachments` exists so that a base64 document never reaches
`request_logs.request_payload` — its docstring names the three costs: megabytes per row, binary the
gateway never inspected inside the retention boundary, and redaction handed something it cannot
process. It matched a **list of wrapper key names**, `("inlineData", "inline_data")`, under a
comment reading *"`FRD-107`'s KIRA shape adds its own when it lands"*.

It landed. The KIRA surface carries the bytes **on the part itself** — `{"mime_type": …,
"data": …}`, no wrapper — so no key matched and nothing was stripped. Measured against the running
stack: a 5 KB PDF stored whole, in a row for a request that was **refused** for lack of a capable
model, so not even limited to what was served. Every KIRA attachment since the surface shipped.

The comment predicted its own failure and nobody read it at the moment it came true, which is what
a note about the future is worth. The fix asks about the **shape** — a dict carrying a media type
and `data` together is inline binary, wherever it sits — so it covers both surfaces and the third
one nobody has written. A key list is a thing to remember; a shape is a thing to recognise.

After: 5452 bytes to 195, the bytes replaced by what they were, how many, and a digest.

Two smaller things came with it. The summariser decoded the base64 **twice**, once for the size and
once for the digest — two passes over a document on the write path for one answer. And it now
survives a datum that is not base64 at all: a *response* payload has not been validated on the
request path, and an upstream returning something unreadable must not cost the audit row — the same
door `storable` closes in the writer, met again from the other side.

---

## A regex that could not read regexes (2026-08-19)

`is_catastrophic` decides which operator-supplied patterns may be compiled onto the request path.
It was one regex — `\([^)]*[+*}][^)]*\)\s*[+*]` — and it had two holes, both found by timing
candidates rather than by reading it:

    (a+){20}$     accepted      51 s on a thirty-character input
    (a+){2,}$     accepted      76 s
    ((a)*)*b      accepted     159 s
    (\d+){15}$    accepted      35 s

The **outer** quantifier was matched as `[+*]` only, so every counted form walked past — and
`[^)]*` cannot see beyond the first `)`, so a group inside a group was invisible. A pattern
language cannot describe its own nesting; that is not a subtlety, it is why the detector is a
scanner now, tracking group spans with escapes and character classes honoured.

The consequence was real rather than theoretical. These are patterns an operator configures — a
pipeline filter, `AIRA_REDACT_PATTERNS` — and they run over caller content on the request path.
One of them stalls a worker for minutes on a short prompt, for every caller, until somebody thinks
to look at the configuration. The guard exists to stop an operator doing that by accident.

Sixteen known-bad shapes are now refused and twelve ordinary ones still accepted, including the
three most likely to be caught by a careless widening: a group repeated a fixed few times, a
character class holding a bracket, and **escaped** parentheses that are not a group. And every
pattern this project ships is asserted to still compile — a detector that refuses a built-in is not
a fix, it is a gateway that does not start.

Worth recording how the search went, because the first version of this finding was wrong. `(.*a){20}`
was also accepted and cost 30 ms, and I nearly reported it: measuring it against longer inputs
showed a **flat** 30 ms, so it is a fixed cost and not a backtracker. The four above grow past a
minute. A number is not a defect until it moves.

---

## Six characters bought an untraceable request (2026-08-19)

An adversarial sweep against the running gateway — malformed JSON, wrong types, absurd numbers, a
traversal in a model name — found three defects of one shape: **a caller's own value becoming a
server error**, which is the class this project has already fixed twice from the other side.

**A lone surrogate.** JSON may escape half a surrogate pair, so `"\ud800"` parses into a Python
string that no UTF-8 encoder accepts. Nothing noticed until httpx *built* the upstream request,
nine steps later: `500` on both surfaces, and no audit row, because the recording sites cover a
request that reached an upstream and this one died one step short. By then the rate limit was
spent, the budget reserved and the pipeline run.

**`maxTokens: 1e309`,** and this is the one that matters. It parses to `inf`, Python's `json`
writes it as `Infinity`, and RFC 8259 has no such literal — so Postgres refused the insert. The
request was correctly refused with a `422` and **the refusal was recorded nowhere**. A caller
could choose not to be logged, with six characters, on a product whose entire purpose is evidence.

**`model_id: 999999999999999999999999999`.** An `int` to Python, out of range for the `INTEGER`
column it is compared against, `NumericValueOutOfRange` from the driver, `500` to the caller.

Three fixes, in the three places the questions belong. `ensure_body_is_encodable` lives in the
shared layer and both surfaces call it at their own parse step — a surface parses, and *"can this
be written down"* is a parsing question, not a dispatch one; `test_surface_layering.py` fails on a
surface that skips it. `ModelId` is bounded to what the column holds, because a boundary that
models an unbounded int as a 32-bit one has only moved the failure to where it reads as ours. And
the log writer replaces an unrepresentable value with its name rather than losing the row — that
one is for the **upstream's** door, since a response payload is a model's output and a provider
answering `NaN` would otherwise erase the record of its own answer.

The sweep also confirmed what did not break: a NUL byte in a prompt, a float where an int belongs,
a path traversal in a model name, a schema nested past its bound, an array where an object belongs.

Two notes on the tests. `TC29` survived its first version: without the fix that body is *still* a
`422`, because Pydantic refuses `inf` for an `int` field — so the status told me nothing and the
test passed with the fix removed. What changes is the **message**, and therefore where the refusal
happens and whether a row can be written at all. And the earlier budget finding in this same round
had the mirror problem: a mutation replacing UTC with local time survived on a machine whose clock
is UTC, which is every CI runner's.

---

## Fourteen listeners, none of them on IPv6 (2026-08-19)

The console could not be reached from outside the machine. Every check from inside was green —
`docker compose ps` healthy, `curl localhost:4200` a 200, `index.html` and `runtime-config.js`
correct — and the browser answered `ERR_CONNECTION_RESET`. I twice concluded the fault was on the
far side of the port forwarder and said so, which was wrong both times.

The discriminator was nginx's own access log: **no request from the browser had ever arrived**,
only the fifteen-second healthchecks from `127.0.0.1`. Traffic that never reaches the server is not
a server problem, and that separated the question in one look. The forwarder carried
`::1 4200 -> 4200` alongside the IPv4 row; the browser resolved `localhost` to `::1`; and this
machine had fourteen IPv4 listeners and zero IPv6 ones.

Docker binds **one socket per published entry**, not one dual-stack socket, and `bindv6only=0` does
not change that because the userland proxy is what opens them. Measured rather than assumed:
`-p "[::]:15200:80"` alone answered on `[::1]` and failed on `127.0.0.1`; `-p "0.0.0.0:15200:80"`
alone did the reverse; both together answered on both. So every published port is now published
twice, and `AIRA_BIND_HOST6` mirrors `AIRA_BIND_HOST` with the same loopback default — `::1` for
the same reason `127.0.0.1` is the other one, because these files publish credentials.

Two things worth keeping past this incident. **A reset is not a refusal**: something accepted, so
the search starts outward, at the forwarder and the host, which is exactly where it did not belong.
And **every check I ran was over IPv4** — each one green, each one true, and none of them about the
family that was broken. That is the same shape as a test whose setup never reaches the path it is
named after, which this repository met twice in the last two days on entirely different code.

**And the model editor's standing decisions.** *"Approved for use / Deprecated is now present in
every tab."* They were — after the form, before the footer, flush against the last input with
nothing between them. A checkbox touching the field above it belongs to that field's section, to
every reader, whichever tab is open; the placement was defensible and the *reading* of it was not,
which is the only measure that counts for a screen.

A rule alone was not enough, and the second report said so: *"the two toggles now jump with the
size of the tab contents."* `modal--steady` fixes the **dialog's** height, which keeps the window
still and lets everything inside it slide — the body was one scrolling block, so the strip and the
band sat wherever the current tab's fields ended. Measured after the fact: 257 pixels of travel
between Identity and Price. The body is a column now, the tab panel takes the remaining space and
scrolls, and the strip and the band are pinned. My own comment in the stylesheet had argued against
pinning, on the grounds that it leaves a different gap on each tab — true, and beside the point:
the gap is empty space nobody sees, and the band moving is precisely what a reader does.

A rule and its own band is the rest of the fix, and it is worth saying what was rejected. Not a fourth
tab: somebody fills in three screens, presses Save, and creates a model nothing can call, with the
switch they never opened sitting at its default. Not the footer: that is where Save and Cancel
live, and a decision *about the model* is not an action on the dialog. `e2e` asserts the geometry —
below the strip, below the form, outside it, with a visible rule — rather than the CSS rule, so the
next arrangement that puts them back inside a tab fails too.

Also of note, and my own doing: `make migrate-gateway` from the working tree had put the database
at `0039` while the containerised migrator still ran an image that stopped at `0038`, so the next
`up` failed with *"Can't locate revision"*. Loud, immediate and fixed by rebuilding — recorded
because the same order of operations will happen again.

---

## Second sweep: the wire between the planes, and a schema nobody compared (2026-08-19)

Yesterday's sweep found copies of one fact. This one went looking in the places where a fact has
no copy at all — where two halves of the product are each correct and nothing checks the join.

**A configuration event the gateway cannot apply was dropped in silence.** `apply_event`'s chain
ended `else: return`. Tolerating an unknown type is right and stays — a rolling update runs two
gateway versions at once, and a consumer that crash-loops on a newer Management's event stops
*every* configuration change reaching it, not just the new one. Silence was the wrong half, and it
is the same line the KIRA surface reached the day before: tolerate, and say so. An unapplied event
is a governance control an operator believes is in force — a budget that never arrives, a released
model that never lands — and the only symptom was the console and the gateway disagreeing.

**And nothing compared the two vocabularies.** There are three statements of it: the `emit(…)`
calls, `_TOPIC_FOR` in the outbox subscriber, and the `elif event_type ==` chain in the consumer.
`test_outbox_routing` had compared the first two in both directions since August and had found a
real defect doing so; the third — the end of the wire, the only one that changes what runs — was
compared with neither. It is now, with `pipeline.deleted` recorded as the one deliberate asymmetry
(Management has no endpoint that deletes a pipeline; the gateway keeps the branch so an older
instance survives a newer Management that grows one).

**The gateway had no equivalent of `makemigrations --check`.** Management has had one since
August, when it found a pending `AlterField` that had been sitting there since `FRD-308` shipped.
Thirty-nine Alembic revisions and nothing compared them to the models — on the plane where it costs
more, because SQLAlchemy will not refuse to start over a missing column: it declares the table in
Python and issues a query, which arrives as a `ProgrammingError` on the request path.

It found something on its first run. `0008` created `ix_request_logs_use_case_created_at`; `0033`
created `ix_request_logs_use_case_page` on the same leading columns plus one, for the trace view's
keyset page, and did not drop the older one. Both had been maintained on every insert since, on
the table that takes **a row per request**. Measured before writing the migration rather than
assumed: 11 706 scans against 29, and the 29 are queries the survivor answers too. `0039` drops it.

**The migration graph had no guard either.** Two heads is what a merge of two branches produces —
each a valid chain in its own review — and `alembic upgrade head` refuses it *at the deployment*.
Now a hermetic test over the version files: one head, one root, every parent present.

**Six security-control properties had tests and no proof those tests could fail.** The bound on
failed authentications and the Kafka SASL/TLS wiring were both well covered and had no mutation, so
nobody had ever broken them to watch a test notice. All six caught.

**`docs/CONFIGURATION.md` §8 put three refusals in the wrong group.** It listed all seven under
*"outside `AIRA_ENVIRONMENT=local`"*, and residency, duplicate model names and an unreachable Vault
fire everywhere. That is not pedantry: setting `AIRA_GOOGLE_API_KEY` on a stock local configuration
stops the gateway starting, because AI Studio answers on the **global** endpoint and `global` is not
in the EU-only default — and a reader who had been told local was exempt watched the process die
with nothing pointing at the region. Pinned by a test, so a well-meaning `if is_local()` on that
path cannot make a local gateway route EU traffic differently from a production one.

Two mistakes of my own, both worth recording because both are this repository's own named shapes.
`str(sqlalchemy.URL)` renders the password as `***`, so the first version of the drift check failed
with *"password authentication failed"* — a library being careful, producing an error that points
at the database. And the first fixture set `sqlalchemy.url` on the Alembic config, which
`migrations/env.py` deliberately ignores in favour of `GatewaySettings()` — so the upgrade ran
against the **running stack's** database (a no-op, it was at head), the scratch database stayed
empty, and the check reported thirty-nine missing tables. It looked like a landslide of defects and
was its own wiring; there is now a test asserting the scratch database is the one that gets
migrated.

Eight new mutations (`TC14`–`TC23`, less two that could not be hermetic), 499 properties.

---

## Going looking for the shapes that turned up in one day (2026-08-18)

*"So many mistakes were found in one day, in one look — now go through the code and eliminate
similar ones."* Fair. Yesterday's port collision and the console's three copies of one address were
not isolated; they were instances. This is what a search for the same shapes found, and what it
cost to fix.

**The ports were half-done, and I had done the half that looks finished.** Compose's fourteen
published ports became `AIRA_PUBLISH_…_PORT` variables — and *nothing that talks to the stack*
knew: the Makefile carried twenty literal addresses, `tools/` five, `tests/integration/` a dozen,
`e2e/` eight, and `proxy.conf.json` two more in a format that cannot ask anything. Move a port to
dodge a collision and the stack comes up correctly while `make showcase` waits forever on the old
one and `make test-integration` reports "connection refused" — which reads as *nothing is running*
rather than *you are knocking on the wrong door*. `tools/stack_addresses.py` is now the one place
that answers, `tools/stack-addresses.cjs` asks it from Node, and a guard fails when a literal
reappears. The Makefile resolves all fourteen in one 42ms `python3` call rather than fourteen
`uv run` calls, because a developer who pays four seconds on `make help` starts working around the
Makefile.

**And the owner had the same bug it was written to fix.** Compose's fallback for the three
application services is nested — `${AIRA_PUBLISH_GATEWAY_PORT:-${AIRA_GATEWAY_PORT:-8001}}` — and
the inner name is what `docs/SETUP.md` had been documenting. Reading only the outer one published
the stack where the reader asked and left every tool at 8001. Found by asking `docker compose
config` instead of reasoning about it, which is now a test over five shapes of the resolution.

**`docs/CONFIGURATION.md` documented seven Vault variables that nothing reads.** `AIRA_VAULT_ADDRESS`
where the code reads `VAULT_ADDR`, with no prefix — they are HashiCorp's own names, read before any
settings class exists. An operator following the reference to turn Vault on would have set the
prefixed form, seen no error, and had every credential come quietly from the environment. That is
the exact failure `secrets_state()` was written for after it cost three days once; the document had
been sending readers back into it. `FRD-116` had a *third* spelling (`AIRA_VAULT_ADDR`). Nine more
settings were missing entirely, five of them the whole Kafka SASL/TLS family — the ones a
production deployment cannot do without, and `PLAINTEXT` is refused outside `local` precisely
because the read-model that authorization comes from is built from those topics.

**The console's URL prefixes were stated four times** — every call site, `AIRA_PREFIXES` in the
interceptor, the nginx `location` blocks, and the dev proxy. A fifth prefix added to the services
and forgotten in the interceptor sends the request without a token, and the `401` is then handled
by *logging the user out*: a valid session ended over a list nobody extended. One `prefixes.json`
now, read by TypeScript and by CommonJS; nginx cannot read JSON, so it is compared.

**Three guards were satisfied by a spelling rather than by the thing they name.** Two of them went
vacuous the moment the Makefile's addresses became variables — `assert "curl -fsS
http://localhost:8001/readyz" not in showcase` looks for a string that no longer exists, and
`assert "4200" in target` was checking for a number the file had stopped containing. Both would
have passed with the weaker readiness loop right back in. The third was mine, in this same session.
A guard that cannot fail is a guard that is gone.

**The KIRA surface lost an audit row in silence.** `contextlib.suppress(Exception)` around the
recording of a refusal keeps `FRD-122` FR-7 — never turn a correct refusal into a server error —
and drops the second half: no row, no log line, nothing for anybody reviewing the audit to notice.
The Gemini surface has logged `audit_refusal_not_recorded` since the shield was written there. Two
surfaces, one governance question, two answers; `test_surface_layering.py` now fails on a blanket
suppression around a recording call, so the third surface cannot inherit the wrong one.

**`e2e/` was never type-checked.** `"strict": true` in its tsconfig and no reader: `ng build`
covers the console and stops at its own `src/`. A spread over `NodeListOf<Element>` had been
sitting in `layout.spec.ts`, compiling under Playwright's transpile and failing under `tsc`. Now in
`make lint-frontend`, which is what CI runs — the same shape as the ESLint claim `CLAUDE.md` had to
retract in August.

And wiring it up immediately paid for itself twice. The tsconfig said `"module": "ESNext"` while
Playwright transpiles to **CommonJS**, so the new `stack.ts` type-checked an `import.meta.url` that
threw `Cannot use 'import.meta' outside a module` on the first run — a type check configured for a
module system the code never sees is a type check that agrees with itself. It now says `CommonJS`,
and rejects that expression; `stack.ts` was also missing from `include`, so the file every spec
imports was only checked by accident.

**A raw INSERT in the integration layer listed its columns by hand**, with a comment predicting
that it would break "the moment a field is added". `include_reasoning` was added, and it broke. The
comment was right and was not a fix: the column list is now asked of the Django model.

Also: `FRD-123` promised `make verify-local`, which has never existed (`verify-up`). Guarded now,
excluding `DEVLOG.md` and `LESSONS.md`, which quote dead targets *because* they were dead.

**And one bound stated twice.** `SchemaBounds`' dataclass defaults and the three
`max_response_schema_*` settings carried the same numbers independently. Production always passes
the settings; every test in `test_response_schema.py` uses the dataclass. Let them drift and the
tests go on passing while measuring a limit production does not have — the stand-in more permissive
than the thing it replaces. `core/` must not import `config`, so the agreement is asserted instead.

**The browser suite had been red since the model editor was split into tabs, and nobody had
looked.** Nine specs went straight from "open the editor" to a field, and the fields now live on
three tabs — `element(s) not found`, which reads as *the control is gone* rather than *it is one
click away*. That is the cost of shipping a layout change and running only the two lower layers:
the split was made and verified by hand in a browser, and the layer that exists to catch exactly
this was not run. `openEditorTab()` in `e2e/tests/support.ts` is the fix, a helper rather than nine
clicks, because the next tab moves fields again.

Seven new mutations (`TC7`–`TC13`), each broken and confirmed caught. 489 properties.

---

## A compatibility surface that refused the traffic it exists to accept (2026-08-18)

Six diffs came back from a real chatbot pointed at the KIRA surface in another environment. They
are small, and the reason they existed is not: every one of them is a place where a rule this
project holds — *no silent drop*, *a closed vocabulary*, *do not approximate* — was being kept by
a mechanism the rule did not actually require, and the mechanism was refusing valid traffic.

**`extra="forbid"` on the request shapes.** The chatbot sends fields the predecessor tolerated, so
every call came back `422` over a field that changes no answer. The obvious fix — ignore extras —
reintroduces exactly the defect `FRD-124` was written against, and the guard said so: a test
asserts that `conversationHistory` is refused, *"because an ignored `conversationHistory` would not
error, it would answer without the conversation."*

Both are right, about different fields. So the line is drawn by what ignoring the field would
**do**. A name that differs from a modelled field only in case or punctuation is something the
caller meant to set, and accepting it silently drops what they sent: refused, naming the field and
the spelling this surface takes. Anything else is accepted and **named** — in an
`X-AIRA-Unmodelled-Fields` header, set in `_sunset()` where all three exits already go, so it
reaches refusals too. The near-miss check normalises both names rather than listing four, so it
holds for the field nobody has thought of yet, and it runs on the nested shapes where `mimeType`
for `mime_type` is the same hazard.

Two tests changed sides, and that is the honest record of it: `test_the_kira_surface_refuses_an_
unknown_field_too` now asserts the field is *named* rather than refused, and the spelling guard now
checks each camelCase name against the shape that actually has the field — `taskType` was being
asserted against `ChatRequest`, which has no `task_type`, so the old blanket refusal was answering
for a property nobody had checked.

**`additionalProperties` was never an unknown field, only a missing one.** It means the same thing
in OpenAPI 3.0 and JSON Schema, and every client generating a schema from a typed model emits it on
each object. Refusing it returned "not a field of the supported schema vocabulary" for a field that
is. Added to the vocabulary; the vocabulary stays closed otherwise, because a schema field
constrains the answer and a dropped one is invisible. The test that used it as its example of an
unknown field now uses `unevaluatedItems`, which genuinely is one.

**`minimal` is not a value of the OpenAI dialect.** It exists on one vendor's newest family; every
other server answers `400 invalid value`. So the least-thinking mode was unreachable on every
OpenAI-compatible server there is. It is sent as `"low"` — the adjacent level that exists. This is
not the rounding refused for `limited`: there the caller named a number, here the dialect has no
number to name. `minimal` therefore goes back into both local seeds, where it had been struck in
August for precisely the reason now fixed; the pair-guard that keeps those two seeds in step
carried the correction across, which is what it is for.

**A streamed non-200 was a 500.** The upstream's body had not been read when the status was judged,
so the reason never made it into the error — the same request non-streamed answered `400` with the
reason in it. `await response.aread()` before judging.

**The refused body now goes to the log**, capped at 12 KB. It was already on the audit row, and the
audit row was not enough: diagnosing this meant a database query against a use case that may not
have payload storage on, while the operator had a terminal open.

Six mutations added (`TC1`–`TC6`), each run to confirm the new tests notice when the property is
broken. `Y8` — which broke `extra="forbid"` — was re-anchored rather than deleted: the property it
named is intact, it simply has two halves now.

`TC6` earned its keep immediately: the first version of the streaming test passed **and** the
mutation survived it, because an `httpx.Response(400, json=...)` has its content already in hand,
so `_reason()` could read the body whether or not the fix was there. The test was green for a
reason unrelated to what it was named after — the trap `CLAUDE.md` names, walked into again the
same day. A response built over a real byte stream is unread until somebody reads it, and the
mutation then dies as it should.

---

## The console named its own address in three places, and a tabbed dialog that jumped

*"We are building enterprise software here, not a junior learning project."* Fair, on both counts.

**The dialog jumped.** `.modal` is centred (`top: 50%` plus a translate) and sized to its content,
which is right for a dialog whose content does not change while it is open. Put tabs in one and
every switch resizes it *and* re-centres it: the window moves up and down as the reader crosses
sections. A `modal--steady` keeps a fixed height bounded by the viewport, so the chrome stays put
and the body scrolls — no per-tab measurement and no number to revisit when a field is added.

**And the addresses.** Yesterday's grep found two and I reported two. There were **three**, in three
different mechanisms, each of which would have survived a search for the other two:

- `auth.config.ts` fell back to `http://localhost:8080/realms/aira` — so a deployment whose
  `runtime-config.js` failed to load sent every user to a login page on their own machine, and the
  error named neither the realm nor the reason;
- `index.html` carried a **second** CSP as a `<meta>` tag, compiled into the bundle, naming that
  same origin. The nginx header could follow the issuer all it liked; the meta tag still refused
  the connection. A built artefact cannot be templated by nginx, so the entrypoint substitutes a
  placeholder;
- the header itself was the separate variable that "has to agree", closed earlier the same day.

One variable decides all three. Proven by starting the image with the issuer moved to `:8090`:
`runtime-config.js`, the meta policy and the header all named `keycloak.example:8090`.

The fallback did not become a thrown error, though that was the first attempt: `authConfig` is a
module-level constant, so throwing failed the *import* and took four test suites with it — and in
production would have failed before the shell that could explain it exists. The issuer is empty
instead, read at the point of use rather than at import, and `AuthService.init` reports it through
the `startupError` every other startup failure already uses.

`tools/tests/test_the_console_carries_no_address.py` is what stops the fourth: no shipped `.ts` or
`.html` may name a host or a port. A dev-server proxy and a Dockerfile default are configuration of
the build, not knowledge inside the product.

---
## Two stacks on one machine, and a console that knew its own address by heart

Reported after a parallel system was brought up beside this one: *"that was a catastrophe"*, and
*"the URLs and ports are plain text in the frontend, so changing one means going through the whole
frontend — that must not be."* Both true, and each in a different way than it looked.

**Ports.** Three of fourteen published ports were variables — gateway, management, console. The
other **eleven were literals**: Postgres, Keycloak and its health port, Kafka, Schema Registry,
Vault, Redis, Grafana, both OTLP ports, Ollama. A second stack collides on every one and the only
way out was editing the compose file. All fourteen are `${AIRA_PUBLISH_…_PORT:-<today's value>}`
now, so nothing moves by default.

The prefix is `AIRA_PUBLISH_` and that is not decoration: `AIRA_POSTGRES_PORT` is already a
**setting** — the port the gateway *connects to*. Using it for the published port would make
"move the published port" silently mean "connect to a different port inside the network", where
Postgres still listens on 5432, and the failure would read as the database being down. The guard
that caught it was the phantom-name check written yesterday, on its second day.

**And the collision a port cannot fix**: every `container_name` was fixed, so two stacks both
wanted to be `aira-postgres` and Docker refuses that whatever the ports say. `${AIRA_STACK:-aira}`
prefixes all twenty-two, which also gives the second stack a legible identity in `docker ps`.

**The console.** Better than "plain text everywhere" — the issuer and client id already came from
`runtime-config.js` rather than the bundle. But that file was **static inside the image**, and the
CSP's `connect-src` was a *second* variable that, in the compose file's own words, "has to agree
with it". Two places, two formats, and moving Keycloak meant editing a JavaScript file inside a
built image. The entrypoint writes the file from `AIRA_OIDC_ISSUER` now and derives the policy from
its origin: one variable, and a pair that cannot disagree.

Getting that right took two measured failures, both of which would have shipped:

- Written as `10-…​.sh`, the derivation ran and vanished. The nginx entrypoint **executes** a `.sh`
  in a subshell and **sources** a `.envsh`, so only the second can export anything the templating
  step will see. The console came up, the file was correct, and the served header read
  `connect-src ;` — found by reading the header off the running container rather than trusting that
  setting a variable sets it.
- Renamed, it still did not follow: the Dockerfile *already set* `AIRA_CSP_CONNECT_SRC`, so
  `${VAR:-derived}` never fired. A probe with the issuer moved to `:8090` still served
  `localhost:8080`. **At the default port the wrong value happens to be the right one**, which is
  exactly how it would have gone unnoticed. The image sets no default now.

Proven by rendering a second stack (`AIRA_STACK=aira2`, four moved ports) and by starting the image
with a moved issuer: `connect-src 'self' http://keycloak.example:8090`, derived.

---
## The model editor is three tabs, and approval is on none of them

Eighteen fields in one column, with the input price sitting between the provider and the publisher.
Split by the question each field answers — **what it is** (id, display name, provider, publisher,
platform, hosting, KIRA id), **what it can do** (capabilities, the output caps, and the thinking,
embedding and attachment declarations), **what it costs** (the four prices). The capability
checkboxes and the blocks they reveal are on the same tab on purpose: nobody should tick a box on
one screen and go looking for its consequences on another.

**Approval is deliberately not a tab, and that was the decision worth making slowly.** It is not a
property of the model — it is what makes the model callable (`FRD-307`) — and it starts *off*.
Behind a tab, somebody fills in three screens, presses Save, and creates a model nothing can call,
with the switch they never opened sitting at its default: a control that did nothing, arrived at by
never being seen. So it sits beside Save, visible whichever tab is open, which is the one place its
state cannot be missed at the moment it takes effect. Deprecation keeps it company — both are
statements about the model's **standing** rather than about what it is.

Values live in signals, so switching tabs keeps everything typed; only the DOM comes and goes. A
reset returns to the first tab, or reopening the editor would show "what it costs" and no name
field. Two tests hold the arrangement: one that approval is reachable from every tab, one that each
field is behind the question it answers — otherwise the next field is appended wherever the file
happens to end.

The pencil on the use-case description is a plain **Edit** button now. An icon there was a guess at
what "a pencil or something" meant, and the answer was: a button that says what it does.

---
## A model's reasoning, and six things the console got wrong about it

`FRD-135`: thinking is **counted always**, returned **where a use case says so**. The counting is
not a setting — providers bill thinking at the output rate and an installation does not get to
decide whether it was charged. Measured before the fix: `prompt=25 candidates=1 thoughts=143`, of
which AIRA recorded 26 of 169. `reasoning_tokens` is a subset of `completion_tokens`, the invariant
`FRD-133` set for cache tokens, so every existing price, budget and report keeps meaning what it
meant. Over the live matrix run: 9337 completion tokens of which **4272 were thinking** — 46% of
the bill would have been invisible.

Returning it is per use case and **off**. Off means the request is refused **by name**; the schema
level refusal moved to the surface, where the use case is known, and did not soften. On, the
thoughts come back marked as Google marks them and are stored **in the response payload** — same
column, same `store_payloads` gate, same retention, same role to read. No second storage path,
because a second one is a second retention bug.

**The console then reported six problems, and five were real.**

*The toggle was missing* — because the frontend image had not been rebuilt. My fault, and the sort
that wastes somebody's evening: the code was right and what they were looking at was three builds
old.

*The description fields were a form.* Two textareas standing open where a reader wants a sentence.
Read view with a pencil now, and a cancel that **forgets the draft** — keeping it would show, as the
description, something the server has never been told.

*The model form's thinking section could not be read.* Fair: *"it says thinking disabled — is the
thinking block disabled, or does the model become an instruct model?"* Neither, and the label said
only the wire value. Every box now says what it declares — *can be told not to think at all* — and
the budget bounds are separated from the mode list with the sentence that was missing: they bound
what a caller may ask for with `limited`, they are not output caps, and the thinking budget comes
**out of** the output allowance rather than beside it.

*Check reachability answered nothing for Gemini models*, because the Agent Platform adapter had
**no probe at all** — `/readyz` said "no probe available; not checked" and the console repeated it.
It has one now: `:countTokens`, which Google does not charge for. A probe that generates costs money
to answer "are you there", every time anything asks.

*Add from provider had no Vertex* — filtered out for not publishing a list, with a general sentence
underneath. An absent provider is indistinguishable from a credential that failed. Listed now,
marked *publishes no list*, with a panel naming why and a button that opens the editor with the
platform's provenance filled in. And the "not served" message no longer claims a missing credential
is the only cause: on this platform the key can be perfect and the model simply absent from
`AIRA_VERTEX_MODELS`.

**The sixth was not a console problem at all**, and it took three wrong diagnoses to find. Requests
were being refused with *"personal data could not be removed"*. First reading: the length guard is
too strict for short prompts — true, and fixed (a proportion is a statement about prose; on `"say
ok"` the bar is two characters). Second: the redactor was spending its whole allowance thinking —
true, and caused by **my** catalogue entry declaring the `thinking` capability with no thinking
block, so the pipeline could not tell the model to be quiet. Third, and the real one: with thinking
off the redactor still answered `"9"` — it had **solved the riddle in the caller's text** instead of
rewriting it, with an instruction that already said *do not answer*.

That is prompt injection against an **internal** step, and the pipeline's own injection filter does
not protect it: that filter decides whether to serve the request, while this one hands the same text
to a second model as a task. The text is passed as **data between markers** now, with the model told
that everything between them is data. Verified against the real model both ways. `LlmRedactor` had
no direct test at all before this round; it has nine.

---
## The Agent Platform adapter takes an API key too (`FRD-115` FR-3a)

The owner put a key in and nothing worked. Google's answer named the cause exactly:
`403 API_KEY_SERVICE_BLOCKED`, consumer `projects/858738136418`, service
`generativelanguage.googleapis.com`. Not a network problem, not our code — the key reaches Google
and Google refuses it *for that API*.

**Because it is not an AI Studio key.** Vertex AI is now
[Gemini Enterprise Agent Platform](https://cloud.google.com/products/gemini-enterprise-agent-platform),
and it issues API keys to accounts that never create a service account. AIRA had two Google
adapters and neither fitted: AI Studio has the right credential and the wrong host, Vertex has the
right host family and exchanges a service-account JWT. Measured with `countTokens`, which Google
charges nothing for: the key answers `200` on `aiplatform.googleapis.com` for `gemini-2.5-flash` and
`gemini-3.5-flash`, and embeds on `gemini-embedding-001` (3072 dims) and `text-embedding-005`.
Four prompt tokens spent to learn all of it.

**The residency question decided the shape, and the first answer was wrong.** A 404 message
mentioned `locations/europe-west1`, which I reported as EU residency. Google's own
[data-residency page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/data-residency)
says the opposite about the *global* endpoint express mode documents: it "routes and processes data
anywhere globally… you can't control or know which region". A resource path is not a processing
guarantee, and I had read one as the other.

So the feature could have been a fifth provider on the global endpoint, declared `global` like AI
Studio — and then a probe settled it: **the same key answers `200` on the locational host**
`europe-west1-aiplatform.googleapis.com`, on both path forms. Which makes this not a new provider
at all but a **second credential on the existing one**: same hosts, same paths, same per-model
region, `x-goog-api-key` instead of `Authorization: Bearer`. An installation with nothing but an API
key gets the residency `FRD-115` FR-5 exists for.

Set both and the **service account wins** — a deployment that rotated to one and left the old key
in the environment would otherwise keep using the key, and Google's audit trail would go on naming
a credential somebody thought was retired (`C26`).

**Both credentials from Vault, verified against the running server** rather than assumed, because
that is what was asked for: both arrive, and the multi-line PEM survives the round trip. Also
learned there: **Vault wins over the environment** (`FRD-116` FR-3) — my test asserted the opposite
and was wrong, not the code. And `VaultSource._cache` is a *class* attribute, so a case that stubs
Vault leaves its values standing for every case after it; the builder test three cases later was
handed a key it never configured. `reset()`'s own docstring says tests must not share that cache and
nothing was making it true.

Documented as the owner asked: `docs/INTEGRATIONS.md` now carries both paths step by step — service
account (enable API, create account, one role, JSON key, store, name project and models) and API key
(get it, check its API restrictions, store it, the rest identical) — with the Vault form for both
and links to Google's own pages.

---
## Tokens from more than one realm (`FRD-118` FR-1)

Confirmed by the owner, and with it the question §11 of that FRD has been holding open since it was
written: **one population or several?** Answered *one* — a migration between realms, a second
instance, a merger, where the same group path from either means the same thing because it is the
same directory content. That is what keeps this a configuration list rather than a schema change.
Two *unrelated* directories would need `(issuer, sub)` as the identity and an issuer column on
grants and memberships; that stays unbuilt and is now written down as such.

`AIRA_OIDC_ISSUERS` is `issuer|audience|jwks_uri` per entry, `;`-separated, and `settings.issuers()`
returns one list whether the single pair or the list was configured — so nothing downstream has to
ask which form it got, which is the shape `FRD-126` keeps arriving at.

**Routing is by the token's own `iss`, read unverified — a hint, never a trust decision.** The
verifier it selects then checks `iss` and the signature for real, so a token signed by realm A while
claiming realm B is refused by both: B has the wrong key, A has the wrong issuer. That property is
`C23`, and it is the one an implementation gets wrong by returning the first verifier that says yes.

The alternative — probe every verifier — is what makes `kid` selection expensive rather than wrong:
PyJWT answers an unknown key id by **refetching the whole key set**, so a token from realm B would
cost realm A a remote call on every single request. Two tests count the fetches, including the case
where a token names a realm and that realm refuses it: trying the others would only produce the same
refusal from each, at the price of a key-set refresh apiece — the cheapest denial of service there
is, aimed at our own upstream.

**A hardening check nearly stopped applying.** `unsafe_settings` read `oidc_audience`, which a
multi-realm deployment leaves empty — so the rule that makes an audience unavoidable outside `local`
would have passed vacuously for exactly the deployments that need it most. It walks every issuer now
and names the one that is missing an audience (`C24`), and a second problem was added beside it:
OIDC declared on with **no** issuer at all, where the validator is never built, every bearer token is
refused, and the configuration reads as though single sign-on were working.

The audit row carries the issuer (`0036_log_issuer`, nullable — back-filling history with today's
realm would be a claim nobody checked). And a test stand-in was replaced by the real `Attribution`
dataclass: a `SimpleNamespace` that is *less* complete than the thing it replaces breaks the moment
that thing grows a field, which is the same mistake as one that is *more* permissive, seen from the
other side.

---
## Sixty seconds, and only for the clock that is behind (`FRD-134`)

Built as specified, and the shape is the point: PyJWT applies one `leeway` to `iat`, `nbf` and
`exp`, and those are not one question. A token whose `iat` is in the future means **our** clock is
behind the issuer's, and accepting it extends nobody's access — the token was genuinely minted. A
token past `exp` is a credential living longer than the issuer granted. `AIRA_OIDC_CLOCK_SKEW_SECONDS`
defaults to 60, `AIRA_OIDC_EXPIRY_LEEWAY_SECONDS` to 0, both refused above 300 s at construction —
above a token's own lifetime it stops being tolerance and becomes a second lifetime.

The implementation is deliberately **subtractive**: `decode` gets the clock skew, which covers
`exp` too, and a second check then refuses what the expiry leeway does not cover. It can only
reject what `decode` allowed, never the reverse, so no verification is reimplemented and `exp`
stays required. At the defaults `exp` behaves exactly as it did before; only a clock that is behind
became forgiving. `C21` breaks that second check and a test notices — which matters, because it is
the line a later refactor deletes as redundant on the grounds that `decode` looks like it already
checked.

Both planes, from one place: the gateway and the management backend build the same `JwtVerifier`,
and a tolerance that held on one would sign somebody into the console and refuse the same token at
the gateway. The refusal is logged with the claim and the tolerance — *"a token refused as
not-yet-valid usually means this host's clock is behind the issuer's"* — and the caller still gets
`401` and nothing more, because reporting our clock to an unauthenticated caller is a disclosure.

---
## The kill switch could only ever do the blunt thing, and a question could not leave the catalogue

Fifth pass, and both areas had a finding.

**Suspensions.** `POST /v1beta/suspensions` accepts `use_case`, `action` and `throttle_rpm`, the
matcher obeys all three, and the suspensions table **renders** each of them — because a *rule* can
create a scoped or throttled suspension. The manual form sent none. So every decision a person made
during an incident was a **full block, everywhere**: a credential bound to one use case was stopped
in all of them, and *"hold this caller to ten a minute"* could not be said at all. The gentler and
narrower options existed, were displayed, and belonged to the automation only.

The form now has a scope picker (default *everywhere*, so nothing changes for somebody who does not
care), block-or-throttle, and a rate that appears only for a throttle — and `canSubmit` refuses a
throttle without one, because the server does and an incident's first minute should not be spent on
a 400.

**The question catalogue.** `TestResult.case` is `PROTECT` and the model has said *"retired rather
than deleted"* since it was written: a verdict was formed against that wording, so deleting the
question would take the verdict with it. Nothing caught the `ProtectedError`, so *Remove* raised an
unhandled exception — a **500** — for every question the catalogue had ever been run against, behind
a confirm box promising *"answers already given to it stay."* And `retired`, the field built for
exactly this, had **no caller anywhere**: not in the console, not in the API layer, only in a
migration.

Both halves: the server refuses the delete by name and says to retire instead, and the console's
button is *Retire*, which is a PATCH that leaves the answers with the wording they were judged
against. An unanswered question — a typo — is still deletable, because making it permanent would
leave the catalogue with a record of a mistake.

The kill-switch guard reads `create_suspension`'s own `body.get(...)` calls rather than a
serializer, since that endpoint deliberately bypasses Management and Kafka. Mutations `C19`/`C20`,
both caught. 2372 Python tests, 843 Angular.

---
## API keys and anomaly rules: clean, and now kept that way

Fourth pass. Both came out **reachable in both directions**, which is the first time in this audit
that has happened, and it was checked rather than assumed.

*Anomaly rules.* One form authors a use case's rules and the global ones — `rules-tab` imports it —
and every field the evaluator reads has a control: kind, window, threshold, parameter, sample floor,
action, target, action minutes, throttle, and `enabled`, which decides whether the rule is consulted
at all. The upsert was checked for the trap budgets had (`data.get("enabled", True)`) and does not
have it: DRF leaves an absent boolean out of `validated_data`, so `defaults=values` preserves it.
Measured: disable a rule, re-post it without the field, still disabled.

*API keys.* Issuing has its own contract — label, owner, lifetime — and the console sends all three.
Everything the gateway consults about a key (`is_active`, `revoked_at`, `expires_at`, `subject`,
`use_case`, `label`) is set at issue or by revocation. Revocation is deliberately one-way and a
lifetime is not extendable: you issue a new key.

**One thing worth hardening.** `ApiKeySerializer` renders the masked view and nothing writes with
it — so it carried no `read_only` marking at all, and `is_active`, `revoked_at`, `prefix` and
`issued_by` were caller-settable *by shape*. Not a live defect; one
`ApiKeySerializer(data=request.data)` away from being one, and the shape of that mistake is a caller
reviving a revoked credential or choosing the prefix of somebody else's. Every field is read-only
now, said out loud rather than left to the fact that no endpoint writes with it today.

**And a guard was satisfied by the wrong source again.** The rule check first matched any
`: AnomalyRule =` literal in the form file — and the other one is `NEW_RULE`, the *template* a blank
form starts from, which naturally names every field. It passed with `min_sample` deleted from the
payload: a default answering for a control. Same tell as the budget/rate-limit guard earlier the
same day — it kept passing under the mutation it was written to catch. **Point a reachability check
at the thing that is sent**, and prove it by breaking that thing. Both guards now read `submit()`
and `issueApiKey()` respectively. Mutations `C17`/`C18`.

2367 Python tests, 841 Angular.

---
## Budgets and rate limits: a switch the gateway obeys and nobody could flip

Third pass of the same audit. `enabled` exists on both models, travels on both events, and is
**obeyed by the gateway** — `budgets/service.py` selects only enabled budgets, `ratelimit/service.py`
skips a limit whose flag is off. The rate-limit table has printed an **Active / Disabled** badge
since it existed. No screen could change either.

**And it was worse than unreachable.** Both endpoints *upsert* — POST keys on
(scope, subject[, period]) and is how an existing row is edited — and the handler read
`data.get("enabled", True)`, while the console's body never mentions the field. Measured before
fixing: disable a budget, change its token cap from the console, and it is enforcing again. A limit
somebody deliberately lifted for an incident is a decision, and silently reversing a decision is
worse than never offering the switch. `test_a_save_that_says_nothing_about_enabled_leaves_a_lifted_budget_lifted`
is that measurement, kept.

Both halves fixed, because either alone leaves the defect. The handler now writes `enabled` **only
when it was said** — absent on a create still means the model default, which is on. And both tabs
carry a switch: *Active — disable* / *Disabled — enable*, sending the whole row back because the
endpoint upserts and a body carrying only the switch would blank the figures beside it. A reader
who may not manage sees the state and no control — the budget card did not even show it, so a use
case could be spending against a budget the console displayed and the data plane ignored.

The guard now covers both serializers. It reads the **typed object literal** each tab builds
(`const budget: Budget = {…}`), because an untyped `{...}` would let a field disappear from the
payload with the guard still passing — and it is **scoped to the file that owns the payload**: the
first version counted spreads from every panel at once, so the rate-limit tab's `{ ...limit,
enabled }` answered for the budget tab's, and the guard passed with `enabled` removed from budgets
entirely. A guard that a different screen can satisfy is not a guard. Caught by trying it.

Mutations `C15` (the upsert default) and `C16` (a field dropped from the payload). 2364 Python
tests, 841 Angular.

---
## The same audit for use cases and pipelines — and a search tool that lied

*"Mach das gleiche für use cases und pipeline."* Same method: what the column is, what the API
takes, what the console sends, what the screen prints.

**The pipeline came out clean**, and that is worth stating because it was not assumed. All twelve
step-config keys `engine.py` reads — `action`, `mode`, `scope`, `use_builtins`, `patterns`,
`instruction`, `on_undetermined`, `notice`, `on_failure`, `model`, `default_model`, `categories` —
are settable in the builder, and a routing category can be given all three of its fields including
the **description**, which is the string `classifiers.py` puts in the router's prompt as
`- {name}: {description}`. A category somebody could name and not describe would route by nothing
and look configured.

**The use case had two**, and they were the KIRA id exactly: `description` and `processing_notes`
are accepted by the API, carried to the gateway's read-model, **printed on the overview** — and
offered by no screen. *"No description."* was what every installation ever saw. `processing_notes`
is the sentence somebody writes for a data-protection review, so a governance record nobody could
author is a governance record nobody had. Both now live in an `about-panel`, greyed for a reader
who may not manage, and the parent's two paragraphs are gone.

**And the audit nearly reported a defect that was not there.** `grep -rn allowed_models` across
the console found nothing that writes it, which would have made *"which models a use case may call
cannot be set from the console"* the headline finding. It is set — by `model-release-panel.ts`,
which compared two lists with `join('\0')` written as **raw NUL bytes**. Valid TypeScript, the same
string to the compiler, and enough to make grep classify the file as binary and skip it **with no
message and exit status 1**. The one file that answers the question was the one file the search
could not see, on the day the question was asked.

That is the worst failure a search tool has — an empty result and a true search look identical —
so it gets a guard of its own: no tracked source file may carry a byte that makes standard tools
treat it as binary (`tools/tests/test_source_files_are_readable_as_text.py`). The escape is three
characters and reads the same to the compiler.

Guards, both directions, both planes: every writable `UseCaseSerializer` field must be sent by some
panel (`slug` exempt, with the reason — it is the key in every client's URL), and every step-config
key the **engine** obeys must be authorable. Proven by breaking each: remove `description` from the
panel's payload, rename `use_builtins` in the builder — each is named by the guard. Mutations
`C13`/`C14`. 2361 Python tests, 831 Angular.

---
## Every control a model needs, checked one by one — and made a guard

*"Heute hatten wir ein unerreichbares Feld mit der KIRA Model ID. Ich will nicht, dass es sich
wiederholt — geh alle einzelnen Elemente durch."* So: model field by model field, comparing what
the column is, what the API accepts, what the form offers and what the panel prints.

**Two more of the same shape, and one of them costs money.**

*The attachment estimate.* The console could tick `image/png` for a model and never say what one
costs, so every declaration written there sent `{"image/png": null}` — and the gateway reads a
missing estimate as **zero**: `attachment_tokens` sums only the entries that are objects. A request
carrying a 20 000-token document was reserved for as if it were a sentence, which reopens under
documents exactly the race `FRD-405` closed for text. The figure was already *displayed* beside the
type whenever the API had put one there. Displayed, unsettable, and load-bearing — the KIRA id
again, with a budget behind it. The form now asks, and unticking a type forgets its number rather
than keeping one nobody can see.

*`underlying_model` and `addressing`* were the opposite trap. Both are stored, both travel to the
gateway on the config event, and both are **dropped** on the way into `ModelDeclaration` — the
object every dispatch decision is made from. Nothing has ever read them. Printed among Provider,
Platform and Hosting they read as configuration, which is precisely what made *"KIRA id —"* look
like a field somebody had left blank rather than one nobody could fill. Giving them inputs would be
the same defect wearing the other mask: a control you can set that changes nothing. They are off
the panel; the columns stay, unread, and if a reader ever appears it brings a control with it.

**The durable half is the guard**, because a list of findings is true for one afternoon.
`test_every_model_control_is_reachable.py` asks the question from both ends: every writable field
of `ModelSerializer` must appear in what the console sends, and — the one that matters — **every
field of `ModelDeclaration` must be enterable in the console**, because a dispatch decision taken
on a value nobody can enter is what the KIRA id was for a whole API surface. Exemptions carry their
evidence, and a second test fails when an exemption outlives its field.

Proven by breaking it: with `numeric_id` removed from the payload the guard names it. That is also
mutation `C12`, so a later refactor that loosens the guard is caught rather than trusted. The use
case's own model controls were checked the same way and are complete — release, approval, caching,
tools, retention, member scoping all reach the console.

---
## A model catalogued from the console could not be called by a KIRA client

Reported plainly: *there is no way to give a model a KIRA id, and none is filled in either.* Both
halves were true, and together they made a control that displayed as working and did nothing.

`numeric_id` is how `/kira/api/external/chat` names a model — that surface identifies models by
integer, never by name. The column exists, the API has always accepted it, the model detail panel
even prints **KIRA id —**. The *form* never asked. So every model added through the console went in
with `NULL`: catalogued, approvable, releasable, listed on the Gemini surface, and answered
`MODEL_NOT_FOUND` by the KIRA one, with nothing in the refusal that would tell the reader why. The
only models that worked there were the two the demo seeds with ids written into the seed file.

Two fixes, because either alone leaves half the defect. The form now offers **KIRA id**, so an
installation migrating from the predecessor can give a model the exact number its clients already
send — which is what the field is *for*. And when nobody types one, the server assigns the next free
number from `9500` upwards, above everything this repository seeds or documents, so a model
catalogued without a thought about KIRA is still reachable there. A number nobody chose beats no
number.

Uniqueness was already a database constraint, which answers a caller with a 500 and a sentence about
a key name; DRF's generated validator would have improved that to "model with this numeric id
already exists" — true, and it leaves you to go and find which one. The refusal now names the other
model. Dropping DRF's validator to say it better took its range check with it, so `< 1` is checked
explicitly; the constraint underneath is untouched.

Guarded at both ends: the Angular test types into the rendered input rather than setting the signal,
because the defect being prevented is a control that renders and sends nothing — and a signal set
from a test renders nothing. Three mutations (`C9`–`C11`) break the auto-assignment, its climb past
ids already taken, and the duplicate refusal; all three are caught. 457 properties now.

---
## The anomaly evaluator was wrong for every instance after the first

Asked to fix the defect `FRD-127` turned up while it was being written. It is the kind that cannot
be found by reading one process: everything about the evaluator is correct at N=1.

Every gateway instance runs the evaluation loop, and three things about it were **per-process** —
the set of scopes that saw traffic, the cooldown map, and the decision to evaluate at all. So two
instances read the same shared `request_logs`, reached the same verdict, and wrote an event each,
while both sat inside their own cooldowns: the mechanism meant to stop repeat firing was the one
thing that could not see the repeat. With enforcement on, one finding became one suspension per
instance — one decision, several authors.

All three are shared facts now. **Which scopes saw traffic** is read from `request_logs` rather
than from a set the audit writer filled, because that set held only the requests *this* instance
served — behind a load balancer the evaluator knew about its own fraction and the rest was measured
by no rule at all, silently: it evaluated, found nothing, and looked exactly like a quiet minute.
**The cooldown** is the `anomaly_events` table. **One evaluator per tick** is a transaction-scoped
Postgres advisory lock — released however the transaction ends, where a session-scoped one survives
a crashed process and would leave the fleet with *no* evaluator, which is the worse failure.

**A second defect was hiding inside the first**, and it is the one that would have bitten every
deploy rather than only a scaled one: the cooldown was in memory, so a restarted instance began
with an empty map and re-fired every rule the moment it started, describing traffic its predecessor
had already reported. A rolling update makes that the normal case.

**The plan changed while implementing it, and the FRD says so.** It recommended moving the
evaluator to its own container — structurally the better answer, and it would have meant every
existing deployment silently stopped detecting anything until an operator added the container. A
capability that disappears on upgrade unless somebody reads the release notes is worse than one
that needs a lock. Worth recording that the lock *alone* would have been wrong too: the instance
that wins it has to see the whole fleet's traffic, so the touched set had to leave the process
first. Either half without the other is a fix that looks like one.

**The harness caught two survivors, and one of them was my own new test.** `QA29` — a failed round
gives its window back — survived after being re-anchored, because its stand-in sessionmaker raises
when it is *called*: the round never opened a session, so where the watermark is written was never
exercised. The test that fails during the evaluation, which is what a database blink actually looks
like, exists now. `N11` survived for a neighbouring reason: the rule for the untouched use case had
no traffic to find, so deleting the filter changed nothing. Its traffic now sits inside the rule's
window and outside the lookback, which is the only arrangement that can tell the two apart.

`on_written` went with the touched set — a hook that told the evaluator something it can now read
for itself, about one instance's share of the traffic. 454 properties.

Suites: 2337 hermetic Python at 95.94 %, `ruff`, `mypy` clean.

---

## Four corrections from the owner, and two of them undo a decision from the same day

Read in the console rather than in the code, which is why all four are things no test would have
found.

**A `<select>` is sized by its widest option, and nothing above capped that.** A use case with a
long name stretched the picker past the window and took the page's layout with it — on a
multi-monitor desktop the overflow ran onto the second screen. `max-width: 100%` was already on the
control and did nothing, because it is measured against a `.field` that is itself a flex item
sizing to *its* content: flex items default to `min-width: auto`, which is precisely what carries
an intrinsic width upward. Both halves were needed, `min-width: 0` on the field and a real ceiling
on the select.

**"This use case has no pipeline" was a sentence that should never have been written.** The owner's
question — *how can a use case have no pipeline?* — has no good answer. A request comes in and a
request is dispatched; the steps are what happens in between, and none means nothing happens. That
is a configuration, not an absence, and the message sent a reader off to build something that
already existed. The refusal is gone; `PipelineConfig` now says so in its own docstring, because
the code invited the mistake by treating a missing row as a missing pipeline.

**The start model came off the pipeline again**, one day after it went on. The objection is the one
that matters and I had missed it: a use case releases several models *on purpose*, and naming one
on the pipeline reads as *this is the model this use case uses* — it narrows, in the reader's mind,
a decision the release deliberately left open. It also made the wrong thing the precondition, so a
use case was un-runnable for want of a pipeline field rather than for want of a model.

A run now carries its own entry model, picked when it is started and **bounded by what is released
to that use case**. That bound is what the pipeline field was really buying: without it a caller
names a model the use case may not call, the gateway refuses at dispatch, and the run fills with
403s that say nothing. Reading the release list answers that without taking the choice away — and
without a run ever *writing* a release, which is what `_release_for_testing` did. Two runs of one
use case may now enter at two different models, which is exactly the comparison somebody evaluating
a model wants and the pipeline field made awkward. `start_model` is dropped from both planes
(`0003`, `0035`); the dry run goes back to inferring, which is a known and documented gap rather
than a new one.

**And read-only was rendered as prose.** The models released to a use case were shown, to anybody
who may not change them, as a paragraph of `<code>` chips. The owner's report is the whole finding:
*it does not look like a control, so the developer will not even read it* — the one piece of
configuration they most need, rendered as the one thing on the page that reads as decoration.
It is the same picker now, disabled, in the browser's own greyed styling. Fixing it properly meant
fixing the component underneath: `app-multi-select` **removed** its field when disabled, which is
how a control turns into prose in the first place. Disabled now means greyed, not gone — what
disappears is only what *acts*, the per-chip remove and the list toggle.

**And then the same gap one screen over.** With `start_model` gone, the *dry run* was inferring
again — and it is the caller with the strongest claim to be asked, because it is a builder testing
a rule and reading `effective_model` as the answer. It now has the same picker over the same
released models, defaulting to *let the gateway choose*, which keeps the inference as a deliberate
choice rather than the only option. The two fixes are the same shape: **the question belongs at the
point of use, bounded by the permission that already exists.**

`ADR-0020` carries a `Superseded, same day` note rather than a rewrite, because what a decision got
wrong is worth as much to the next reader as what it got right.

Suites: 2340 hermetic Python at 95.99 %, 820 Angular at 92.13 % branches, 452 mutation properties.

---

## The question catalogue stops being about models and starts being about pipelines

The owner's answer to a finding from the review below, and it deleted the finding rather than
fixing it. The review had flagged §3.7: `MayTestModels` let a broad set of roles start a run, and
the runner then called `_release_for_testing`, which wrote `allowed_models` on the seeded use case
so its own run would not be refused. I had left it as an explicit owner decision. The owner's
reading was that the role set was never the problem — a feature that has to quietly edit a
governance decision in order to work is a feature fighting the model it is built on.

**So a run is now the catalogue put to a use case, through that use case's own pipeline**
(`ADR-0020`). The catalogue itself is unchanged and still belongs to Global Administrators and IT
Security. What changed is the subject: a run names a use case rather than a model, may be started by
anybody the *gateway* would accept for it, and the pipeline decides which model answers. Testing a
model is then an ordinary use case — IT Security makes one, releases the models to it, points its
pipeline's start model at the one under evaluation. No special path, no internal attribution, no
release written as a side effect: `_release_for_testing` is gone.

Three things fell out of it that are worth recording separately.

**`FRD-504` §5.3 had been asking for this since it was drafted**, and said so in its own words —
*"the first honest measurement we would have of whether the injection filter earns its place"*. Two
modes were specified, *through the pipeline* and *direct to the model*; one was built, and it was
the other one. Every run went to a seeded use case whose pipeline was empty, so in a year the
catalogue had never once exercised a filter, a router or a redactor. The document had described the
gap accurately the whole time and nothing read it against the code. The two modes also collapse into
one mechanism now: run it against a filtering pipeline to measure the filter, against a bare one to
measure the model. A blocked question stops being a broken run and becomes the finding.

**A pipeline now declares a start model**, which is the field this needed and which the dry run had
been guessing at for months. `_model_the_pipeline_is_about`'s own comments record three guesses in a
row — the first registered model, the first released one, the first released one that can generate —
each documented as wrong in production and each reported back as `effective_model`, where a builder
reads it as a decision somebody made. It now prefers the declaration and keeps the guesses only for a
pipeline that declares nothing. Blank stays a real state rather than something to backfill: it means
*only a caller who names a model enters here*, which is every pipeline written before today, and the
console then says the use case cannot be run and **why** instead of inventing one. Deliberately not
part of `Pipeline.is_empty` — a pipeline with a start model and no steps still runs nothing.

**The mutation harness caught the rename before the tests did.** `Q1` — "a model's standing is its
latest run, never a total across every run" — had been anchored on `order_by("model", ...)`, and the
anchor check failed as soon as the axis became the use case. The property had not changed at all;
summing every run still lets an old, since-corrected result drag the current one down forever. It
was re-aimed at the same rule over the new axis, and four properties were added for the new
behaviour: a run enters where the pipeline says rather than where the caller says, a run may only be
started where the gateway would accept this caller, a use case with no start model is refused by
name, and the dry run prefers a declaration to a guess. That last one **survived** on the first run —
the field was read, and no test would have noticed if it stopped being.

**And then the rule narrowed once more, the same day.** The first version said a run may be started
by anybody the *gateway* would accept for the use case, taken from `FRD-504`'s own sentence —
*whoever may call a model may test one*. The owner's rule is that a normal use-case **user** does
not run it. That is right, and the sentence it replaced was written when a run was about a model the
whole installation had approved: a run is now a hundred prompts through somebody's pipeline,
spending that use case's budget, against a catalogue that states what this installation tests for.
A decision *about* the use case rather than work *inside* it — a distinction this codebase already
has a word for, `may_manage`.

So `may_run_tests_queryset` is `may_call_queryset` narrowed by `MANAGE`, plus the two installation
roles. A composition rather than a fourth access predicate, because both halves are separately
necessary and `LESSONS.md` §5 is a list of rules restated once too often. Two mutations, one per
half: a composition guarded only as a whole can quietly lose one.

Three things the change had to reach that the permission class could not. It is asked **per object
at every endpoint** — the class answers "is there *any* use case this person could run", which is
right for offering the screen and wrong for starting a run, and an administrator of one use case
passes it while naming somebody else's slug. Reading the catalogue follows running it, because the
questions say what to avoid (§8). And the screen itself withholds: reached by address rather than
by the nav, a 403 now takes the tab strip down and names who runs the catalogue, where a 500 still
reports a broken page — "ask an administrator" would send that reader to somebody who cannot help.

The suite was **green before any of this was tested**, because every runner in it was IT Security.
That is the shape `LESSONS.md` §7 opens with, and it is why `_member` and `_administrator` now exist
beside `_runner`.

**Running all four layers then found three more things, none of them in the feature.**

**The mutation harness refused to start**, reporting a **red baseline** — and it was right.
`test_a_dry_run_takes_the_rate_limit_like_any_other_request` sets one request per minute and its
*first* request came back `429`, `retry_after: 32`, a number nothing in the process could produce.
`redis_url` defaults to `redis://localhost:6379/0` and `make up` publishes a Redis on exactly that
address, so the **hermetic** suite had been sharing a durable bucket store with the developer and
with its own earlier runs. Green in CI, which has no Redis; green on a machine with the stack down.
`LESSONS.md` §7's *"a unit test that reads the developer's machine"*, and the stack coming back up
is what exposed it. Fixed with a session fixture setting `AIRA_REDIS_URL=""` — this codebase's
existing spelling for "no Redis" — plus `test_the_unit_layer_is_hermetic.py`, two tests guarding
both halves, because a fixture that does nothing visible is the kind that gets tidied away.

**A browser test read a sentence and believed it**, which is what a person would have done. The
Runs panel branched on `runnableUseCases().length`, and that list starts empty on every load — so
for as long as the request took, every reader was told *"there is no use case you may send requests
to"*, including readers for whom it is false. `LESSONS.md` §6: **unknown is never rendered as
zero**, and this is the variety that says something about somebody's *access*, which is exactly the
kind a person acts on — they go and ask to be added to a group they are already in.

**And my own new e2e test was wrong three times before it was right**, each time in a way that made
it pass-shaped rather than correct. It asserted that `ucuser` is offered no screen at all; earlier
suites had left group grants behind that made their Keycloak group an administrator of a throwaway
use case, so the assertion measured the database's history. Rewritten as the *difference between
two people on one use case* — `ucadmin` is `admin` of `kundenservice`, `ucuser` is `user` of it —
it then read "not offered" for **both**, twice: `option[value=…]` asks about the attribute where
Angular's `[value]` binding sets the property, and a `count()` after `goto` samples a page that has
not rendered. A test that answers the same for both sides of a comparison agrees with itself and
proves neither half. It also needed an explicit `logout` between the two sign-ins, or the SSO
session hands the second login straight back to the first user — it would have passed the day the
rule broke.

Suites: 2348 hermetic Python at 95.85 %, 806 integration, 144 browser, 821 Angular at 92.1 %
branches; `ruff`, `mypy`, `prettier`, ESLint and `tsc` clean; 453 mutation properties.

---

## A security and correctness review of the whole codebase — 26 findings, all fixed

Asked to read the whole thing as a reviewer and write down what was wrong. The four suites were
green, `ruff` and `mypy` clean, and coverage at 95.7 % — which is the condition this project already
says proves nothing on its own, so the review was done by reading each control against the sentence
that describes it and by measuring the ones that could be measured.

**Two were serious, and both were a correct block of code in the wrong place.**

- `?may_call=true` widened the queryset in `get_queryset`, and DRF resolves every detail route and
  every `@action(detail=True)` through it. Measured: a caller holding nothing but the Keycloak
  group `/use-cases/secret-uc` got `404` without the parameter and `200` with it — plus the member
  list, the budgets, the rate limits, the pipeline configuration and the API-key metadata. The
  mutations were never reachable (`_may_manage`/`_is_member` ask independently), so it was
  disclosure rather than escalation. It belongs to `list`, and it is asked there.
- `/v1beta/anomalies` restricted `select(AnomalyEvent)` with a condition over `RequestLog` —
  the trace view's block, pasted onto a different statement. SQLAlchemy resolved the foreign
  columns by adding the table to the FROM clause with no join predicate, so it rendered as
  `FROM anomaly_events, request_logs`: a cartesian product **and** a filter that asked about
  unrelated rows, so the per-user restriction did not apply to a single finding. Two failures in
  one line, pointing opposite ways.

**Three were a rule written twice that had drifted on one copy.** `payloads.py` compared
`use_case_members.subject` — which holds a *username* — against `principal.subject`, which for an
OIDC token is a directory id, so no console user was ever recognised as an administrator of their
own use case. `api/usage.py` and `ki_usage` asked `is_governance` while their docstring and their
own refusal message said *oversight*, so IT Security was refused every per-use-case figure — the
correction `visible_scope` documents making on 2026-08-08, on two call sites that were not carried
with it. And `persistence/redaction.py` carried a private copy of `is_catastrophic` that had lost
the `{n,m}` form and alternation.

**Two were a fact applied at one `return` out of four.** `FRD-309`'s notice reached Gemini's
`:generateContent` and neither stream nor the KIRA surface — a `pii_filter` rewrote callers'
prompts and told three quarters of them nothing. Fixing it surfaced a third: `with_notices` refused
an empty answer and a tool call and called that "text only", while its own docstring said the case
it exists for is a `responseSchema` **document** — which is non-empty text with no tool call, so
the sentence went in front of it and the document stopped parsing. The check needs a fact about the
*request*, which the function was never handed. And an unexpected exception on the KIRA surface
answered in the AIRA envelope: `_kira()` was defined four lines above `_handle_unexpected` and used
by four of the five handlers.

**Two were measured rather than argued.** One served request opened **15 database sessions**
against an empty read-model, five of them the same `ModelCatalog.declaration()`; and *every*
authenticated Management request ran **17 statements, 8 of them writes**, in the steady state where
nothing had changed — a read path that writes, which rules out a read replica and takes row locks
on `auth_user_groups` for a `GET`. Now 10 and 2/0, through a memo with a **request's** lifetime
(never the application's: the catalog decides what a request may ask for) and a reconcile that
writes only what differs.

**One was two lines of nginx.** `proxy_buffering` is on by default, so both SSE verbs arrived as a
single lump through the console's own proxy — exactly what `streaming_chat` documents having fixed
*inside* the gateway, reintroduced one layer out where no gateway test can see it. Proved both
ways against the real image: 0.0/0.4/0.8/1.2 s with it off, all four chunks at 1.61 s with it on.
Its `proxy_read_timeout 120s` also sat below the gateway's own 300 s for a cold self-deployed
model, so the proxy gave up first and the reader got somebody else's 504.

The rest: a budget counter created by a *refused* reservation never got its `EXPIRE`, so the one
key that most needs rebuilding from Postgres was the one that never was; `AnomalyService`'s
cooldown map was unbounded two lines below the one that is explicitly bounded, with the same
argument written on it; `AIRA_DEMO_MODE` waived **every** deployment check at once rather than the
ones a demo needs, so one variable turned a production gateway into an open port with a published
key; `/readyz` handed the topology and the loaded secret names to any credential, including the
weakest one this system issues; the gateway did not re-apply the pipeline text bounds Management
applies, which is `ADR-0018`'s shape and was closed for regexes only; `OpenAIServer.api_key` was a
parameter nothing set, contradicting `FRD-123` §8 in code; the SPA was served with no security
headers at all; the console could not read the compatibility surface's error envelope, so the
server's own wording was replaced by "Something went wrong"; the dev stack published Postgres,
Redis, Kafka and a dev-mode Vault on every interface; and `source_ip` — the first column an
incident filters on — had no index.

**And one the review found by running what it had changed:** the Angular branch-coverage gate was
already red on `main` at 91.37 % against a threshold of 92. `make ci` had been failing before any
of this. Fixed by covering `describe()` — the sentence an operator reads for every step outcome,
which had ten branches and two tests — never by moving the threshold.

Every fix carries a mutation (`RV1`–`RV11`, plus `P14b`/`P14c`/`H6b`), and all of them were
observed to be caught. Four anchors had to be re-pointed, and two of those — `G2`/`R21` and
`N35` — had become two claims about one line after the syntax they guarded was consolidated;
they were re-aimed at what is still distinct rather than left as duplicates.

---

## Both slow tiers, end to end — and what they found (828 + 142)

Asked to run the integration and browser suites completely. Neither had been run in full this
session, and both had something to say.

**Integration: 828 tests, one failure — and not from this session.**
`test_a_member_budget_binds_only_that_member` asserted `[200, 200, 200]` from three requests by one
caller against an `each_member` budget of 1. It had been failing since the `member` scope was
removed: the test used to configure a budget naming *somebody else*, which genuinely left this
caller alone, and the mechanical rewrite to `each_member` kept the expectation. `each_member` binds
everybody, so the right answer became `[200, 429, 429]` — which is exactly what the test **directly
below it** already asserts. Two tests, one scenario, opposite expectations, and nothing noticed
because nobody ran the tier.

Its property is worth keeping and needs a second person to state, so the fixture can now issue a
key owned by somebody else: this caller spends their allowance, and the other person's first
request is still served. That is `ADR-0019`'s other half at the integration layer — one pot **per
person**, which is not one pot.

**Browser: 8 failures, and those were mine.** Folding the connection block shut hid the contents
seven tests assert; they open it now, through the summary, the way a reader does. The eighth was
`model-release`, which passes alone — collateral, and diagnosed below.

Then two more on the second run, both real:

- **A callout outside the fold is still inside the element.** The "no model is released" message
  had been moved out of `connect__body` and left after `</summary>` — where a shut `<details>`
  hides everything. So the one use case that most needs the explanation showed a folded card and
  nothing else. It sits *before* the card now. The browser test caught it twice: once when the fold
  was added, once when the fix was not far enough.
- **An unqualified `getByRole('tabpanel')` is a query about a page that has one of something.**
  Opening the block puts a second tab strip on the page, so the assertion resolved to two elements
  and named the overview panel. Both call sites ask for the panel by name now.

And one flake of the shape this session already fixed once: `model-release` sampled
`await released.count() + await nothing.count()` immediately after the heading appeared. The
heading renders before the release list arrives, so under the load of a 142-test run it asked
during the gap and got zero — reporting *"the panel says neither"* about a panel that was still
loading. It waits for whichever answer turns up, then checks it is exactly one. **`count()` is a
sample, not a wait** — the third time that has cost something here.

Final: **828 integration passed**, **141 browser passed, 1 skipped** (the hundred-question
catalogue run, skipped deliberately and with its reason).

---

## What this session left undocumented, checked rather than assumed

Asked at the end of the round: *"is everything we did in this session documented?"* Checked
against the discipline in `CLAUDE.md` §4 rather than answered from memory — every commit against
the DEVLOG, then the documents nothing mechanical guards.

**Complete already:** nine commits, nine DEVLOG entries, in order. Every FRD touched carries its
own *as built* section, the generated feature index matches the headers, `ADR-0019` is written and
indexed, and the property count in `CLAUDE.md` is what the harness defines — the four tests that
check those mechanically all pass.

**Four gaps, all in the documents a reader opens rather than the ones a test reads:**

- `REQUEST-LIFECYCLE.md` still said a pipeline step is *"booked against the budget with
  `requests=0` — the caller made one request."* That was true until this session reversed it
  (`FR-9b`). The one document whose whole subject is "one request, every control, in order" was
  describing a control that had changed underneath it.
- `INTEGRATIONS.md` described **two** ways into a use case where `FRD-209` §2.1 has three — the
  third being the grant naming a person, which this session made work. It also said nothing about
  `preferred_username`, which is now what decides whose allowance is whose (`ADR-0019`), and an
  installation whose tokens omit it should know what it gets instead.
- `GAP-ANALYSIS.md`'s budget row named the use-case figures and not the per-person ones.
- `TESTING.md` described **two** tiers where `CLAUDE.md` promises four, and claimed the hermetic
  tier is hermetic. It is, with the stack stopped; with a Redis reachable the **budget counter is
  shared between runs**, which is how a test written this session refused a request it should have
  served. Both are written down now.

**LESSONS** gained one genuinely new rule and three merged instances. The new one is *"a default
argument is a silent one"* — the wire shape's worst variant, because nothing is missing to notice:
`resolve()`'s `direct` argument had tests of its own and two callers passing two arguments. The
merged ones: the closed vocabulary restated four times where **the fourth copy was the test**, and
two more setups that never reached the path they were named after.

---

## One human, one allowance — whichever credential, whichever surface (`ADR-0019`)

Asked, straight after the per-person figures landed: *"if it was that easy to calculate a person's
consumption, why not throw the API key and the Keycloak sign-in into one pot for limits, request
limiting, budgets and so on — then we do not have to worry about double budgets."*

Right, and the code had been waiting for it. `scopes.py` carried the two-pot problem as a *known
limit* and named the fix in the same paragraph: *"a stable identity for a person across credentials
rather than a scope that names one."* `FRD-606` built that identity a day earlier for a different
reason — the name beside the subject, so a display could group one human — and using it for the
decision was the smaller half of the same idea.

`aira_gateway.scopes.person` is the whole rule: the name where the credential carries one, the
subject where it does not. **The fallback is not a formality**: falling back to nothing would put
every nameless caller — a service account, an older realm mapping — into one shared pot, which is
the opposite failure and much worse, since it is the case nobody checks. A mutation that removes it
is caught.

**Two things deliberately unchanged.** `subject` stays what an audit row is about, so `FRD-604`'s
question — who is accountable for this credential — still has its answer. And a **suspension** still
reads the subject and the credential: stopping traffic aims at a person, a credential or a use case,
and folding the first two would make "block this leaked key" stop the human holding it.

**The counters were merged, not abandoned** (`0032_merge_member`). The mapping is *observed* — the
audit row has carried subject and name side by side since `FRD-606` — so a subject that has called
resolves to its name and one that has not is left keying nobody. Where both pots exist for a period
they are summed. Verified on the running stack: `member:coding-assistant:dca4ff6f-…` became
`member:coding-assistant:ucadmin`, carrying its 36 tokens. Without that step everybody who signs in
appears to start the period at zero — under-counting a budget, which is the one direction a budget
must not be wrong.

**The matrix the owner asked for**, `{Gemini, KIRA} × {API key, bearer}`: seven hermetic cases in
`test_one_person_one_allowance.py`, written as one *comparison* rather than four separate
assertions, because a per-combination test passes just as happily against the defect — which is how
it survived this long. Reverting the key to the subject fails four of them; removing the fallback
fails the fifth. Plus one e2e, because only that layer has a token minted by real Keycloak whose
subject genuinely looks nothing like a username: a key spends a request budget of one and the same
person's dry run is refused by it. Shown to fail against the old gateway by rebuilding it.

The console's two warnings said *"an API key and a Keycloak sign-in are two separate budgets for the
same person"*. True when written, false the moment the key changed, and a caveat that has quietly
become wrong is worse than none — somebody sizes a limit around it. They now say the opposite, and
a test forbids the old wording.

A note on the suite: these tests hit a **real Redis** where one is reachable, so the budget counter
outlives the run. A fixed slug made the first assertion depend on how many times the file had been
run — the kind of flake that gets a real finding dismissed. Each test now uses a use case of its own.

`QA49`–`QA51`; `E1` re-anchored.

---

## My own figures on the overview, and 3467 px folded away (`FRD-606` §9)

Two from the owner. *"I want to see my consumption and remaining budget in the overview of the use
case"* — the members tab answered it for everybody and the overview for nobody. The same panel,
narrowed by a name: the arithmetic of a remainder is the part worth not writing twice, and a copy
on the overview is a copy that disagrees with the members tab the first time either is touched.

Half of "remaining budget" has no personal answer, and saying so is the honest version: a
`use_case` budget is one pot the first caller may spend all of, so it reads *"Left of this use
case's shared day budget: 499 of 500 requests — shared with everybody in it"* rather than being
divided by head into an allowance nobody configured.

*"Make the description of connections collapsible, it takes up too much space."* Measured before
touching it: the overview 3849 px, that block **3467** of them — 90% of a page whose job is to say
where a use case stands. It is a reference, not a status. A `<details>`, shut: **3467 → 122 px**,
page 3849 → 2347.

The sentence that answers the question stays **outside** the fold. It is why the panel exists — a
caller hunting for a per-use-case URL that does not exist — and a reader decides whether to open a
block from its summary. My first attempt shortened it to "base URLs and examples", which put the
answer behind the fold that exists to hide the examples; an existing test caught it, and a new one
holds it there.

**Two of my own new tests could not fail**, both found by the harness: one asserted the *wording* of
a line that must not appear, so a mutation rendering the same claim under another label passed it;
the other left the usage map empty, so the line was missing because nothing had been measured rather
than because the scope was wrong — a setup that never reaches the path it is named after, twice in
one file.

---

## A classifier is a request after all — for budgets, not for buckets (`FRD-125` FR-9b)

The owner's answer to the question left open two rounds ago: *"count them in request budgets, rate
limits stay."*

`FRD-125` FR-9 had booked a pipeline step's model call with `requests=0`, and its reasoning was the
consequence: *"could trip a request limit for traffic the caller never sent."* That consequence is
now **accepted**, and the reason is the one the owner gave — the call reaches a model and costs
money, so a use case running two steps per request is doing three times the work its request budget
was sized for, and a budget that cannot see that is sized against something that is not happening.

Accepted **for budgets only**, which is the interesting half. A rate limit still counts arrivals:
the gate is taken once, before the pipeline, on the one request that arrived. Slowing a caller for
calls the gateway made on their behalf would throttle precisely the traffic they did send — FR-9's
argument, still true where it applies. Both halves are pinned, in both directions, because the two
rules now differ deliberately and a reader who finds one would reasonably "fix" the other.

**And this reverses my own change from two rounds ago.** I had narrowed the report to the caller's
own requests, because the report and the budgets disagreed and the report was the one out of step.
The rule it was measured against has changed, so the fix goes with it: both sides count every model
call now. What survived unchanged is the property both reversals were actually about — the budget
bar and the request figure beside it must not mean two different things. `QA45` was re-aimed rather
than re-anchored: it guards the booking now, since `requests=0` is the value a half-undone reversal
would silently go back to.

FR-9 is struck through in the FRD rather than edited away, with FR-9b beside it: what the rule was,
what it is, and that the warning was accepted rather than overlooked.

Verified live: one dry run with an LLM filter, and the counter for that day reads `requests 1,
tokens 36` where it would have read zero requests. The rate-limit half was already visible in an
earlier measurement — a use case limited to one per minute served the first dry run and refused the
second, which it could not have done if the classifier had taken a token of its own.

`QA45` (re-aimed), `QA48`.

---

## What one person used, and the column that made it answerable (`FRD-606`)

Asked: *"in reporting I am missing a display of how much money was used up — at the moment it is
only the requests. And an overview of what a person in the use case has used up, in tokens and in
money, for both the API key and the Keycloak sign-in, even when there is no budget limit; where
there is one, how much is left."*

Two of the three needed no new figures at all, which is `FRD-603`'s finding one level down:
`BudgetUsage` already carries the period's tokens, requests **and** money, and the budget card
rendered only the metric that budget happened to limit. A request budget answered "how much money"
with silence while holding the answer.

**The third needed a column.** A subject is not the same alphabet for the two credentials — an
OIDC token's is the directory's user id, an API key's is its owner's username — so one person was
two rows and nothing could join them. `Attribution` had carried the name all along, with a
docstring saying it is *never* written to the row, because a name can be reassigned and a subject
cannot. That reasoning holds, so the name is added as a **descriptive** column: `by_member` still
groups by subject, which is what every counter and every budget key uses; `by_person` groups by the
name. Rows written before the column stand alone under their subject — the join genuinely was not
recorded, and inventing the name a subject *probably* had is the one thing an audit row must not do.

Verified on the live stack with one person calling both ways: a key request and a console dry run,
landing in one row — `ucadmin · signed in: 0.0001 / 0 req · API key: 0.0002 / 14 req`.

**Three things the running stack found that the tests had not.**

- A half with money and **no requests** was hidden: the panel asked `requests > 0` to decide
  whether a credential had called, and a pipeline step's model call is recorded with no request
  against it (`FRD-125` FR-9). Somebody whose month went entirely through a classifier had a half
  with real spend and no line saying so.
- Two decimals hid the whole answer: an allowance of `0.01` against a spend of `0.0003` read
  `0.01 of 0.01`. The remainder is computed in nano-units and rendered in the significant precision
  of the figures beside it, with the limit's storage zeros trimmed first.
- `.table th` shouts, which is right for a column heading and wrong for a person's name.

**And the harness found the wire in my own change.** Deleting `username=username` from the write
survived every test: the column existed, the grouping used it, the panel rendered it, and nothing
checked the one step that fills it. Two correct halves and no wire — the shape I have spent four
rounds finding in other people's code. Covered now at both layers, attribution → pending row and
pending row → database.

`QA46`, `QA47`.

---

## Two clocks, and the one nobody wound (`FRD-404`, `FRD-601`)

Asked: *"check the same for reporting and retention."* Two findings, and both are a rule stated
once and applied on one of the two paths that need it.

**A deleted use case's prompts were kept for ever.** Retention builds a period per use case from
the `use_cases` read-model and makes one more pass for rows carrying no use case. A row whose slug
is *neither* matched neither pass — and that is exactly what deleting a use case produces.
`_delete_usecase` keeps `request_logs` on purpose and says *"their payloads still expire on the
retention clock."* They did not. Measured on the running stack: **1509 rows** holding stored prompts
for use cases that no longer existed. After the fix, one pass cleared all 1509 and left the metadata
standing — verified live, 1509 → 0.

The unknown-slug pass follows the installation default rather than clearing on sight, because Kafka
orders the use-case topic against nothing: a use case whose row has not arrived yet looks exactly
like one that was deleted, and clearing on sight would strip payloads a second old. Both edges are
tested, and the older mutation on that call reported **STALE** rather than green when the signature
changed — `N2` working as built.

**A classifier was counted as a request.** `FRD-125` FR-9 books a pipeline step's model call with
`requests=0` and gives the reason in the requirement: *"the caller made one request and counting the
classifier as a second would inflate every request figure."* The budgets honoured it; the report
counted rows. On this stack one use case showed **6 requests where 3 were made**, and another showed
**1 where the caller made none** — that row was a dry run's classifier call. The row's own comment
states the intent it was missing: named for the step *"so the reporting breakdown separates what the
use case asked from what governing it cost."*

Only the count narrows; tokens and money still sum every row, because those rows exist so the cost
of governing a use case is visible. The prefix is one constant now, read by the writer and the
reader. All four breakdowns share one measure list, so a fix landing in the totals and missing the
groups would have made a screen disagree with itself — asserted for all four.

**What the audit did not find**, recorded as checked: payloads live in `request_logs` and nowhere
else (attachments are forwarded, not stored; `payload_access` holds no content; a step's row never
carries the prompt); CSV is a *rendering of the same result* rather than a second endpoint, so the
visibility rule cannot be forgotten on one of them; `visible_scope` is applied once, through the
window helper every breakdown uses; the export dropdown offers the outcome breakdown and, when it
is chosen, replaces the button with a sentence rather than answering 400; and the report already
distinguishes *unpriced* from *refused*, which an earlier round had to fix.

One bounded behaviour stated rather than changed: `anomaly_events`, `payload_access` and
`budget_usage` have no clock of their own. None holds caller content, but they grow without bound,
and an installation choosing a record-retention horizon should know it does not reach them.

`QA43`–`QA45`, each shown to fail first.

---

## A closed vocabulary, restated four times, wrong in both directions (`FRD-500`)

Asked: *"check the same for anomaly rules and incidents."* The same question again — which paths
does the rule reach — and this time the answer was in the console rather than the gateway.

`aira_common.anomalies` defines seven rule kinds and calls itself closed. Django derives its
choices from the enum and cannot drift. The console cannot import Python, so it restates the list,
and the list had drifted **both ways at once**: it offered `token_spike`, which does not exist, and
omitted `blocked_prompt_rate`, which does.

Measured in the browser before changing anything: picking *"Token use jumped against the previous
window"* and pressing **Create rule** answers `kind: "token_spike" is not a valid choice.` And
`blocked_prompt_rate` — measured by the gateway, seeded by the showcase, **listed on the very
screen** — could not be created from the form on that screen. The one kind that reports the
injection filter earning its keep was unreachable.

**Four copies, and the fourth was the guard.** The dropdown, the units table and the sentence
writer all carried the drift; the fourth was a test named *"every kind has words"* iterating a
hand-written list with the same ghost and the same omission. It asserted completeness against a
list that was itself incomplete — a guard agreeing with the thing it guards, which is a shape this
repository has named often and had not yet seen in a *test's own fixture*.

The comparison now lives in the one language that can read both sides, and runs in both directions
over kinds, targets and actions. One direction would have caught `token_spike` and left
`blocked_prompt_rate` missing for as long as nobody asked. A second guard closes the other end:
every kind in the vocabulary has a branch in `evaluate_rule`, and each evaluates against a real
schema — because that function's final `return []` for an unknown kind is deliberate forward
compatibility, and it makes a same-version gap look exactly like a rule that found nothing.

Verified live afterwards: the corrected dropdown creates a `blocked_prompt_rate` rule, no error,
and the row arrives in the gateway's read model over Kafka.

**What the audit did not find**, recorded as checked: all thirteen rule fields on both planes, in
the payload and in the consumer; `upserted`/`deleted` emitted, routed and applied; all three
suspension targets matched and all three actions handled; a throttle without an rpm refused by both
planes rather than created and ignored; suspensions expired on read rather than by a sweeper; and
incidents needing no read model at all, because the console posts them to the gateway's own API —
there is no second copy to diverge. Two bounded behaviours stated rather than changed: the
touched-scope cap (4096 per tick) and the in-process cooldown.

`QA41`, `QA42`, each shown to fail first.

---

## The dry run had the permission controls and not the spending ones (`FRD-401`, `FRD-405`, `FRD-503`)

Asked: *"can you check the same for budgets and rate limits?"* — the same question as the access
round, of a different rule: **which paths does it actually reach.**

`pipeline:dryRun` was rewritten once already, because a caller could post a pipeline naming any
model and have the gateway call it — its docstring lists the finding as *"no use case, no release
check, no approval check, no budget, no rate limit, and no audit row."* The rewrite restored
authorisation, the release check and the audit row. **It did not restore the other two**, and the
split is not random: the two it fixed are about *permission*, the two it left are about *spending*.
The docstring went on listing all four while the code implemented half.

Measured by removing the fix again — a use case with `limit_requests: 0`, a rate limit of one per
minute, and an outright suspension by IT Security each let a dry run through and call a model.
Audited and billed, so visible after the fact and stopped by nothing.

`guard_before_work` is taken before the engine runs: one call, not three, because the bundle exists
so an order is not a call site's to assemble (`FRD-126`). Live afterwards: first dry run served,
second and third *"Request rate limit exceeded for use case."* The first attempt at that
measurement passed twice and was **my probe, not the fix** — the limit had been created five
seconds earlier and the gateway's config cache had not expired.

The guard is structural too. `test_every_spender_takes_the_gate.py` reads every module under `api/`
and fails one that reaches a provider without taking the gate — over the **category**, not over the
file that was wrong, with three exemptions named and reasoned. Removing the fix makes it print
`pipeline.py` on its own.

**What the audit did not find**, checked in the same pass and worth recording as checked: budgets
and rate limits carry the same fields on both planes; `upserted` and `deleted` are emitted, routed
and applied for both, already guarded in both directions by `test_outbox_routing.py`; both scopes
are evaluated together on every verb, because the gate is taken once before the verb branch; and
`each_member` needs no membership list at all — the caller is the key — so it behaves identically
whether somebody is a member by group or by name. The one known limit stands and is stated in the
console: a person's API key and their Keycloak sign-in are two per-head allowances, because the two
credentials answer "who is this" in different alphabets.

`QA40`, shown to fail first.

---

## A grant naming a person was specified, replicated, and read by nobody (`FRD-209` FR-6)

Asked, after the previous round: *"I want to add any group from Keycloak and any user as well, and
give them admin or user rights — I do not want to have to make a separate group named after the use
case. Is that how it is, or did I misunderstand you?"*

Not a misunderstanding — the question found a defect, and my previous answer had described the
symptom as the design. `FRD-209` §2.1 and FR-6 say a caller's use cases are **the union of** the
`/use-cases/<slug>` groups their token carries, the group grants matching a group they carry, and
**the user grants naming them**. Two thirds worked.

The third existed everywhere except where it counts: `resolve()` has taken a `direct` argument since
the vocabulary was written, with its own tests; Management emits `membership.upserted`, the outbox
routes it, the consumer applies it, and `use_case_members` in the **gateway's** database held
correct rows for every demo use case. Both resolvers called `resolve(held, grants)` — no third
argument, on either plane. So a person added by name was a member in the console, a member in the
gateway's own database, and refused by the gateway.

**A default argument is a silent one.** `FRD-209` had already been through this shape twice (§8.1's
event with no topic, §8.2's), and both times there was a missing entry to notice. Here there was
nothing missing — only an optional parameter nobody passed.

`_with_group_grants` compounded it by returning early on `not principal.groups`, so the caller this
route exists for left before the lookup. Fixing the resolver alone would have changed nothing.

Both planes pass `direct` now and `may_call` agrees with the gateway. Verified live: `ucuser`, added
**by name** to a use case created a minute earlier and holding no group that reaches it, runs a dry
run — `Dispatched to qwen3:0.6b`.

Two of my own tests could not fail, both found by the harness rather than by reading them: the
resolver tests call the resolver directly, so restoring the early return in `_with_group_grants`
broke nothing; and the stale-read test built a fresh resolver against a broken database, so it could
not tell an emptied member cache from one that was never filled. Both rewritten to reach their own
path — the same trap as the previous round's checkbox that was asserted through its signal and
absent from the template.

`N57`–`N59`, each shown to fail first.

---

## The showcase's members were rows the gateway could not read (`FRD-209`, `ADR-0007`)

Reported: *"I just tested with the use-case admin and the global admin on the Coding use case — the
dry run works for neither."* Measured before changing anything, and every part of it was true:

- `usecases_usecasemembership` held three rows for `coding-assistant`;
- the gateway's `use_case_groups` held **none** for it, and the demo realm had no
  `/use-cases/coding-assistant` group at all;
- so the console — which asks Management's `is_member` — showed all three as members, and the
  gateway, which reads the Keycloak groups in a token, refused every one of them.

`personalwesen` carried the second version of it: the group existed and **nobody was in it**, while
the seed named `admin` as its administrator. Neither had ever worked from the console for anybody.

**The seed's `MEMBERSHIPS` and the realm's `users[].groups` are one list written twice**, and
nothing compared them — this repository's most repeated shape. A test compares them now, in both
directions: everybody the seed makes a member must be reachable from their token, and a
`/use-cases/<slug>` group must name a use case the showcase creates. Breaking the realm entry
reproduces the reported defect as a red test.

**The realm reconciler could not have carried the fix either.** It checked that every user and
every group in the file exists, and never that anybody is *in* one — a realm with all the users,
all the groups and nobody in them satisfied it and serves nothing. It compares memberships now.

**And the console offered the button.** `is_member` counts a database row and grants a global
administrator everything; `may_call_queryset` — the gateway's own rule, already used by the
smoke-test screen — is a third answer, and the use-case API now reports it as `may_call`. The
builder says, before the button, that the gateway will refuse and why. The button stays enabled:
this is the console's reading of a rule the gateway owns, and a disabled control that is wrong
about it could not be argued with.

Verified live afterwards: global admin and use-case admin both dry-run `coding-assistant`
(dispatched to `qwen3:0.6b`), and a global admin on `kundenservice` — visible to them, not
callable — sees the warning before pressing anything.

Two harness traps on the way, both the same one: a spec that passed with `may_call ?? true` written
in the *mock*, so `undefined` never reached the component and the "no opinion" case tested its own
setup; and — the previous round — a checkbox asserted through its signal while absent from the
template.

---

## A refused dry run still has to say what it found (`FRD-309`)

Reported: *"when I start a dry run and it was rejected, the warning or error doesn't go away, and I
can't see the result of my dry run for each step — I would like to see it, because then I can check
compatibility for my use case."* Reproduced both halves in the browser before changing anything.

**The message outlived its subject.** It stayed until the next run, so reading it, fixing the step
it named and looking again still showed the old complaint. Bound to the same subject as the trace
now — configuration, sample text, keep-going option — in a **separate** signal, because one signal
for both stamped the new configuration onto the old trace and marked a stale result fresh. That
second bug never reached the screen; it fell out of writing the first fix down.

**A block hid every step behind it.** The remaining configured steps are cards marked *not reached*,
built from the configuration as it was **when the run was made**.

**And they can now be run.** `past_blocks` keeps evaluating past a refusal — opt-in, because the
default answer has to be production's answer and each such step spends real tokens on a call the
served path never makes. Marked `after_block` on the wire and badged *would not run — refused above*
on screen. Verified live: a heuristic filter blocks, the routing step behind it runs, is dashed,
badged, and shows the classifier's reply.

**The checkbox was written, tested, and not in the template.** The component tests set the signal
directly, so they passed over a setting reachable from code and from nowhere a person could click —
found by the browser probe, not by the suite. The test goes through the control now, and fails when
the control is missing.

Fixed alongside, and it is the same shape: the **issue-key window** said `.form-inline` and laid out
as three lines regardless, each field as wide as its own hint — inputs 838, 641 and 461px, a ragged
right edge. It is a `stack`, which is what a form in a window with a sentence under every field
actually is. That was the pre-existing alignment failure reported in the previous entry.

And a **45-second timeout on a button that was on screen the whole time**: `submitOfOpenForm`
branched on `await locator.count()`, a single immediate poll taken the moment the opening click
returned. On a slow render it saw zero, fell back to the page-level selector, and waited for
something that cannot exist — a window's submit button sits in the modal footer with `form="…"`,
outside any `<form>`. Warm, the modal won the race every time; restarting the backend first
reproduced it on demand. It waits for whichever appears now.

`QA37`-`QA39`, each shown to fail first, and each half of the new wire broken separately.

---

## The builder shows what the models said, and nothing scrolls inside it (`FRD-309`)

Reported after the PII step shipped: the graph is short, the inspector on the right grows long, the
test area ends up at the very bottom — **no scrollable areas** — and the dry run should show step by
step what the models put out.

**Measured before touching it.** With a routing step selected: graph 478 px, inspector 688 px, test
panel starting at **y=1232 in a 720 px viewport**. The left column held 210 px of dead space while
the panel that says whether the configuration works sat half a screen under the fold. It lives in
that space now (panel top **675**, page 1407 → 1269), and the inspector's `position: sticky` +
`overflow-y: auto` is gone — a scroll container inside a document that already scrolls gives a
reader two scrollbars and one of them appears only sometimes. Its height cap had its own failure: a
sticky element taller than the viewport pins its top and leaves its lower half unreachable, which is
what the routing step's default-model field used to do with enough categories. What made the sticky
panel look necessary was the empty left column. The e2e guard that asserted `overflow-y: auto`
**asserted the defect as a requirement**; it now asserts that nothing inside the builder scrolls on
its own, over every element rather than the inspector by name.

**Two `<fieldset>`s came out of it, and they are the read-only rule.** A reader who cannot manage a
pipeline may still run it — the callout says so — and `<fieldset disabled>` makes every descendant
inert with no way to exempt one, so the grid-wide guard took the dry run away from exactly the
people the read-only view is for. Caught by the existing reader test, which now checks **every**
fieldset: with one hard-coded binding, checking the first would pass.

**The trace explains itself.** It rendered `[blocked] injection_filter` per entry — what happened,
never why, and for all three LLM-backed steps the why is a model's own answer that nothing carried.
Each step is a card now: the model that was **asked** (never the one routed *to* — the routed model
is already on the same card, and borrowing that name makes the router's decision read as the
answering model's), what it replied verbatim, and for the redactor the caller's sentence before and
after. Live: an LLM filter against the mock returned `undetermined` and the panel showed the reply
that explains it — which is the whole feature, since neither word, both words, an empty reply and a
refused call are one verdict and four different repairs.

**Shown, never stored.** `FRD-122` §5.3 keeps a classifier's prose off the audit row through an
allow-list. The reply travels in the trace entry's `detail`, which is a screen; `decision` is
unchanged, and a test asserts both halves of that at once.

**A trace that outlives its configuration is a confident statement about the wrong thing** — the
failure this panel exists to prevent. It is labelled out of date rather than cleared, and the
browser-side live preview returns, being then the only thing describing the pipeline as it stands.
The comparison includes the **sample text**: a verdict about one sentence sitting under another is
just as stale.

One e2e assertion was **passing for the wrong reason**: `expect('.callout--danger').not.toContainText(
'AIRA_OIDC_ENABLED')` matched the *block reason*, which happened to be styled as a danger callout.
With the refusal now a card, that locator matches nothing on a healthy run and
`not.toContainText` **fails** against an absent element — which is how it announced itself.

Also: the "nothing released" callout moved **above** the builder. It explains why every model
dropdown is empty and why a dry run will refuse, and a reader who meets it after scrolling past both
columns has already drawn their own conclusion.

`QA34`–`QA36`, each shown to fail first. **Pre-existing and reported, not touched**: the e2e
alignment guard fails on the *issue-key* window with "nothing with two controls on a line was found
to compare" — the form declares `.form-inline` and lays out one field per line (the first carries
`.grow`). Confirmed on `HEAD` by rebuilding the frontend from a stash.

---

## A step that rewrites the prompt, and the two firsts it needed (`FRD-309`)

Asked for: an LLM-based PII replacer — a trusted model from the use case's released list, an
instruction saying what to remove, and a disclaimer on the answer in the operator's own words.
Asked first whether the pipeline was modular enough. **For the two steps it had, yes; for this,
no**, in three specific ways: `run` and `dry_run` each carried an `if/elif` chain over the step
types, a step could change the *model* but never the *content*, and nothing in the pipeline could
touch the response.

**The refactor came first, and it paid immediately.** A step is one function returning a
`StepEvaluation` now; the two loops interpret it. That exposed a divergence nothing had noticed: a
router whose classifier could not be reached fell through to the configured `default_model` in
`run` and reported *unchanged* in `dry_run` — the builder's preview named one model while
production used another, on the one screen whose whole job is to say what the pipeline will do.
Two hand-written copies of one rule, and the difference was invisible until something compared
them.

**Then the defect that only a database row could show.** The step worked end to end on the first
live run — model sent the redacted prompt, notice on the answer — and `request_logs` held the
original, in full. The payload written there is the *wire body* captured at the surface; the
pipeline rewrites the *canonical* request. So the redaction protected the model and not the
database, which is the one thing the accepted design said it must do. Underneath it: the body was
**both** a parameter of `accounting`, passed by nine call sites, and `trail.body` — two places
holding one fact, which stayed harmless exactly as long as nothing changed it. One now.

Three rules the feature is built on:

- **A rewrite that cannot be trusted is a failure, not a redaction.** Empty, a preamble, a summary
  — each is a plausible answer that *applied* sends the model a different question than the caller
  asked, with a 200. And this step has no lesser version of itself, so a failure **blocks** by
  default (`FRD-125`), with `allow` recorded as the choice it is.
- **The decision says it redacted, never what** — and no count, because the placeholder shape is
  whatever the operator's instruction asks for, so counting would mean dictating it.
- **The notice goes in front of plain text only.** A `responseSchema` document with a sentence
  before it is unparseable, and a tool call has no text at all — the answer *is* the call. Both
  cases are recorded (`withheld`) rather than passed over, since "no notice shown" and "nothing was
  redacted" are different facts an answer alone cannot separate.

Measured against `qwen3:0.6b`, which replaced one name and left another: **the control is exactly
as good as the model behind it**, which is why the field says *trusted model*. Same finding
`FRD-125` recorded for the LLM injection filter on the same model.

**And then the same thing one step over**, asked immediately after: can routing say which model the
classification chose? It is the same notice machinery, with one addition — the sentence has to name
things the operator cannot know while writing it, so `{category}` and `{model}` are substituted.
**Explicitly, not with `str.format`**: the template comes out of a text box, and a stray brace — a
JSON example, an unclosed placeholder — makes `format` raise. A notice that crashes the request it
describes is worse than one that prints a brace, and an unknown placeholder is left standing so a
typo reads as one instead of vanishing. A notice is given only where a category actually matched:
naming one the router did not choose would be a confident statement about a decision never taken.

**Measuring that found an older gap.** A live request whose classifier matched no category left an
**empty decision list** — identical to a row where no router was configured at all. That is the hole
`FRD-125` closed for the filter ("ran and passed" is not "no filter") and `J17` for "could not be
asked"; the third case, *asked and matched nothing*, still recorded nothing. It does now, and it is
kept apart from *matched, model unchanged* — those send a reader to different places: one is a
working router whose category maps to the model already in use, the other is a classifier or a
category list to look at.

**Then the objection that was right: testing each step alone is not testing the pipeline.** A step
is a function; a pipeline is an ordered sequence in which each one sees what the last left, and
almost everything interesting lives in that seam. 38 cases now, parametrised over **orders** rather
than written per step:

- **Order is a decision, not a preference.** The routing classifier is a model call like any other:
  it reads the prompt. Redact *before* it and the personal data never reaches the second model;
  redact *after* and it already has. Both are legitimate configurations — the classifier may be the
  same trusted model — so both are asserted, and what must not happen is the two behaving alike.
- A block stops the steps behind it and keeps what the ones in front recorded; every step that ran
  reports what it spent, including when a later one refuses.
- Notices accumulate in the order they happened to the caller; a repeated step is a chain, and the
  second one owing no notice because it changed nothing is asserted rather than assumed.
- **The rewrite survives a fallback hop.** A chain re-dispatches *the request*, so a redaction
  living anywhere else would work until an upstream had a bad minute.
- And `run` against `dry_run` across **all fifteen** combinations of one, two and three steps —
  the property the single dispatch table exists for, now checked instead of hoped for.

**Two of the six injections used to prove them were wrong before the tests were.** Reintroducing the
old `run`/`dry_run` divergence produced *zero* red tests — because the edit sat one line below the
statement it meant to break, so it changed nothing. Aimed properly it fails eleven. Second time in
this session that an injection, not the code, was the thing at fault; the tell is the same both
times — a suspiciously clean result from an edit nobody re-read.

The step type now exists in three places — the gateway runs it, Management validates it, the
console offers it — so the three lists are compared **in both directions**, each shown to fail
alone. `P10`–`P14`; 411 mutation properties.

---

## The scope that named one person is gone

Owner's decision, in both places it existed — budgets and rate limits: *"take the restriction to one
person out entirely; it does not look tolerant or democratic. Just the whole use case, or everybody
in it."* What is left says what an administrator actually needs — a **shared pot**, where the first
caller to arrive can spend it, or **a fair share per head**, which needs no names and keeps applying
to whoever joins.

**The rows go with it, and that is the part worth arguing.** `Scope.applying` no longer resolves
`member`, so a surviving row would be **enforced by nothing and visible in nothing** — it sits in
the table, matches no caller, and the console has no option that could show it. This project has a
name for that shape. So both planes delete: Management in a migration, which is where the decision
lives, and the gateway in its own, so an installation whose relay has not run is not left enforcing
something nobody can see. The gateway also drops the counters those budgets accumulated: a
`budget_usage` row keyed to a scope nothing resolves can never be read again and would outlive
every retention clock this system has.

**Deleted rather than widened to `each_member`.** A cap somebody set for one person is not a cap
for everybody, and converting would invent a governance decision nobody made. Measured before and
after: 14 rows carried it, three of them in real demo use cases; afterwards, none in either plane
and no orphaned counters.

The removal took two parameters with it. `subject` and `caller_username` existed **only** for that
scope — the second because a rule typed as a name had to match either of the two alphabets a
credential answers "who is this" in — and with it gone they were read by nothing, threaded through
two services and four call sites. A parameter nothing reads is a rule the code appears to have and
does not, so they are gone too.

**What that loses is written down rather than lost with the tests.** The named scope was the only
place those two alphabets were ever reconciled, so one person using both a browser and an API key
now has two per-head allowances instead of one. That was already true of `each_member` — it has
always keyed on the caller — but it was covered by a test of the named scope, and deleting the test
would have deleted the knowledge. It is in `aira_gateway.scopes` and `FRD-400` §2.2 now, with the
honest fix named: a stable identity for a person across credentials, not a scope that names one.

**And the question that followed exposed what the removal costs, measured.** Asked whether a
person's keys and their Keycloak sign-in share a pot: a limit of one per head on the live stack
answers it. Two keys owned by one person → the second is refused, **one allowance** (a key's subject
*is* its owner's name, so every key they own counts to the same place). The same person's bearer
token → served, **a second allowance**. Nothing reconciles the two alphabets since the named scope
went.

Not fixed — keying counters on a name instead of a subject would move a renamed person's history,
and `budget_usage` stores that shape — but **warned about, on the screen where the figure is
typed**. A number that is wrong by a factor of two for anybody running an agent with a key while
also working in a browser is not a footnote, and the form gave the reader no way to know. The
warning appears when the per-head scope is chosen, on budgets and on rate limits, because the
property is identical and a warning on one of them would leave the other silently wrong.

**Reported straight back: "where do I find these notes? there is nothing in budgets or rate
limits."** True — it lived in the creation window, so anybody *reading* the configuration never met
it, which is most of the time anybody spends on those tabs. **A warning nobody meets is a warning
that was not given.** It is on the tab as well now, wherever such a row already exists, rendered
from **one** definition through an `ng-template` so the two places cannot drift.

**Then: too strong.** It was written as a warning and read as one — nothing here is broken, a
reader simply has to know how the figure is counted, and a callout that sounds like an alarm about
a working system is the wrong kind of accurate. Two sentences now, and a plain callout rather than
a warning one: *"Counted per credential. An API key and a Keycloak sign-in are two separate budgets
for the same person; all of their keys share one. A budget for the whole use case still bounds
both."*

**And the second half of that report was the more important one**: *global budgets and global rate
limits come before the personal limit*. Measured — a use-case cap of four requests, exhausted, and
then **both** credentials refused, key and bearer alike. So the doubling is of the *per-head*
allowance and not of what the use case can spend, and a reader told only the first half concludes
the governance is broken by a factor of two. The warning says both halves now.

The browser test was first written against the showcase's seeded limit and passed for the wrong
reason — the seed's per-head row is on a different use case, and after the scope change it had not
been re-seeded. It creates its own row now: three times in two weeks a test in this suite has been
caught asserting inventory rather than behaviour.

Everything else followed: the option, the field and its suggestions, the validation, the labels,
three mutation properties (`S1`, `S10`, `S11` — a mutation kept against a deleted rule reports green
about nothing), the showcase seed's axes, and the tests that used `member` merely as a convenient
second scope, which were rewritten on `each_member` because the property they carry — FR-4's
all-or-nothing decision across scopes — is untouched.

---

## Two buttons at zero, and a name nobody could look up

Both reported while adding a rate limit, and both larger than the screen they were noticed on.

**"Save and Cancel are too close together" — they were at 0 px, in every window.**
`<ng-content select="[modal-foot]">` projects **one** element, the caller's `<div modal-foot>`, so
`.modal__foot` had a single flex item: the `gap` declared on it applied to the wrapper and never
reached the buttons, which sat in a plain block touching each other. Measured before it was
touched. `display: contents` on the wrapper hands the buttons to the footer as flex items, so the
alignment, wrapping and gap that were always declared there are the ones that act — and the gap is
wider than the console's ordinary inline one, because these two are **opposite decisions**:
mis-clicking Cancel loses what somebody typed and mis-clicking the primary commits it.

**"One named person" was a bare text box.** The reader had to know the username, spelled exactly,
with nothing on the page to check it against. It offers this use case's people now — and still
accepts anything typed, which is the part worth stating: a rule names a *subject*, and access can
come through a Keycloak group, so somebody granted that way belongs to **no membership row at all**
(`FRD-209`). A picker would be narrower than the rule it fills in, which is the conclusion
`FRD-604` already reached for a key's owner and wrote down as a deviation. Hence a `datalist`:
suggests, never restricts.

The same field exists on **budgets**, with the same gap, so it was fixed there too — the report was
about one tab and the defect was about a shape.

The footer test asserts a **minimum distance** rather than a CSS rule, and walks three windows
rather than the reported one: naming only the rate-limit window would have gone green the day
somebody fixed that one by hand. Both properties broken and rebuilt.

**And the immediate objection to the suggestions was the right one**: *"now I can write any rubbish
into the restriction and cover no member at all."* Exactly — and refusing what is not in the list
still is not the answer, for the reason above. What was missing is that the console said **nothing**
either way, so a typo produced a rule binding nobody, saving cleanly and sitting in the list looking
exactly like a working one. That is this project's most repeated defect wearing a feature's clothes:
configured, displayed as active, applying to nothing.

It says what it **knows** now, at the two moments that matter — while the name is being typed, and
on the saved rule, which is where a typo from last week is actually found. Careful to say *knows*:
who is in a group is the identity provider's answer, which is why this cannot be an error and must
not be silence. The same wording the access panel already uses for a grant that reaches nobody, and
the same treatment on budgets, which carry the identical field.

---

## The last creator that was not a window

Reported: *"Issue key in the use-case overview is not in a window, not consistent with the other
elements in the UI."* Correct — it was a form that unfolded inside the panel, while budgets, rate
limits, anomaly rules, global rules and model declarations all open one. The reader learns the
pattern four times and meets the exception on the fifth, which is worse than either pattern used
consistently.

`core/ui/modal.ts` owns the three promises a hand-rolled panel forgets one of — Escape closes, the
keyboard moves in, the backdrop closes — so the form moved into it, the trigger stopped being a
toggle (a window has its own Cancel), and *Issue* became the window's primary action.

**The guard is about behaviour, not about the component.** The model catalog hand-rolled two
windows before `app-modal` existed and they *are* windows: `role="dialog"`, a backdrop, their own
Escape. Requiring the shared control would fail them for being early rather than for being wrong.

Two things came out of writing it. It first matched creators by **testid**, went green against the
old template, and would have gone green forever — the reported button had no testid at all. Matching
the **label** (`+ Something`, this console's convention) is what sees it, and that is also what a
reader recognises. Widened, it immediately found a third control, `+ Add category` in the pipeline
builder — which turned out to be a genuinely different thing: it appends an empty **row to a list
already being edited in place**, where a window would take the reader away from the table they are
filling in. Exempted with that reason rather than converted, in the list that is named for it.

Not changed and worth stating: the security page's **kill switch** is still an inline form. It is an
action rather than a creator, so the guard does not match it and the pattern does not obviously
apply — but it is the same shape, and somebody should decide rather than discover it.

---

## The half of the group chain nobody had tested

Asked whether the tests cover assigning a group to a use case, **adding people to that group**, and
then checking their access with both an API key and a Keycloak bearer token. Checked rather than
answered: the bearer half was covered and the other two were not.

What existed used service accounts **already in** a department, written into the realm file. That
cannot show the promise the feature is actually built on — AIRA never writes to a directory, so who
is in a group stays the identity provider's answer, and a grant made today has to reach a person
added tomorrow with nothing changing here. And nothing asserted that such a person can **issue an
API key** at all, although `is_member` counts a group grant deliberately (`FRD-209`) — the key
being what a client actually uses.

Both are now one test that performs the sequence an administrator performs: grant a department that
reaches nobody, put somebody in it, mint a fresh token, call with it, issue a key as that person,
call with the key. Each step is asserted before the next, so a failure names the link that broke.
The **new token** is itself the property — group claims are baked in at issue time, the mirror of
the hermetic rule that leaving a group takes access away *on the next token*.

The second test is the removal, and the two halves are deliberately **not symmetrical**: a bearer
token stops working on the next token, and an API key keeps working, because it is bound to the use
case rather than to the group. Losing the right to issue one is not the same event as the ones
already issued becoming invalid. Written down because the opposite is the intuitive expectation —
an administrator taking somebody out of a department will assume their keys went too — and because
the trail names the key's owner (`FRD-604`), which is what makes that survivable.

The test writes to the realm, which nothing in AIRA does, and removes the group again in a
`finally`: a suite that leaves grants behind in somebody's directory is doing the thing this system
refuses to do. Proved by deleting the group-grant branch of `is_member` and rebuilding Management —
red, then green on restore.

**Running the whole integration suite then found five failures, and one of them was a real defect
of mine from the day before.** Yesterday's blank-embedding rule was written as a **schema**
validator, and a schema violation becomes `VALIDATION_ERROR` — while the contract has a code for
exactly this case, `EMPTY_EMBEDDING_INPUT`, which a migrating client's error handling switches on.
Right behaviour, wrong vocabulary: the compatibility failure this surface exists to prevent, in the
change that was meant to improve compatibility. It lives in the mapper now, raising the contract's
own code. The hermetic test could not see it because it asserted that *some* code was present.

The other four were stale expectations from the deliberate `404 → 422` change, in files I had not
run — *a subset that passes is not a suite that passes*, again, and the integration layer is where
that gets found. Moving the rule then tripped the mutation-anchor guard (`QA12` pointed at the line
that moved), which is that check working as built. And re-reading my own edit caught something no
test would have: the join had picked up `TEXT_PART_SEPARATOR` on the way, the **chat** rule — a
newline between embedding parts would have changed every vector to a number that is nearly the same
and answers a different question.

---

## Half the state kept, half dropped

Reported from the console: open the AI Studio listing, click *Catalogue…*, cancel the editor, open
the picker again — the provider is still selected and **the list never loads**.

`catalogueOffered` closes the window without clearing the provider, and that is deliberate: it
needs it afterwards, to record where the model came from. `openBrowse` then asked the gateway for
the offerings only `if (askable.length === 1 && !browseProvider())` — the single-provider
convenience — so a *remembered* provider skipped the fetch entirely. The select said AI Studio,
there was nothing under it, no error, and no way forward except picking a different provider and
picking back.

The selection surviving is the feature; the list not following it is the bug. It follows now, and
a remembered provider the gateway no longer offers is **forgotten** rather than asked for — the
same half-state one step along, where the select would show a name it cannot resolve.

**Asserted on what was asked of the gateway, not on what is on screen.** An empty listing and a
listing that was never requested render identically, which is exactly why no test had an opinion.
Both halves of the fix were broken in turn: reverting it fails two cases, and dropping only the
forgetting fails one.

---

## One paragraph became a column

Reported from the console: importing a model from AI Studio and clicking *Catalogue…* leaves the
editor "completely broken, everything packed into one row". Measured in the browser rather than
read: the vendor's note came out **67 px wide and 4818 px tall**, and the model-id field beside it
**30 px**.

Nothing was misplaced. The note is a `<p class="callout grow">`, and `.grow` is `flex: 1` — which
is `flex-basis: 0`. **An item with no basis contributes nothing to the wrap calculation**, so it
never moves to a line of its own; it is squeezed instead, and `.grow`'s `min-width: 0` — the
standard fix for a flex item that refuses to shrink below its content — lets that run all the way
to one word per line. A note and a fieldset are full-width things, so they now say so.

The field beside it was the same fault one size down, and the more annoying half: `.grow` means the
*growing* field is the one that collapses, and here that is the model id, the longest value on the
form. It has a minimum now, written with `min()` so a phone is not pushed into a horizontal scroll
by a rule meant to stop a field disappearing on a desktop. **606 px, from 30.**

Only the import path renders those notes, which is why every other screen looked fine — and why
this was reported by somebody using the feature rather than by any suite. The guard is a **ratio,
not an element**: any child of a form much taller than it is wide is text wrapping one word per
line, whatever produced it. Eight to one is loose for an ordinary tall field and nowhere near the
seventy-two this produced. Shown to fail by reverting the rule.

---

## The three declarations the console could not write

Asked whether the Google import carries an embedding model's width. **It does not, and it cannot**
— measured against the live listing: 53 models, 3 of them embedders, and no dimension field
anywhere. `outputTokenLimit: 1` is a token limit; copying it would catalogue a one-dimensional
embedding model. That is `FRD-507`'s rule holding — only what the vendor *states* may be copied —
and the width would otherwise have to come from an embedding call, which `FRD-506` forbids for the
same reason a health check never generates.

Two more answers came out of the same question. **Capability gating already says exactly what it
means**: *"No model could serve this request (qwen3:0.6b: declares no attachment support, so it
cannot read the ['application/pdf'] this request carries)."* — model and reason, on both surfaces,
and since yesterday the two cases differ by status as well (`422` unknown id, `400` known and
incapable). So no new error code: inventing one would extend a closed vocabulary that clients
switch on. **The Gemini surface** answers `400 FAILED_PRECONDITION` with the same sentence, which
is the right vocabulary — operator-fixable, not an outage (`ADR-0012`).

**And the gap underneath all of it.** The API has accepted `thinking`, `embedding` and
`attachments` since they existed, and the console had a field for none of them — it *showed* them
in the opened row as JSON. An administrator could tick "embed" and had nowhere to say how wide the
vectors are; the seed was the only way in, which is why `all-minilm` listed with a batch flag and
no width. `FRD-206` inverted: a capability with no way in announces itself through nothing, because
an absent control reads as a design decision. Measured first — editing a model without those fields
loses nothing, since the API upserts and leaves omitted ones alone — so this was a gap and not a
defect.

All three blocks are in the editor now, each shown only when its capability is ticked, with the
validator's own rules mirrored where a form can hold them: a thinking budget must stay below the
output cap, a default mode must be one of the declared ones, a default width must be one of the
declared widths. Three details are the ones worth keeping:

- **`null`, never `{}`.** Unticking a capability removes the block. An empty object would leave a
  model declared to embed with nothing said about it — and a test that named only two of the three
  stayed green when the third was made to send `{}`, found by breaking it.
- **A per-type token estimate is carried, not rebuilt.** The form has no input for it, so writing
  `media_types` from the checkboxes alone would silently drop a figure somebody measured.
- **Two vocabularies had to be restated in TypeScript**, so both are compared against the Python
  enum and the gateway constant **in both directions** — the capability list was missing two
  members for days on exactly that omission.

**The type caught a fixture describing a shape the server refuses**: `attachments.media_types` was
a *list* in a spec, which the validator rejects, invisible while the field was typed
`Record<string, unknown>`. And the browser test was written asserting the seed's own value — it
passed alone and failed on the second run, because its first attempt had saved a different one and
never reached its restore. It asserts the **round trip** now, and reloads rather than reopening,
which is both steadier and the stronger claim: a reload fetches the catalog again.

Verified end to end on the running stack — console payload → Management → Kafka → gateway
read-model → the compatibility surface's `GET /models`.

---

## The predecessor's own suite, run against this surface

250 of the predecessor's tests against AIRA: 233 compatible, 17 differences. The owner went through
them and kept most — a compatibility surface that copies every behaviour copies the mistakes too,
which `FRD-107` §5.5 has always said. Four were changed and one was a question.

**An unknown `model_id` answers `422`, not `404`.** This was a *documented* deviation: the code
matched and the status did not, on the argument that 404 is what a missing thing gets. Running the
suite turned it into a failure a migrating client would also see, and the argument does not survive
that: a generated HTTP client switches on the status before it reads the body, `404` reads as
"wrong URL", and only `422` sends anybody to look at `model_id`. **A model the gateway does not
*serve* now answers the same way** — that refusal arrives from the shared layer one step later,
and answering it differently would make the status depend on which of two equivalent failures came
first.

**A blank entry in an embedding list is refused rather than absorbed.** A list is *one* embedding
(`FRD-113` §11), so `["ok", ""]` embedded exactly like `["ok"]`: 200, an ordinary-looking vector,
and no way for the caller to learn that one of their chunks was empty — a silent drop in the one
place nobody can notice it. The canonical validator had refused blank texts all along and never saw
one, because this surface **joins before validating**. Whitespace counts.

**`all-minilm` reports its width.** The fields have been in `GET /models` since Stage B and the
*seed* declared none of them, so the model listed with a batch flag and no dimensions — which a
reader takes as "no fixed width" rather than "nobody wrote it down". `FRD-114` FR-7 forbids
inventing it, so it was **measured**: two texts of very different length, 384 values both times.
Task types stay undeclared, because this dialect has none.

**Fifteen media types, and the same fifteen.** Counted rather than assumed, and now pinned by a
test that lists them — a count agrees with itself after a swap, which is the one edit a reviewer
would not notice.

**And the question: does `/health` really answer from a cache?** Yes, deliberately — probing every
upstream per call makes the endpoint as slow as the slowest provider, bills somebody for asking
whether a model is alive, and can wake a scaled-to-zero endpoint (`FRD-117` §5.2). Measured rather
than defended: the probe runs every 60 s, a stale verdict is reported as stale after 180 s, and
stopping the model container made the endpoint answer **503 within 30 seconds**, recovering on
restart. What was wrong is that nothing *said* so — `time_taken` carries the last probe's duration
and reads exactly like a measurement taken just now. Each upstream now carries a `cached:<n>s` tag,
because a figure that invites the wrong reading is the same defect as a wrong figure.

**A test that passed for the wrong reason, caught by breaking the rule it names.** The blank-entry
cases were written against a model with no declared batch support, so every list was refused as
`EMBEDDING_AGGREGATION_NOT_SUPPORTED` — a 422 with nothing to do with blankness. Deleting the new
rule turned nothing red. Found the way this repository always finds it: break the property, watch.

---

## The tab said Angular, and a button said nothing

Three reports from using the console, two of them the same shape as everything else this week.

**The favicon was Angular's default**, byte for byte, from the Phase 0 shell. The AIRA mark existed
and was in the page header; the tab icon had simply never been changed. Nothing could have caught
it — no test asserts what an image looks like, the file is never fetched by a suite, and every page
renders correctly with a framework's logo above it. It is **generated from the mark's own geometry**
now (`tools/make_favicon.py`, PNG and ICO written by hand because no imaging library is installed),
so the alternative failure — two pictures of one logo, drifting the day a colour changes — cannot
start. The SVG is what current browsers use; Safari has never supported an SVG favicon, so the
`.ico` is a fallback rather than a leftover.

**"Issue a key" is gone.** It had three defects stacked in one attribute. It was a
`routerLink` with `fragment="api-keys"`: the page selects its tab from a **query parameter**, and
the tab is called `keys`, so the corrected fragment would have pointed at a name that is not a tab —
and behind both, the parent reads the parameter from the route **snapshot**, so navigating to the
same route with a different one changes the URL and nothing else. Repaired as an `output` first, then **removed** on the
owner's decision: the tab bar two centimetres above already leads to where keys are issued, and a
second route to one place is a second thing that can rot. The third inert control this repository
has shipped after a `title` attribute that showed nothing and a `routerLinkActive` that styled
nothing, and the third found by somebody using the console rather than by any suite — the only
assertion anywhere was that the block renders.

**And removing it exposed what the button had been standing in for.** With it gone the block said
`<your key>` four times and never said where one comes from, which is the same instruction-with-no-
destination the button had been an attempt at. There is a **Credentials** section now: the API key,
the tab it is issued on, and the three header forms the gateway accepts — plus the thing that was
missing outright, the **OIDC bearer token**. That is not an edge case: it is how a person or a
service account calls the gateway with no key minted, and the only way a Keycloak group grant
reaches the data plane at all. The sentence that earns its place is the *difference* — a token
carries an identity and **not** a use case — because without it a reader meets a 403 they cannot
explain. The measured propagation delay is written down beside it for the same reason.

**A removal has no natural counterpart**, so both tests now assert the absence and the route that
does exist: nothing fails when a control comes back, and without them the next reader to ask
"connecting a client, but where do I get a key?" adds one again. Writing the e2e assertion also
caught me guessing a `data-testid` that does not exist — it asserts the tab **panel's content**
now, because a tab can mark itself selected while showing nothing, which is the defect this test
replaced.

**And a question measured rather than answered**: can an arbitrary Keycloak group be granted a use
case, and can its people then issue keys and authenticate with a bearer token? Yes to all three,
end to end on the running stack with a group deliberately outside the `/use-cases/<slug>`
convention. What the measurement added is a number nobody had: **a grant is effective in Management
immediately and at the gateway about eight seconds later** — it travels by Kafka into the
read-model, and until it arrives the gateway answers 403 to a caller the console already shows as a
member. That is the design (`FRD-204`: the gateway never asks Management on the request path), and
it is worth knowing before somebody grants access, refreshes, and concludes it did not work.

**Both reports came back "unchanged", and both times the reason was the same**: the console runs
from a built image, and the running one was three hours old. It was still serving the 15 086-byte
Angular icon while the repository held the 1 226-byte mark. Nothing in the repository can catch
that — it is not a defect in the code, it is the code not having been deployed — but it is worth
writing down, because a fix that cannot be seen reads exactly like a fix that did not work. The
served bytes are now compared against what the generator produces: identical.

---

## The repository is public — say what AIRA does, not what the predecessor's system looks like

The instruction: the predecessor's product name out entirely, less information about its API, and
no dates in the documentation that is read as current.

The compatibility surface keeps its name — `/kira/api/external` is on the wire, and a client
migrating by changing a base URL is the whole point of `FRD-107`. What came out is everything
*around* it, which had accumulated without anybody deciding to publish it: the product name in four
documents, the specification document by **filename and section structure in forty-seven places**,
a source file of theirs cited in a comment, and — the one that matters most — its **security
posture**. Sentences saying the predecessor disables TLS verification and ships
`allow_origins=["*"]` with credentials existed to explain why *our* rule is different. That is a
good reason to state the rule and no reason at all to name whose weakness it is: a system that is
presumably still running does not need its weak spots described in a public repository by its
successor. The rules are unchanged and still explained; only the attribution is gone.

Dates left the two places read as *current* — `CLAUDE.md` and each FRD's header, and with it the
generated index. `Status: **Done (2026-08-06)**` is a delivery timeline nobody chose to publish,
and `git log` answers "when" better than a header somebody has to remember to update. **The DEVLOG
keeps its dates**: it is a log, and a log without dates is a list.

`tools/tests/test_public_repository_hygiene.py` is the counterpart, because the alternative is
remembering and every one of those forty-seven citations was defensible where it stood. It parses
every tracked text file and fails on the product name, the specification filename, a source file of
theirs, or a sentence describing their configuration — and separately on a date in `CLAUDE.md`,
`LESSONS.md`, the index or an FRD header. `.gitignore` is exempt on purpose: the rule that keeps
the predecessor's document out of this repository has to name it, and trading that protection for
one fewer mention of a filename is the wrong direction.

It found eleven FRD headers my own pass had missed — dates sitting in `Origin:` and `Related:`
lines rather than in the `Status:` field it was written to clean. All four properties were shown to
fail before being believed.

**Then the ADRs were read rather than grepped, and that found what a pattern could not.** A regex
finds the shapes somebody already thought of; `ADR-0010` held three it did not. It named the
**regional endpoints and credential type** of somebody else's deployment, `ADR-0012` said what
their system **is used for**, and three documents said which controls it **does not have** — no
budgets, no rate limits, no attribution. `FRD-118` added a fourth: that every authenticated request
there depends on the identity provider being reachable, which is an availability weakness written
out in full. Each existed to explain why *AIRA* is built as it is, which is a reason to state
AIRA's rule and none at all to describe the system it replaces. The same held for the **deviation
list** — "TLS verification stays on; CORS is an allow-list, not `*` with credentials" is the
removed disclosure one inference away, so those are now stated as our baseline rather than as
differences.

**The rule that came out of it: describe the contract, never the deployment.** What the wire
carries is ours to document, because we serve it — `422 MODEL_NOT_FOUND`, a list called `entities`,
a per-model default thinking mode; a reader implementing against this surface needs every one.
Where it runs, what it depends on and what it lacks are somebody else's operational detail. The two
read alike — one sentence saying there is no error code for a case, another saying there is no
rate limiting — so the **subject** is what separates them: seven statements now say *the contract*,
which is more precise anyway, and the distinction became something a pattern can check instead of a
reviewer. It caught this very entry on the first run, where both examples had been quoted verbatim.

**And then the question that found the damage: "will you still understand me when I say KIRA?"**
Yes — 940 mentions remain, and they are the ones that carry meaning: the module, the route, the
tests, `FRD-107`, `ADR-0010`, the migration guide. But checking rather than answering turned up two
things the gates could not see, because **no gate here reads prose**. A mechanical substitution
across forty-seven citations had left about fifteen sentences broken — *"Origin: the predecessor's
contract Depends on FRD-110"*, *"exactly as the predecessor's contract and §4"*, a docstring saying
*"the predecessor's terminal SSE event (the predecessor's contract)"*. ruff, mypy and 2121 tests
were all green over it: an operation accepted, apparently worked, and left the documentation worse
than it found it, which is the shape this project keeps writing guards about — arriving this time
in my own edit.

The second is the cost of the change and worth stating plainly: forty-seven citations pointed at a
contract document, and afterwards **nothing said the document exists**. `FRD-107` §7 now says it is
held outside this repository and not ours to publish, and — more useful day to day — that the wire
tests are the working reference, with the document needed only for a case AIRA has never served. Three more properties, each shown to fail.

## 2026-08-13 — `CLAUDE.md` §6 was a third copy of this file, and the copies disagreed

Reported by the owner: the status section is over a thousand lines, and short feature descriptions
would do — the FRDs, PRD and ADRs exist. Measured before touching it: **1801 lines, of which §6 was
1667 — 93%**, 24 377 words, against a DEVLOG of 137 dated entries that already held the same
rounds in the same prose.

**The length was the complaint; the duplication was the defect, and it had already cost
something.** Every FRD carries a `Status:` header and §6 restated it — so on inspection **twenty-two
headers disagreed with §6**. `FRD-100`, the Gemini surface every request goes through, still said
*Draft*; so did tool calling, all of Phase 0 and most of Phase 2. The copy loaded every session
stayed true and the copy nobody opens rotted. That is *a hand-written list with no counterpart*, this
repository's most repeated shape, arriving in the documentation instead of in the code — and the
same rot had reached §6's own "next candidates" list, which named five features as upcoming that
had all shipped.

Three homes, each with a counterpart that fails:

- **The FRD header is the single source.** `docs/features/README.md` is **generated** from the
  headers (`tools/features_index.py`), and `test_features_index.py` compares the committed index
  against them **in both directions** — a status changed without regenerating, and an FRD added
  without either. Both were shown to fail. `FRD-406` had a header format of its own and was
  invisible to any tool reading headers, which is exactly how a second format survives; normalised.
- **`docs/LESSONS.md`** holds what is in no FRD: the recurring defect shapes and the rules they
  produced, deduplicated (the same rule appeared up to six times in §6's prose, each time in
  different words). 232 lines, grouped by where they apply, each with the cases that produced it —
  and §4 now says a *new* rule is **merged** into an existing entry rather than appended, since
  this file goes stale by growing.
- **The DEVLOG keeps the narrative**, which is what it was always for.

§6 is **46 lines**: what runs, where to look, and the five things deliberately open.
`test_claude_md_stays_short.py` fails when it grows past 90 — not because length is the problem,
but because 1667 lines happened one defensible paragraph at a time, and a rule only a reviewer
enforces is one the next round breaks. Shown to fail by adding a hundred lines.

`CLAUDE.md`: **1801 → 192 lines.** Nothing was lost — it moved to the document that owns it.

## 2026-08-13 — Where the two surfaces still forked, and why nothing said so

Asked after the `/uc` prefix had to be *retrofitted* onto the KIRA surface: we agreed the surfaces
would be abstracted as far as they can be and duplicates kept out — how did a fork appear anyway?
The answer is that the boundary was drawn correctly and drawn once. `api/serving.py` extracted
**everything below the surface** — the pre-dispatch gate, the pipeline, the dispatch chain, the
audit writer — and what it left to each surface is what genuinely sits *at* the surface: parsing,
the error envelope, and the two places a row is written for a request the shared path never
finished. **Attribution sits exactly on that line**, which is why `use_case_refusal` (the
*authorising* half, extracted after a bug) had been shared for weeks while `resolve_use_case` (the
*reading* half) had not.

So the fork was looked for mechanically rather than by reading: every `record_request` call site
against every field it passes. Three divergences, none of them an error anywhere.

- **`api` was a defaulted parameter.** `record_request(..., api: str = "gemini")` made a call site
  that forgot it right on one surface and silently wrong on every other. Measured live: a KIRA
  request whose pipeline ran an LLM filter left its classifier row under `api='gemini'`, so a use
  case's *governance* spend (`FRD-125b`) was reported against a surface it had never called. The
  discriminator now travels on the `AuditTrail`, set once by the surface that owns the request, and
  **neither the parameter nor the field has a default** — a default on a discriminator is a
  discriminator that stops discriminating at the first hurried call site.
- **`tool_calls` was recorded only on the served path.** A request that offered functions and was
  then refused recorded nothing about them, on either surface — so *"somebody keeps trying to use
  tools here"*, which is a `FRD-122` question, had no answer. The count is taken in
  `prepare_for_dispatch`, before anything can refuse, and both refusal recorders pass it.
- **The thinking-mode parse was written out twice**, normalisation, code and message. Identical on
  the day it was written and compared by nothing — so a surface that lost its `.strip()` would
  accept `" high"` from a client the other refuses, with no error anywhere. One `mode_from`.

Verified live after the fix: `kira|pipeline:injection_filter` beside `kira|chat`, and a refused
tools request recording `{"declared": 1, "called": []}`. Each fix was shown to fail first — the
pipeline row against the restored default, the tools row against **both** halves of the old code
(the late count and the omitting recorder) — and three of the four mode-parse rows went red when a
surface was made to drift, with the exact-match row correctly staying green.

**The guard is the deliverable.** `test_every_surface_records_alike.py` compares every recording
site against the shared one and requires an omission to be *named with its reason*, in both
directions, so a third surface or a new kind of row has to be looked at rather than inheriting
whatever the defaults happen to be. It caught the omitting recorder on its own. `QA31`–`QA33`;
409 mutation properties, all three new ones caught. **No functionality changed.**

**And the integration run found a fourth, in a test.** `test_an_embedding_batch_weighs_what_it_is`
went red once under full-suite load and green in isolation — the signature of a test measuring the
machine. It configured `limit_rpm=600`, which refills ten tokens a second, so the five its batch
takes are back **half a second** later and the second request is refused only if the first answered
faster than that; measured against the running stack, that call takes 82–234 ms, so the margin was
a quarter of a second. The file's own `SLOW_REFILL` constant exists for exactly this and carries
the lesson in its docstring — this one test did not use it. Now it does, and it was **shown to
still fail for the right reason**: the gateway was rebuilt with the batch weighed as one, both
batch tests went red, and the restore brought them back.

---

## 2026-08-13 — A developer round: 227 tests over one governed use case

One use case with a real language model and a real embedding model, both surfaces, four verbs, and
the policies changed underneath it while the gateway runs — filter, router, fallback chain, release,
tool switch, caching, storage, rate limits in three scopes, budgets across three limits and two
periods, and the kill switch. Five files, **227 tests, 3:42**; with the existing suites, **802
passed, 22 skipped, 0 failed**.

**It needed a fixture of its own, and the reason is the finding.** `conftest.Fixture` builds a use
case and a key, and since `FRD-308` such a use case may call **nothing** — so a suite written on it
can only ever exercise `mock-1`, which is exempt from the release *and* approval gates and therefore
cannot answer a question about either. Three existing suites had quietly become tests of the double.
`governed.py` releases both real models and offers the policies as methods.

**Written against the running stack rather than from the source.** Every wire shape was probed live
before a test asserted it, which is what kept 227 tests from encoding my assumptions — and four of
those assumptions were wrong. They are now written down where the next reader meets them:

- **A chain does not rescue a primary nobody serves.** `prepare_for_dispatch` answers 404 before the
  chain is consulted, which is right: a name nothing serves is a typo or a retirement, and answering
  from a different model would be the substitution `ADR-0012` §3 exists to prevent. The chain covers
  candidates that fail a *condition*, not candidates that are fiction.
- **A budget and a rate bucket differ on an oversized request, deliberately.** The reservation script
  checks `requests >= limit` *before* adding — "already at it" is what refuses — so one request may
  carry the counter past the line, while a bucket refuses a batch bigger than its capacity outright.
  Refusing in the budget too would mean a batch of five hundred could never run under a 499-request
  monthly budget, even on the first of the month. So the weighting is asserted through what the
  batch *left behind* rather than through its own status.
- **An empty use-case header is no selector, not a bad one** — a client sending an unset variable,
  which falls through to what the key is bound to.
- And a whitespace one cannot be sent over HTTP at all; `httpx` refuses it before the gateway sees it.

**Proved able to fail, not assumed.** Three properties were broken in the gateway and rebuilt —
`tool_summary` made to carry a tool's arguments, an embedding batch made to weigh one, the injection
filter made to record only what it flagged. Each of the three tests named for those went red, and
green again on restore.

**One finding, reported rather than asserted — and then fixed.** Both streams were driven and hung
up after the first chunk, side by side:

    gemini  streamGenerateContent  status=200  outcome=client_gone
    kira    streaming-chat         status=499  outcome=client_gone

They agreed on what happened and disagreed on the number, and the test asserted only the outcome —
deliberately, because writing `{gemini: 200, kira: 499}` into a green test is how a defect becomes a
specification.

The Gemini stream assigned `acct.status = 200` unconditionally, on the reasoning that a stream which
dies half way still has 200 in its already-sent headers. That is an argument about the **wire**, and
the column is not the wire: `499` appears exactly once in the gateway, as `Accounting.status`'s
default, with the comment beside it reading *"Nobody is sent it; it exists so the audit can tell that
case from a served one"*. The KIRA route never assigned the status at all — `served()` or `failed()`
and otherwise the default — which is why it was right. The Gemini one does the same now.

**Google's own model agrees, and the real API was asked what it could answer.** `google/rpc/code.proto`
maps `CANCELLED` — *"the operation was cancelled, typically by the caller"* — to **499 Client Closed
Request**, so the *compatibility* surface for the predecessor had been following Google's convention
while the Google-compatible one had not. What real Gemini *records* is not observable from a client
and was not measured; what was measured is that its wire behaviour is a 200 SSE stream that simply
closes, exactly like ours. (A side observation from the same call: 114 thought tokens for a 15-token
answer — the order of magnitude `FRD-111`'s reservation exists for.)

**No mutation entry, and that was checked rather than assumed.** The defect was reintroduced and the
whole hermetic gateway suite passed, 1424 tests: the two versions differ only when nothing was
served, and `TestClient` buffers a streamed body before a test can hang up. A mutation that survives
would be a false claim, so the harness gets a note naming the integration test that does guard it —
the second entry in that list, after `asyncio.shield`.

## 2026-08-13 — All four layers run, and the fourth found the same defect again

The two checks the repair round left open, run to the end.

**`make mutants`, all 406 properties: every one caught. No survivor, no stale
anchor.** That answers the question the thirteen rotted anchors raised — whether
there was a second kind of problem underneath, properties no test defends at all
— and the answer is no. The run restored every file it touched and left no
journal. It is worth stating what this run costs, because that is why it had not
happened: about eight minutes checking the baselines are green, then roughly an
hour applying 406 edits and running a pytest selection for each. The fast anchor
check added this morning is what makes the expensive run worth trusting; it
cannot tell you whether a property is defended, only that the question being
asked is about this codebase rather than a previous one.

**Playwright, 127 tests: one failure, and it was the same defect as `gpu-b`.**
The model-release test typed `gemini-flash-latest` into the picker — a model that
exists only where somebody configured a Google AI Studio credential and imported
it (`FRD-507`). On `make showcase`, and in CI which seeds the same way, the
catalogue holds `qwen3:0.6b` and `all-minilm`; the picker never offered that
name, the keypress chose nothing, and the assertion waited out its ten-second
timeout against an empty locator. The test is about a release being made and
taken back — *which* model is a property of the deployment, so naming one made it
assert the catalogue instead. It reads the picker's own first option now, and
searches for that, which keeps the reason the name was introduced (searching
leaves exactly one option, so the keypress has one possible meaning) without
pinning it to one installation. It also dropped from 11.2 s to 1.9 s, because
most of that was the timeout.

Third instance in two days of a test naming something only one machine has, after
`gpu-b` and `qwen2.5:3b`. The tell is the same each time: the assertion is about
behaviour and the failure is about inventory.

All four layers now: **2068 hermetic · 575 integration · 127 e2e · 406 mutation
properties**, with one e2e test skipped on a documented cost decision (a hundred
catalogue questions against a local model, minutes per run).

## 2026-08-13 — The integration suite had stopped following the product

`make showcase` end to end (21 containers, 10 served / 1 refused / 0 failed — the refusal is the
injection, as designed), then `make test-integration` against it: **19 failed, 559 passed**.

**None of the nineteen was a product defect, and none came from the day's work.** That second half
was proved rather than argued: the three changed production files were stashed, the gateway rebuilt
from `HEAD`, and the suspicious suites re-run — identical failures. The causes cluster tightly
around the deliberate changes of 09.–12.08., which is to say the suite has not been green since
that work landed.

The two that were worth measuring rather than reading:

- **`FRD-308` is enforced.** Against a use case released only for `other-1`, a real model is
  refused by name (`400 … has not been released`); `mock-1` is served. `ModelReleasedForUseCase`
  exempts a provider marked `is_test_double`, and the test posted **against the double** — the one
  model the rule does not apply to. Its sibling `test_a_released_model_is_admitted` was **green for
  the same reason**, and would have stayed green with the release empty, absent, or the check
  deleted. That pair is the clearest instance yet of a test that cannot detect the property it is
  named after, and both now use a real model: shown to go red when the release is flipped in either
  direction.
- **The cursor paging is sound.** It failed in the full run and passed 3/3 in isolation, because
  the audit write is off the request path (`FRD-405`) and the test waited for **three** rows before
  paging as though six existed. Under a full suite the queue is deeper: page one took the newest
  three of five and page two correctly returned the remaining two, reporting "the second page lost
  rows" — a paging defect that was not one.

One correction to my own reading, and one to my own instrument: the single `vector` returned for a
list is not a silent loss but a decision confirmed against the contract and measured by
cosine (1.000000 to the parts concatenated); and a 401 in the first probe was a regex that clipped
the API key by one character.

The remaining fifteen: six use cases created without a release (`FRD-308`, 11.08.); two requests
sent with no use case (`AIRA_REQUIRE_USE_CASE` became true by default, 11.08.); one hard-coding
`gpu-b`, a server name from *this file's own* fallback fixture, so it tested whichever
`AIRA_OPENAI_SERVERS` naming the deployment used; one asserting `HEALTHY` on the KIRA surface after
`/health` was corrected to the predecessor's `Healthy` shape; one dry run predating `use_case`
becoming required; one expecting `["ok",""]` to be refused, from when a list meant *many*
embeddings; and four asking for `qwen2.5:3b`, which nothing pulls.

Three of the repairs went past the test that failed, because a stale expectation is usually a
missing assertion:

- **The skip guard existed and was applied once out of five times** — an inline
  `if response.status_code == 404: pytest.skip(...)`. It is a fixture now, asking the model list
  *before* the request (a 404 also means retired, mistyped or uncatalogued, and a skip that
  swallows those hides what is worth seeing), plus a parser that fails any test reaching for
  `TOOL_MODEL` without requesting it.
- **The hang-up is performed rather than waited for.** The stream test wrapped itself in
  `pytest.raises(ReadTimeout)` against a five-second client timeout, so it exercised a disconnect
  only when the model happened to be slower than that — and failed outright when it was quick. It
  now breaks out of the body and closes the socket, and asserts that the status and the recorded
  outcome **agree**, which no amount of model speed makes true by accident.
- **The provider name is compared between the surfaces, not against a constant** — which is the
  property that test is named after — and `publisher` joined the columns it reads, the one of the
  provenance triple that was missing.

`["ok",""]` moved out of the refusal table into a test that asserts the join: `200` alone would
also be true of a surface that dropped the second element, embedded only the first, or embedded the
literal list, so the vector is compared to the one `"ok"` produces on its own.

After: **575 passed, 22 skipped, 0 failed**, under the full-suite load that exposed the two racy
ones. Hermetic suite, ruff and mypy unchanged and green.

## 2026-08-13 — `make down` did not, and said so while exiting 0

Reported: after `make showcase`, `make down` does not bring everything down. It does not, and the
machine it was reported from still had the evidence on it — a showcase started ten hours earlier,
the infrastructure gone, and `gateway`, `management`, `gateway-consumer`, `management-relay`,
`frontend` and `gateway-retention` still up, with the consumer crash-looping for **eight hours**
against the Postgres that had been deleted out from under it.

**Two definitions of "the stack", and the stopping targets had the smaller one.** `showcase` runs
both compose files with the `observability` and `demo` profiles — 21 services. `down` named one
file and one profile — **8**. So thirteen services were not in its model at all. The application
services also carry `restart: unless-stopped` while the infrastructure does not, so they do not
merely survive a stop, they come back.

The part that makes it this project's own defect rather than an oversight: compose could not remove
its own network because the orphans were attached to it, printed `Network aira Resource is still in
use`, and **the target exited 0**. An operation that is accepted, appears to have worked, and did
not happen — the same sentence as the outbox rows, the `revoked_at` column and the six no-op
documentation edits.

Fixed as one widest view (`COMPOSE_ALL`: both files, every profile) read by every target whose job
is to act on *whatever is running* — `down`, `destroy`, `ps`, `logs`, `restart` — plus
`--remove-orphans`. **Both halves are needed and this was tested, not assumed:** `--remove-orphans`
over both files removed the six application containers and *left* `ollama` and `management-seed`,
because a service behind an inactive profile is in the model and therefore not an orphan. Only
naming the profile reaches those. `destroy` was the worst of them — it deleted the volumes while
the services using them kept running. `down-full` is now an alias rather than a second set to get
wrong.

The profiles are written out rather than passed as `--profile "*"`. The wildcard is
self-maintaining and needs Compose v2.24; on anything older it matches a profile literally named
`*`, which is silently this same bug again. A written list fails loudly instead — because
`tools/tests/test_compose_lifecycle_covers_the_stack.py` compares it to the profiles that exist, in
both directions, and asserts that every lifecycle target uses the wide view. Verified end to end:
containers started across both files and the demo profile, `make down`, nothing left, network
removed cleanly.

**Then the same question one level out.** Checking the documentation that describes these targets
found `docs/deployment/showcase.md` ending with `make down-full-volumes` — a target that has never
existed, at the last step of the guide written for somebody's first run, after the long part.
`FRD-208`'s instruction with no destination. The link checker now reads `make` commands too and
found three more: `make retention` (it is `prune`), `make relay-once` (the relay is not a daemon —
it publishes what is pending and exits, so `relay` *is* the once), and `make seed-local-catalog`
(no such target; the script is run directly). Correcting the second one corrected the page as well,
which had been calling the relay long-running.

The first version of that check scanned prose and needed a list of English words that may follow
"make" — "make sure", "make sense", "make three of them". That list is a hand-written list with no
counterpart, which is the defect the check is an instance of. Commands are read out of code fences
and inline spans instead: a reader runs what is typeset as a command, and the guard was shown to
ignore a paragraph full of "make sure" while still catching the dead instruction.

## 2026-08-12 — The guards that had stopped guarding, and a suite that would not end

A read of the code and the tests for structure, for fixes that were made once instead of made
general, and for tests that are green about nothing. No functionality changed. Four things, and
three of them are the same defect: **a rule that is only checked by something nobody runs.**

**Thirteen of the 406 mutation properties were defending nothing.** Ten anchors named code that had
moved or been deleted — `O2` had been vouching for a defensive parse of `realm_access` since
`ADR-0017` deleted it — and three matched *several* places, which is worse: the harness edits the
first match, so `C2` claimed to guard the model catalog's "no such row" branch while editing the
"un-lookupable name" branch three lines above it, a different property with a different test, and
reported a confident `caught`. `make mutants` does say `STALE`, and returns non-zero; it is a
multi-hour run, it is not in CI, and nothing else asked. So the one invariant that decides whether
the whole harness means anything had **no fast check at all**, and it rotted for as long as that was
true. `tools/tests/test_mutation_anchors.py` now asks it in milliseconds — every anchor exists,
exactly once, actually changes something, and names a test selection that exists — and the harness
refuses an ambiguous anchor before it runs, which its own docstring had required since it was
written. All thirteen were re-anchored and each was then **run** and shown to be caught.

Two of the repairs went past the harness into the code, which is the point of asking. `O2`'s
property — *a malformed `groups` claim confers nothing rather than raising* — turned out to be
undefended in the ordinary sense too: three shapes a real directory produces (a bare string from a
single-valued mapper, an object, a number) had no test, and the mutation that isolates the rule
survives the whole existing file. And `QA16`'s anchor was ambiguous because the KIRA surface wrote
the same three lines and the same nine-line comment into all three of its dispatching routes;
they are one function now, which makes the anchor unique by construction rather than by a string
somebody keeps unique by hand.

**The suite finished and then did not exit.** Two tests configure real OpenTelemetry providers —
that is what they are for — and the providers are global and were never taken down. Each starts
three non-daemon OTLP exporters aimed at `localhost:4318` and puts an OTLP handler on the root
logger, so from those tests onward every span and every log record in the run was queued for a
collector that is not there, and at exit the SDK retries the flush. Measured: those two tests run
in **0.13 s** and their process took **15.7 s** to end; the full suite hung for minutes after its
last dot, which reads as "the suite is slow" rather than as a defect with an address. The root
`conftest.py` — which already exists to say that a unit test must not read the developer's machine
— now also says that it must not leave the network running: an autouse fixture takes back whatever
a test installed globally, an export attempt is bounded so it cannot cost seconds, and a
session-scoped assertion fails the run if any exporter thread is still alive at the end. The full
hermetic suite: **1 m 52 s, start to exit.**

**Two definitions of one rule, in the two places that already know better.** The gateway had its
own copy of `usecases_from_group_paths` — the function, the prefix constant, character for
character — while `aira_common.access` exists to be the single home of exactly that rule and says
so in its docstring. Nothing had drifted yet, which is the only reason it was still true; `FRD-209`
is the record of what it costs when it stops being. The copy is gone. And the two seeds that
declare the local models hold the same measurements twice; that pair has drifted **twice already**
(`minimal`, then `tools`, the second making the whole of `FRD-131` unreachable from `make
showcase`), and both times the fix was to copy the correction across by hand. Neither file can
import the other, so they are compared in both directions instead — and the test was shown to fail
against both historical drifts before it was kept.

**A hand-written list with no counterpart.** `test_app_state_is_typed.SERVICES` was written by
reading `create_app` once, so a twenty-first service would have been governed by nothing and would
have announced itself through nothing — a set has no opinion about what it does not contain. It is
compared against what `create_app` assembles, in both directions, with the four deliberate
exemptions named and reasoned. Writing both directions immediately caught the guard's own bug: it
reused a predicate that matches `<x>.app.state` and found nothing at all.

## 2026-08-12 — A showcase check, and the column that said "never revoked"

`make showcase` run again and walked end to end: 15 containers healthy, both surfaces serving,
the control plane and reporting answering, the real `google-genai` SDK generating, streaming and
embedding against the running gateway, **no 5xx and no unhandled error in any of the four service
logs**. The three commands the showcase now *prints* were extracted from its own output and run
**verbatim** — 200 on each, 18 SSE events on the streaming one. A demo that prints a command it
has not executed is a demo that works for whoever wrote it.

The check then found something by accident, in the way that counts: I queried `api_keys` for
`revoked_at IS NULL` to see which credentials were live, got six, and concluded two keys had
outlived their deleted use case. They had not — they answer `401`, and the column that says so is
`is_active`. **My query was wrong, and it was wrong because the column I asked is a lie.**

Two paths revoke a key. `ApiKeyService.revoke` — the gateway-side one, used by the CLI — sets
`is_active` *and* stamps `revoked_at`. `_set_api_key_active`, which is how **every** revocation
from Management arrives, set only the flag. So on any deployed system `revoked_at` was `NULL` for
every key that had in fact been revoked: a field reading "never revoked" about exactly the ones
that were.

No credential was ever wrongly accepted — `verify` reads `is_active`, and that was always right.
What was broken is the record, and the record is the whole point of this system: *when was this
credential revoked* is an incident question, and the field that answers it was empty. It is stamped
now, on the way down only, because revocation is terminal and a reactivation that cleared the time
would erase a decision. The event carries no timestamp, so this is when the gateway **learned** of
it — said out loud in the code rather than implied.

Verified live: a key issued, revoked through Management, and the read-model row carrying
`is_active = f` with a time beside it. The two rows from an earlier walkthrough still show the old
shape, which is what the gap looked like everywhere until now.

`QA30`; **406 properties**.

---

## 2026-08-12 — The showcase hands a KIRA user something to paste, and says what the assistant is

Asked where the demo tells somebody who runs KIRA today how to **try** it, and the honest answer
was: it does not. The previous entry added the four administration steps and two migration guides —
which is what a reader needs to *migrate*, and not what they need to *try*, because the demo has
already done all four for its own use cases. What was missing was one command that works.

`tools/showcase_try_it.py` prints it, and it **reads the running catalog** rather than restating it:
the key comes from the same derivation the seed uses, the integer model id from
`GET /kira/api/external/models`. A block written by hand would carry an id from the day it was
written, and `FRD-114` FR-6a is why that matters — ids are assigned in the catalog, and a stale one
names a model nobody has. Both printed commands were then run **verbatim**: `200` on each.

The Gemini equivalent is beside it deliberately. The two surfaces answer the same question from the
same key and land in the same audit trail under different API names, and putting them next to each
other is the shortest way to say so.

**And the coding assistant was described as a tool rather than as a use case.** The block jumped
straight to the OpenCode command, so a reader watched an assistant work and could not say what
about it was *governed* — which is the only reason it is in the demo rather than a second chatbot.
It now says what makes it its own: function calling is on and it is the **only** use case here that
has it (checked against the running read-model, not the seed source); one human instruction becomes
many model calls, so the limit is 240 rpm and sized for an agent rather than for a chatbot; source
code and file paths are content and end up in stored prompts; and prompt caching is deliberately
**off**, because this runtime reports no cached tokens and a switch shown as on while doing nothing
is an absent control wearing a present one's badge.

Every claim in that block is a seeded value, so the console can be opened on any of them.

---

## 2026-08-12 — Two migration guides, written by doing the migration

The demo showed a governed gateway and left the next question unanswered: *how do I put my own
client behind it?* Two documents answer it —
[`MIGRATION-KIRA.md`](MIGRATION-KIRA.md) and [`MIGRATION-GEMINI.md`](MIGRATION-GEMINI.md) — and
`make showcase` now names the four steps and links both, because an instruction with no
destination is a defect this project has already recorded once.

**Both were executed end to end against the running stack before a word was written**, and the
outputs in them are what came back. That was not ceremony: writing from the code would have got
three things wrong. The plaintext key is returned as `api_key` and not `key`; a key's name is
`label` and not `name`; and the delay before a fresh key is accepted is a real, measurable **two
seconds** (Kafka), which is the difference between "your key does not work" and "wait a moment".
A guide nobody has followed works for whoever wrote it.

The content is the same four administration steps for both surfaces, because they *are* the same:
create the use case, release the models it may call, add its people or a Keycloak group, issue a
key. What differs is one sentence at the end — a base URL. The step worth spelling out is the
release: a new use case answers `allowed_models: []` and can call **nothing**, which reads as a
bug for exactly as long as it takes to say that it is the point.

`X-AIRA-Use-Case` gets its own section on both, because it is the one genuinely new concept and
its behaviour is not guessable: a caller in exactly one use case sends nothing, one in several is
refused with the candidates named, one in none is refused outright. And the header **chooses among
what you already have** — naming somebody else's use case is a 403, demonstrated in the walkthrough
rather than asserted.

The Gemini page lists only clients that were actually run — `google-genai` and OpenCode — and says
in as many words that LangChain, LlamaIndex and the Vertex SDK have not been tried. A compatibility
page that implies coverage it does not have is worse than a short one.

One thing the walkthrough checked and found **correct**: a model that was never released to the use
case was served — because it was the mock, and the test-double exemption is deliberate, documented
and bounded to `local`/demo. Verified rather than assumed, which is the only reason it is not in
the list of findings above.

---

## 2026-08-12 — A quality and fault-tolerance read of the whole code

Brief: find defects, change no functionality, raise code quality and fault tolerance. Six findings,
and the one that matters most had been sitting in the disaster-recovery path.

**Every member of a use case shared one compaction key.** `record_to_outbox` derives the key from
`id`/`prefix`/`slug`, and a `membership.upserted` payload is `{slug, username, role}` — so two
members of one use case produced two messages under the same key. The topics are **compacted**, so
the second erases the first, and a gateway rebuilding its read-model from the log sees **one member
per use case** and silently loses the rest. The live read-model is right the whole time, because
each event was applied as it arrived; it is only a rebuild that loses people, which is the worst
possible place for a latent fault.

The same defect was found for **group grants** in a live round and fixed as an `if` beside the
function — while `membership.upserted`, three lines above it in the same table and carrying the
same shape of payload, was left alone. So it is a table now (`_ALSO_IDENTIFIED_BY`), and the test
asks the **rule** of every event type rather than the case of one: two different entities of a kind
must produce two different keys. A third one cannot be forgotten.

**One malformed config event stopped config distribution.** The consumer called `apply_event`
straight out of `async for`, and every handler indexes its payload — so a renamed field, a
truncated value or a database blink ended the process. Then the container restarts, reads the same
message and dies again: a poison pill, while the gateway serves happily from a read-model that has
quietly stopped updating. **A revoked API key goes on working.** And the opposite outcome is
available too, because offsets auto-commit on a timer: the commit moves past the bad message and
the event is lost instead. Which one you get is a race. Now one event that cannot be applied is one
event skipped — **loudly**, with topic, partition and offset — and everything after it still lands.

**A failed detection round lost the window it was about to evaluate.** `tick` takes the touched
scopes in its first statement so a concurrent write cannot be missed, and reads a database for
everything after that. Anything that raised in between took those minutes with it: the loop logged
a warning and no rule ever saw that traffic. `ADR-0014` makes detection asynchronous, which
promises it happens *later*, not that it may quietly not happen — a thousand rate-limited requests
in the minute a database hiccupped is exactly the shape a detector is for. The scopes are merged
back now, not assigned, because traffic keeps arriving while a round is failing.

**The typed-state guard could not see the spelling that matters.** `test_app_state_is_typed`
matched attribute access and missed `getattr(request.app.state, "rate_limits", None)`, which is the
*more* dangerous form: an attribute read of a renamed service raises and is loud, a `getattr` with
a default answers `None` and the call site's `if x is None: return` turns it into a control that
silently does not run. The bound on failed authentications is one of the four sites it was blind
to. Annotating them made mypy report three further reads immediately — `UpstreamProbe.snapshot()`
was typed `dict[str, object]`, which is the same as saying nothing.

**The migrations had drifted from the models**, found by running `makemigrations --check` for an
unrelated reason: `FRD-308` altered `allowed_models` and never generated the migration. Nothing was
broken, which is why it survived — what it does instead is land on somebody else, because the next
person's unrelated `makemigrations` sweeps it into their change where a reviewer reads it as part
of it. Generated, plus a test that runs the check so the next one cannot be silent. The outbox's
`ordering` gained `id` beside `created_at` while it was open: an outbox's **order is its meaning**,
two events in one transaction share a timestamp, and on a compacted topic that is the difference
between an upsert followed by a delete and the reverse.

**And the console could not say who you are.** `/me` had no error branch, and everything
role-shaped in the shell comes from it: the username, the role chips, **Logout**, and the nav
entries for investigating an incident and for oversight. A failure removed all of them in silence,
so an IT Security reader saw a console built for somebody with fewer rights, with nothing to
explain it and no way to sign out. `FRD-206`'s complaint inverted — a refused action announces
itself, an absent one reads as a boundary.

**One proposed fix was refused by the suite, and it was right to refuse it.** The group-grant cache
assigns `()` when its read fails, so one database blink takes access from every group-granted
caller; that reads like a fault-tolerance gap and I changed it to serve the last good copy.
`test_grants_are_dropped_rather_than_served_stale_when_the_read_fails` went red, and its reasoning
holds: a grant is *permission*, so the safe direction is the opposite of a rate limit's — the moment
the table stops being readable its last answer stops being evidence. Reverted, and the argument is
now written where the code is rather than only in the test. `TokenSource` serving through a failed
refresh is not the same case: a credential we already hold is still ours, a permission we can no
longer verify is not still granted.

`QA27`–`QA29`; **405 properties**.

---

## 2026-08-12 — The showcase runs, and the SDK found the field that mattered most

A full `make showcase` from the committed state, then a walk over everything it starts. Fifteen
containers up, both surfaces serving, control plane and reporting answering, **no 5xx anywhere and
no unhandled error in either service's log**. Every status in `request_logs` was one somebody
asked for: 200, 400, 404, 422, 429, 499.

Then the `google-genai` client was pointed at the **running** gateway rather than at an in-process
app, and it refused to work at all:

    400 INVALID_ARGUMENT  generationConfig.thinkingConfig.thinking_budget: Extra inputs are not permitted

**The SDK writes those two fields in snake_case.** Measured rather than assumed — eleven config
fields serialised and compared — and the result is exact: `maxOutputTokens`, `topP`, `topK`,
`stopSequences`, `seed`, `responseMimeType`, `candidateCount`, `systemInstruction`,
`presencePenalty` all camelCase, and *only* the two inside `thinkingConfig` come out as
`thinking_budget` and `include_thoughts`. An inconsistency in the client, and it does not matter
whose bug it is: it is what the official client puts on the wire, and no caller can change it.

The consequence was the worst available. **"Do not think" is the configuration a governed gateway
sets on nearly every request** — it is what this project's own demo traffic sends — and from the
official SDK it was a 400. Both spellings are accepted now; the camelCase one keeps working, so a
hand-built client is unaffected.

`includeThoughts` arrived with it and is the opposite decision: **refused by name**. It asks for
the model's reasoning to be returned, and this gateway drops thinking blocks and never stores them
(`FRD-119` §5.4), so serving it would answer with no thoughts, a 200, and nothing saying why.
`false` is carried and means nothing, because it asks for exactly what we already do and refusing
agreement would be silly.

Third field this week that a real SDK sends somewhere we did not anticipate, after the embedding
`model` and the empty `finishReason`, and the three share one cause: **our tests send what we
believe the SDK sends.** The SDK suite is up to seven cases and is the only thing here that can
find this class at all.

`QA25`, `QA26`; **402 properties**.

---

## 2026-08-12 — A text part carries text, and the compatibility scope is settled

A second static comparison against the predecessor produced a longer list. Most of it is
deliberate and stays; one item was a defect of ours, and it is worse than the report described.

**`str(...)` on a text part converted anything.** `parts` is a list of plain dicts so that a part
can be text *or* an attachment, and the mapper handed whatever arrived to `str`. Measured:

    {"text": null}      → the model was asked about the word  "None"
    {"text": 123}       → "123"
    {"text": true}      → "True"
    {"text": {"a": 1}}  → "{'a': 1}"      a Python repr, on the wire

No error at any point. A caller sending a null gets a fluent answer to a question nobody asked,
with a 200, and blames the model. That is `FRD-124`'s rule broken in our own code — a value
silently transformed is worse than one refused, because only the refusal is visible — and the
same family as the missing newline found the day before. The type is checked where the request is
**parsed** (`RequestContent`), not in the mapper: a surface parses and the layer decides, and one
place that can refuse beats two that can disagree. The refusal names the part index and what was
sent, and the caller's value never comes back out in `details` — this body reaches logs and
screens, and echoing content is how a refusal becomes a second copy of the thing refused.

That the predecessor types the field as a string and rejects the rest is a coincidence here. The
fix would be right against any predecessor.

**Scope settled by the owner, so the remaining items are decisions rather than debt.** Model ids
stay as they are — the receiving project adapts, and `FRD-114`'s numeric alias means an operator
*can* assign the predecessor's ids where that is wanted. Error messages stay **English**
throughout, whatever the predecessor does. Error codes and statuses stay **more specific** than
the predecessor's, which is a deliberate divergence: a client switching on `code` sees the same
strings for everything the predecessor can produce, and the extra ones name cases it never had.
SSE keepalive is deferred, on purpose, with the question written down.

Everything else the report listed is already recorded as deliberate: `GET /models` behind
authentication, `extra="forbid"` (reversing it recreates the defect where eleven fields returned
200 and did nothing), the attachment signature check, the schema bounds, and cached health probes
(`FRD-117`: a health check must not be able to take down a healthy service — the *shape* became
the predecessor's yesterday, the semantics are deliberately ours). One listed difference is not
one: we normalise embedding task types to upper case where the predecessor is case-sensitive, so
we are the *more* permissive of the two and no working client can notice.

**`/version-info` stays open**, and is the same class as `/health` was: we answer a partial object
without `jenkins`, where the predecessor's DTO is all-fields-or-null. Which of the two repairs is
right depends on whether that field is nullable there, which is a question for the predecessor's
source rather than a guess here.

`QA24`; **400 properties**.

---

## 2026-08-12 — A static comparison against the predecessor, and the SDK that had never been asked

A compatibility check read KIRA's own source against ours. Most of what it flagged is deliberate
and stays (strict fields — reversing that recreates `FRD-124`'s defect; authenticated `GET /models`;
schema bounds; use-case attribution; and by owner decision our **more specific error codes**, since
the Gemini surface is where the generic ones live). Four findings were real, and the first was not a
compatibility question at all.

**`/ki-usage` answered 500 to every expectable error.** Measured: no parameters, one parameter, an
unparseable date, a backwards range — four `500 internal_error`, in *Google's* envelope. Three
routes wrap their body in `except KIRA_REFUSALS`; this one did not, and there was no
application-level handler, so every `KiraError` it raises fell through. Worst was the oversight-role
check: a **permission** refusal reported as our own failure, which in a governance console is the
conclusion that spreads. Third instance of one shape — the 174-case round found this surface had no
branch for a shared control's refusal, the envelope round found the two classes that never reach a
route — so the fix is an **application-level handler** plus a guard that walks every mounted route,
not a fourth `try`. Building that guard immediately caught its own failure mode: a hand-rolled walk
over `app.routes` returned **nothing** (this FastAPI keeps routers nested behind `_IncludedRouter`),
so it passed by checking zero routes until it was made to assert that it had found some.

**`/health` was invented rather than copied.** Ours: `{"status": "HEALTHY", "checks": [{service,
healthy: bool, tags}]}`. The predecessor's: `{"status": "Healthy", "total_time_taken", "entities":
[{service, status: str, time_taken, tags}]}`. Different key, field names, type and casing — a typed
client cannot deserialise it, on the endpoint monitoring reads to decide whether to page somebody.
`time_taken` needed a real number: the probe had **measured** each duration all along and spent it
on a formatted string inside `detail`, so it now carries `took_seconds` and `None` where nothing was
asked. An unprobed adapter reports 0.0 and keeps its `not-probed` tag, because the number cannot
carry "we did not look" and the tag can.

**`INVALID_TOKEN` was declared and raised by nothing**, so a rejected credential and an absent one
both answered `NOT_AUTHENTICATED` — a security signal and a deployment slip in one bucket. The bit
travels on the request, not on the error: the alternative is Google's shared refusal type carrying
KIRA's vocabulary, which lasts until the third surface.

**Two text parts of one message are joined with a newline**, as the predecessor joins them. We
passed them through separately, so each dialect rendered them its way — `HalloWelt` on one provider,
two parts on another, the predecessor's `Hallo\nWelt` on neither. No error, a 200, an answer to a
subtly different prompt. Only *runs* of text merge, so an attachment keeps the prose around it in
place.

**Then the `google-genai` SDK was pointed at the app for the first time, and it found two things
within a minute.** Every other test of this surface was written by whoever wrote the surface, so it
agrees by construction; the SDK is the one participant here that never agreed to anything.
`client.models.embed_content(...)` was **refused outright** — it posts to `:batchEmbedContents` with
the model *inside each entry* (in Google's resource form, `models/mock-1`), and `model` had been
declared one level up, on the batch wrapper, by somebody who anticipated the field and guessed the
level. The whole verb was unusable from the official client. It is carried now, never honoured as an
override — the URL chose the model and the controls ran against that name — and a **disagreement is
refused by name** rather than dropped. And every streamed chunk carried `finishReason: ""`, which
the SDK answers with `UserWarning: '' is not a valid FinishReason`, once per chunk: a hundred lines
of complaint per answer, nothing broken, invisible to every test here because our own clients are
dicts and a dict has no opinion about an enum. Omitted now, as Google omits it; the SDK case turns
warnings into failures, because a client's log is part of what we hand somebody.

**One concern of mine measured negative and is recorded as such.** `resolve_direct_target` sits
between the reservation and `hold`, so a refusal there looked like it would leak budget. Two
attempts to demonstrate it failed *as instruments* — the first read Postgres, where a Redis
reservation is invisible, and passed with the release deliberately switched off; the second refused
at model lookup, before anything was reserved, so it never reached the path it was named after.
Measured live afterwards: a refused streaming request for an unreleased model creates **no counter
at all**. There is nothing to fix, the test was deleted rather than shipped green, and the useful
part is that both false starts were the same failure — a case that passes without touching what it
claims to be about.

`QA18`–`QA23`; **399 properties**. `google-genai` joins the dev dependencies.

---

## 2026-08-12 — A developer round against the KIRA surface, and the clock nobody had looked at

Prompted by a fair complaint: several checks in a row had failed to notice that
`/streaming-chat` did not stream. Worth naming the mechanism, because it is more instructive than
the defect. Three layers agreed the surface was correct — the docstring described "exactly one
`completed`" as the design, a hermetic test asserted exactly that, and a live probe **counted**
the events and got a plausible number. None of them can feel what a client feels. An SSE response
that arrives entirely at the end is indistinguishable from one that arrives progressively
**unless you look at when the pieces arrive**, and nothing looked. My first probe did report
`events: 0`; I attributed it to my own instrument (right about `data:` vs `event:`) and dismissed
the count (wrong).

**The regression test is the artefact.** `test_streams_actually_stream.py` asserts the *spread of
arrival times* against a provider double that yields on a clock, parametrised over both surfaces.
Proved by reverting the loop to the assemble-then-send shape, keeping the **event count
identical**: the new case fails, and **157 existing kira/stream tests stay green**. That number is
the finding.

**The harness had to change with the question, and that is the other half.** Written first through
`TestClient`, the case failed on *both* surfaces — including the one measured live at a 4.3 s
spread minutes earlier. `TestClient` collects the whole body before the caller sees a line (the
trap `CLAUDE.md` already records for the disconnect tests), so through it every stream looks like
a block. The app is driven as the ASGI application it is now, stamping each `http.response.body`
as it is handed over. What a buffering client does afterwards is the client's business.

**Audit parity, found by looking in the database rather than at responses.** Malformed JSON from a
valid credential left **a row on Gemini and nothing on KIRA**. `_refused` records only when
attribution is set — deliberately, since a request refused before the credential was judged has
nobody to attribute to (`FRD-122` §2) — and Gemini resolves attribution as a router dependency
while KIRA resolved it *inside* the route, after parsing. Anything the parse rejected fell into the
gap. Attribution never needed the body; it reads the header and the principal. Moved ahead of the
parse on all three handlers, with `test_surfaces_record_refusals_alike.py` parametrised over both
surfaces — and pinning the deliberate exception, that an unauthenticated request still leaves no
row.

**A module claiming a rule the code did not have.** `api/kira/schemas.py` said every field
accepted both spellings, and five fields carried an `alias=` that restated their own name — which
reads like a second spelling and is not one. Nothing behaved wrongly: `FRD-107` FR-2 names
`maxTokens` and `responseSchema` as the only camelCase fields, and those two are right. But a
reader asking whether `conversationHistory` was accepted would have been told yes by the module and
no by the server. The redundant aliases are gone, and the claim is now **tested in both
directions** — the two spellings are accepted, no third one is — after four earlier instances of a
hand-written list agreeing with the constants one way only. A dead `DataPart` class went with it:
nothing built one, and its `extra="forbid"` on `mime_type` alone described a surface *stricter*
than the one that runs, which takes `mimeType` too.

**Verified live, and these all held**: 83 updates then one terminal event carrying the identical
joined text plus usage (first at 0.37 s, last at 3.13 s); an injection **blocked before the stream
opened**, so the client gets a 400 rather than a stream that stops, audited with its decision;
`responseSchema` on a stream; a list of texts embedding to cosine **1.000000** against the joined
single, which is `FRD-113` §11's answer confirmed on the wire; six concurrent requests → six
served, six rows, the counter at exactly six; a mid-stream hang-up → `499`/`client_gone` with the
reservation released rather than leaked. Ten shapes a careless client sends, none a 500, each
naming its problem.

**One finding left open on purpose.** A `client_gone` row carries no tokens and no cost, and the
report does not count it under `unpriced_requests` — that counter means "served on a model with no
price", and the comment beside it reasons "nothing was spent, because nothing ran". For a dropped
stream that is false: the upstream *was* producing, and its usage simply never arrived. So the
spend figure is quietly short by whatever hung-up requests consumed, with no caveat. Folding it
into `unpriced_requests` would be wrong for the same reason folding refusals in was wrong — two
different unknowns — so this wants its own answer rather than a quick one. Recorded, not built.

`QA15`–`QA17`; **393 properties**.

---

## 2026-08-11 — A code-quality and security review, and the verb nobody had pointed the controls at

A read of the whole codebase for quality and safety, with the brief that **no functionality
changes**. Three defects, and the first one makes that brief interesting: fixing it *removes*
answers the gateway was giving. They were answers it was never supposed to give.

**1. `:streamGenerateContent` asked none of the dispatch conditions.** The non-streaming branch
goes through `dispatch_with_fallback`, which is where `ADR-0012` §3's conditions live; the
streaming branch called `provider.stream_generate(...)` directly. Measured against the hermetic
app — each of these refused by name on `:generateContent` and **served with a 200** on the
streaming verb:

    a model no Global Administrator approved (`FRD-307`)      → 200, served
    a model the use case was never released (`FRD-308`)       → 200, served

Residency (`FRD-115`), media types (`FRD-110`), tools, thinking and schemas travel through that
same mechanism, so all of them were bypassed with it — on the verb **every chat client and every
coding assistant uses**. The `:embedContent` bypass of `FRD-405`, one verb over, and the same
lesson: *a control belongs on the path every branch takes, not inside one of them.*

Its second half is about evidence rather than authorisation. The adapter was resolved from the
model the **caller named**, before the pipeline had run — so a `model_route` step re-targeting a
request sent it to the first model's machine under the second model's name. Measured: an answer
produced by server A, recorded and priced as having come from server B, which is exactly the claim
`FRD-115` exists to make checkable.

`serving.resolve_stream_target` now asks the same `permits` the chain asks and resolves the adapter
from the routed model, **before** the response exists, so the refusal carries a status the caller
can read. Deliberately **conditions only, no chain**: a stream still cannot fall back, because once
the first chunk is on the wire the status is 200 and the answer has begun. That remaining gap is
recorded rather than closed — a fallback for streams is a feature, not a repair.

**2. A `throttle` suspension was a 500.** `SuspensionService.check` returns `Throttle`;
`RateLimitService.check` consumes `BucketRequest`; `guard_before_work` passed the first straight
into the second, and they share no field the limiter reads. Every request from a throttled caller
raised `AttributeError` — while the console showed the decision as active and enforcing.
`FRD-125`'s badge-wearing absent control, in the incident-response half.

**Nothing could see it.** mypy could not: the two services meet through `app.state`, which is
untyped, so all 76 calls across that seam are unchecked. The tests could not either, and that is
the instructive half — `test_suspensions.py` asserts the throttle carries `limit_rpm == 5` and
**stops exactly at the seam**. Two correct halves and no wire, after `record_to_outbox`, the
missing Kafka topics, `payload_size` and the unannounced catalog. The repair is `per_minute()`,
one reading of "n requests per minute as a bucket" now shared by all three callers (a configured
limit, the failed-authentication bound, a throttle), plus **annotated locals** at the gate — the
idiom `enforce_pre_dispatch` already uses one function above, with the comment saying why.

**3. The two failure responses that answer without reaching a route carried no security headers.**
A 413 refused on its declared size, and a 500. No `nosniff`, no `no-store`, no trace id — on
exactly the pair `TraceIdMiddleware` names in its own docstring as its reason for existing
(*"the requests that most need correlating are the ones that went wrong"*). Two causes: the body
limit was mounted *outside* the header middleware (the ordering argument for that was sound and
the conclusion wrong — the ceiling wraps `receive`, and nothing above it calls `receive`), and the
500 is written past the entire user middleware stack by `ServerErrorMiddleware` and can only be
headered by the handler itself.

**Also removed: `realm_roles()`**, unreachable since `ADR-0017` (2026-08-09) made group membership
the single source of a role. Only its own tests still called it, and they asserted on
`use-case-user` — a role abolished the day before. An unreachable helper is a rule the code claims
and does not have, and its four tests reported green about nothing.

### Then the class, not the instances

Fixing three defects one at a time is what produces a fourth, so both root causes got a structural
answer — and **each one found something within minutes of being written**, which is the only
evidence that a guard is worth having.

**`test_every_dispatch_applies_the_conditions.py`** parses the source, finds every call that can
reach a model, and requires it to sit inside `dispatch_with_fallback` or `resolve_direct_target` —
or on a written list with a reason. Two findings on its first run:

- **it caught the fix above.** `resolve_stream_target` was called in `_generate` and the dispatch
  happened in a closure inside `_stream_response`, so the check and the call were in different
  functions and only a human knew they belonged together. `_stream_response` resolves its own
  adapter now: getting something to call and being allowed to call it are one act.
- **`:embedContent` had the identical hole**, and nobody had looked. An embedding has no chain
  either — a vector from a second model is not a substitute for the first — so nobody wrote one,
  and the conditions went with it. Measured: an unapproved and an unreleased model, both **200**,
  on both surfaces. The literal `:embedContent` bypass for the **third** time in this codebase's
  history, which is what makes it a class rather than a mistake.

The guard that already existed is the sharpest part of this. `test_every_model_call_is_accounted`
parses the same call sites and already **names** `api/gemini/routes.py:stream_generate`, with a
justification vouching for attribution, billing and recording — all true. Nobody had asked whether
the candidate was *allowed*. **A list is only as good as the property it is a list about.**

**`aira_gateway/state.py` + `test_app_state_is_typed.py`** close the second cause. Every service
read off `app.state` is now annotated or goes through an accessor, and the effect was verified
rather than assumed: reintroducing the throttle defect now fails the **build**, before a test runs —

    error: Argument "extra" to "check" of "RateLimitService" has incompatible type
    "list[Throttle]"; expected "Sequence[BucketRequest]"

The idiom was already here, applied twice (`registry_of`, `catalog_of`) with a comment saying
exactly why; what was missing was finishing the thought across the other eighteen attributes. The
guard's first run found the one service read left unannotated.

`QA1`–`QA9`. `QA1` reported **STALE** an hour after being written, because generalising the helper
from streams to every chainless verb moved its anchor — `N2`'s lesson working as built, and a
reminder that a mutation defending moved code reports green about nothing.

### Three smaller things, and one assumption made checkable

- **`stop()` could hang on the promise it makes.** `RequestLogWriter.stop()` says *"a redeploy must
  not discard pending audit rows"* and kept that by waiting on `_queue.join()` — which returns only
  when the worker marks every entry done. If the worker is gone, that waits for a signal nobody
  will send: shutdown hangs, the orchestrator sends `SIGKILL`, and the whole queue is discarded by
  the call whose purpose is not to discard it. **The failure mode is the exact inverse of the
  guarantee.** The drain now races the worker, and what is still queued is written here instead.
  Both new cases *hung* against the old code rather than failing, so they carry a timeout — a test
  that hangs reports nothing at all.
- **`VaultClient` never closed the pool it opened.** Small and lasting: secrets are read once at
  startup, so the socket is used twice and held forever. Closed in a `finally`, so a failed login
  releases it too — a process on its way down should not be the one holding an open connection to
  a secret store.
- **Twenty `assert`s in production code, and no check that they survive.** `python -O` removes all
  of them, including the one whose own comment says it is what keeps two people's budget counters
  apart. No image sets it today — which is the point: that is an *assumption*, and
  `tools/tests/test_assertions_are_not_optimised_away.py` makes it a checked one, so switching the
  optimiser on becomes a decision somebody has to take deliberately rather than a base image doing
  it quietly. Deliberately **not** a rewrite of the twenty: turning type narrowing into `if …
  raise` adds branches no test can reach and no reader benefits from.

**Reported, not changed** — each would alter behaviour, and this pass was asked not to:

- a **stream has no fallback chain**. `prepared.fallbacks` is ignored on that path, so a use case
  with a chain gets one everywhere except the verb its clients use. Now that the conditions are
  applied, an unqualified primary is *refused* where a chain would have moved on. Closing it is a
  feature.
- the **KIRA surface's 500 answers in the AIRA envelope**, while its routing and validation errors
  correctly answer in KIRA's. Consistent with neither, and changing a response body is a contract
  change.

### The demo, rebuilt from nothing — and the three things that only that finds

Ahead of a stakeholder walkthrough, the whole stack was destroyed (app images deleted, Postgres
and Kafka volumes removed; the model weights kept, because tomorrow runs on this machine) and
`make showcase` was run from empty. Three defects, none of which any suite could have reported.

**1. The console showed a blank page when Keycloak was down.** `AuthService.init()` runs in an app
initialiser, and a rejected initialiser makes `bootstrapApplication` reject — so an unreachable
identity provider produced a **completely white page** with a `200` from the web server. A reader
cannot tell *"the login service is down"* from *"this application is broken"*, and they report the
second: it cost an afternoon when the stack's infrastructure crashed. The failure is now recorded
rather than thrown, the shell renders a panel **naming the issuer** (a *misdirected* console fails
identically to an unreachable one, and the two need different people), and the guard no longer
starts a login it knows cannot complete — that navigation would take away the only explanation the
console can still give.

**2. The demo was breaking the governance rule it exists to demonstrate.** The first clean run
reported **"served 9, refused 2"** where its own record says ten and one. The extra refusal was an
embedding batch sent as `entwicklung` — the use case the seed deliberately narrows to the chat
model, to show `FRD-308`. It had been wrong for as long as both existed, and was invisible because
`:embedContent` reached the provider **without the release being consulted at all**. Applying the
control made the seed's own contradiction the first thing a walkthrough would have seen. Fixed in
the *traffic*, not the seed: releasing everything to `entwicklung` would also have made it green
and would have deleted the decision the use case exists to show — a fix that removes the feature
it was protecting. `tools/tests/test_the_demo_asks_only_what_it_allows.py` now reads both files and
fails if they disagree again, because a stakeholder walkthrough is the worst place to find out.

**3. A `500` on the first screen a walkthrough opens.** Clicking any use case as `admin` returned
`500` from `/v1beta/usage/<slug>`: `BudgetService.usage()` passed **the reader** as the caller for
every budget row, so a `member` row naming anybody else resolved to no scope and `_scope_key`'s
assertion fired. A regression from the per-head scope work earlier the same day (`744337f`), and
the distinction it missed is the whole point of that feature: `each_member` is one counter *per
reader* and depends on who is asking; a `member` row **names its own subject** and does not. The
assertion was right to exist — it is an invariant of the *reservation* path — and asking a reading
question in the reservation's vocabulary is what turned a legitimate view into a 500.

After the fixes, from an empty machine: **`served 10, refused 1, failed 0`**, all five demo roles
log in with role-correct navigation and **no console errors**, and all ten screens — including
every use-case detail — render clean.

### Two more, from running the integration suite against a real database

The hermetic suite runs on SQLite, and **SQLite enforces neither NUL bytes nor column lengths**.
Both of these are therefore invisible to it, and both are reachable by anyone who can send a
request — the model name and the method come straight out of the URL.

    POST /v1beta/models/mock-1%00:generateContent        → psycopg.DataError → **500**
    POST /v1beta/models/aaa…(300 chars):generateContent  → the refusal's audit row is **lost**

The first breaks the rule the 174-edge-case round established: a caller's mistake is answered with
an actionable status, never with our error. The second is quieter and worse — the request *was*
correctly refused with a 404, and the row recording that refusal failed to insert, so `FRD-122`'s
*"the log records what was asked"* was broken by the very row meant to satisfy it.

Two fixes, because they are two problems. `catalog.is_lookupable` refuses the **lookup**: a name no
column can hold cannot name a declared model, so there is nothing to ask the database — and
answering before the query stops the reply depending on which database is behind it.
`persistence.service._fits` bounds the **row**: control characters removed, then cut to the column,
in that order, so the width is counted in characters that will survive. Sanitising rather than
refusing, because a slightly less faithful row is worth incomparably more than no row.

**The first fix did not reach the second problem, and only a measurement found that.** Refusing the
lookup stopped the 500 — and the audit row still carried the NUL into `request_logs.model`, so the
refusal stayed unrecorded. Verified against the running stack, before and after:
`request_log_write_failed` in the log, then the row present with `outcome = model_not_found`.
`operation` is bounded too: `String(64)`, also chosen by the caller, and a bound applied to one
half of `model:method` and not the other is the same defect with a different column name.

**The other thirteen integration failures are environment, not regression**, and saying which is
which is the point: the suite expects a stack whose OpenAI server is named `gpu-b` (this one names
it `local`), whose `qwen2.5:3b` is pulled (it is not here), and which predates two decisions made
earlier the same day — `AIRA_REQUIRE_USE_CASE` defaulting to true, and `FRD-308` requiring an
explicit release. A test asserting a release refusal against a stack where the **mock is
registered** cannot pass either: the mock is a declared test double and is exempt by design.

### And the one that would have hit every stakeholder in the room

A final browser pass over the freshly seeded stack — the state a demonstration actually starts
from — returned **`500` on `/api/v1/use-cases/?page=1`**, the first screen after logging in:

    django.db.utils.IntegrityError: duplicate key value violates unique constraint
    "api_oidcidentity_subject_key"

**The first request from a new person is more than one request.** The console loads `/api/v1/me`
and `/api/v1/use-cases/` at the same moment, so two requests carrying the same brand-new `sub`
arrive together: both find no identity, both create one, and the second loses. It is therefore a
**500 on every user's first login**, and it had been there since `ADR-0007` bound users to the
`sub`. Nothing had caught it because every earlier walkthrough logged in as somebody the previous
walkthrough had already provisioned — the defect is invisible from the second login onward.

`transaction.atomic` never prevented it and was never going to: it makes each attempt atomic, not
exclusive, and the two attempts are on different connections. The fix is the one the race calls
for — **lose gracefully**: whoever arrives second re-reads and uses the row the winner wrote, in
its own savepoint so the failed INSERT does not poison the surrounding transaction. A genuine
constraint failure still raises, because inventing a second account for one person is how an audit
trail comes to name two people who are one.

Verified by deleting every `OidcIdentity` row — which is precisely "nobody has logged in yet" —
and logging in as all five demo roles: **clean, every one**.

### The question that had not been asked: no model downloaded

Asked directly, and the honest answer was no — both from-scratch runs above kept the model volume
deliberately, so the one step never exercised was the **pull**. Run for real, from a machine with
no volumes, no images and no weights: **`served 0, refused 11`**, every one a 401.

**The seed died when no model was present.** Ollama answers `{"data": null}` while it serves
nothing — the state on a first run, mid-pull — and `payload.get("data", [])` defaults only when the
key is *absent*, so `_served_models` raised `TypeError: 'NoneType' object is not iterable` and took
the whole seed with it: no accounts, no use cases, no keys. What it broke is the mechanism written
to answer *"which models are really there"* with **evidence instead of ordering**, and it crashed
in the single case that mechanism exists for. *Absent and empty are different answers* — here,
between absent and **null**.

**And `make showcase` waited for the wrong thing.** `wait-healthy` checks four HTTP endpoints;
none says anything about the seed, and the seed waits on the pull. Then it slept six seconds "for
the read model to catch up", which is not a statement about anything. With the model already
present the pull returns at once and everything fits inside those six seconds — so it worked for
everyone who had run it before and broke for exactly the person the target is for. The **fourth**
instance of that shape here, after a pulled image, a Vault path and a Keycloak realm.

The repair waits for the pull, seeds again against what the pull produced, and then waits on the
condition the traffic is about to test. **The first version of that condition was wrong in an
instructive way**: it asked whether the model appears in `/v1beta/models`, which reports what the
*registry* serves — populated from startup whether or not anybody catalogued anything. It went
green and the traffic answered `400 not in the model catalog` eleven times, with the catalog
arriving over Kafka seconds later. It asks `airaDeclared` now.

Three destroy-and-rebuild runs, the last with no weights at all: **`served 10, refused 1, failed
0`**, zero error lines.

### The compatibility surface, walked for the first time

Asked whether the KIRA surface had been checked for stability. Hermetically yes; **live, not
once** — the showcase drives only the Gemini surface, so the compatibility layer had received no
real request all session, including after its embedding path was changed. Fourteen cases against
the running gateway.

Most of it holds: `/models`, `/health`, `/chat`, `/streaming-chat` and `/embed` (single *and*
list) answer correctly, and every refusal carries this surface's envelope with the predecessor's
codes — `MODEL_NOT_FOUND`, `NO_CHAT_CAPABILITIES`, `NO_EMBEDDING_CAPABILITIES`,
`INVALID_JSON_BODY`, `VALIDATION_ERROR`.

**Two apparent findings were the instrument, not the system**: the probe read `response` where the
contract says `parts`, and counted `event:` where this format uses `data:`. The answers were there
throughout. Worth recording because it is the same trap this project has hit before — the round
that looks like a defect is sometimes the measurement.

**One was real.** A request with no credential answered `401` in **Google's** envelope, on the
surface whose entire premise is that a client migrates by changing a URL — and `401` is among the
most commonly handled statuses a client has. The routes catch their own refusals and render them
correctly; what escaped is what a *dependency* raised before the route ran. The body ceiling had
the same shape: it answers in pure ASGI with a hardcoded body, and already knew the path, because
`describe_target` reads that same prefix to put `api` on the audit row.

The sharpest part is the vocabulary: `NOT_AUTHENTICATED` and `INVALID_TOKEN` have been declared in
`kira/errors.py` from the start and **nothing emitted either**, while the real refusal went out in
a foreign shape. A code defined and never raised is *"an enum member is not a specification"* seen
from the other side.

The Gemini surface is asserted alongside in the same file, deliberately: a fix that gave **both**
surfaces the KIRA envelope would satisfy every assertion anybody thought to write about the
surface being repaired, and quietly break the other contract. `QA10`, `QA11`.

### The predecessor, compared against its source rather than its document

A KIRA↔AIRA comparison read from the predecessor's **own code** produced ten differences. Six are
decisions and are now written into `FRD-107` §5.5, as that section's own rule demands — *a
compatibility surface with undocumented differences is worse than no compatibility surface, because
it is trusted*. Four were not decisions.

**A list of texts is one embedding, not many.** `FRD-113` §11 recorded exactly this as an open
question, wrote both readings down, assumed *one vector per text*, and asked for confirmation
against the running predecessor — then made the assumption **visible on the wire** under a distinct
`vectors` key so that whoever checked would notice rather than have to dig. The check happened and
the assumption was wrong: the predecessor sends the texts as several **parts of one** call and
answers the documented singular `vector`. The consequence was the worst kind available to a
compatibility layer — **different data, not an error**: five chunks in, five vectors out, where the
predecessor gives one. What the joining *means* was measured the same day (`gemini-embedding-001`,
cosine 1.000000 to the concatenation and 0.9489 to the mean): the provider concatenates, it does
not build a centroid. `BatchEmbeddingResponse` is gone; the key did its job and its job is over.

**`/streaming-chat` did not stream.** It called the non-streaming dispatch, waited for the whole
answer and sent one terminal event — SSE as a costume. `FRD-111` §5.4 is why, and it was half
right: it refused to invent `update` events *carrying no model output*, which remains correct, and
that is a different question from whether output should arrive progressively. The two were answered
as one. A chat client saw a blank view for the length of the answer and then all of it at once, and
could not tell *"still thinking"* from *"the connection died"*. Now every chunk is an `update`
carrying real text and `completed` still carries the whole answer — measured live: **38 updates,
one terminal event, and the two agree**. The cost is stated rather than discovered: a stream cannot
fall back, so the conditions are checked before the response exists (`resolve_direct_target`), the
same trade the Gemini surface makes on the same verb.

**`/health` could not fail.** It reported one hardcoded `true`. The comment there said so and named
`FRD-117` §5.2's cached probe as the fix *"until then"* — and that shipped long ago, with `/readyz`
reading it ever since. It now reports one check per upstream from the same cached verdict, still
with no I/O per call, `probed: false` surfaced as a tag rather than folded into the boolean, and
**503** when anything is unhealthy. A health check a monitor believes and that cannot go red is
worse than none.

**Malformed JSON answers 422**, as the predecessor does. `400` is the better answer about HTTP —
`422` means well-formed but wrong — and the predecessor's `422` is an artefact of its framework
rather than a decision. Being right about semantics on a compatibility layer means being wrong
about what the layer is for. `INVALID_JSON_BODY` went with it: a code nothing raises is the defect
this file removed twice in two days.

Two things caught the work rather than the other way round. **The structural guard written this
morning** reported the new `stream_generate` call site immediately — a model call on no list, which
is precisely what it counts. And a standing test caught a regression the rewrite introduced:
`check_structured_result` was lost, and the property had to *change* rather than be restored, since
the status cannot change once events are out — the terminal event is **withheld** instead, so a
client waiting for `completed` never treats half a document as whole.

The most instructive artefact is a test that had to be rewritten: `test_a_batch_returns_one_vector_
per_text_in_order` pinned the disproven assumption. It was **faithful to the code and unfaithful to
the contract**, which is all a test can ever prove — that the two agree. `QA12`–`QA14`.

### A broad sweep of the console's endpoints

Twelve reading endpoints, the dry run and both surfaces, against the running stack: **no 5xx**, and
the 403s correct (an API key carries no oversight role, and those endpoints are bounded by one).

One finding, and the function convicts itself. A dry run of `kundenservice` with an injection
filter reported **`effective_model: all-minilm`** — the *embedding* model. Where a pipeline names no
model of its own, which is the commonest case, the last resort was `released[0]`, and that is
alphabetical: a use case released both an embedding and a chat model gets the embedding one. The
paragraph directly above that line calls the previous fallback *"a guess that is guaranteed
wrong"*, and this was the same guess in a second costume — a pipeline is about a request that
**generates**, and an embedding model can never serve one. The builder reads that field to know
what it is testing against.

Now the first released model that can generate, falling back to the old answer when none can: a
wrongly named model is still more use to a builder than a refusal to run, and the release check
downstream says its own piece anyway.

### Where it was left

`make showcase` from a destroyed stack, twice: **`served 10, refused 1, failed 0`**, zero error
lines. Five roles, first login and after, across every screen: **no console errors, no 5xx**. Five
use cases and no test residue. The unit suite, the frontend suite (733), ruff, mypy and the
mutation harness all green.

One test was repaired rather than its subject: `test_a_dry_run_records_and_bills_what_it_spent`
read `request_logs` immediately after the response, and `FRD-405` moved that write **off** the
request path — so it won a race it was never guaranteed to win, and lost it the first time the
machine was busy with a container build. *A test that passes only on an idle machine reports on
the machine.*

**And the first repair for it was worse than the defect**, which is worth writing down because it
is the same shape as everything above. `log_writer.drain()` looks exactly right — it is the method
that exists for this — and `TestClient` runs the application in its **own event loop on another
thread**, so awaiting that queue from the test's loop waits for a wake-up that cannot arrive. A
flake became a **hang**: the suite sat at 58 % for forty minutes, and a hang reports nothing at
all, where a flake at least reports something. `log_queue_size=0` — writing on the request path,
which is what the rest of the suite does for cases that read the row — is deterministic and was
run three times to say so.

`QA1`–`QA6`, each observed to fail before its fix was written. One existing test needed a change
and it is not a weakening: `test_hardening.py`'s failing stream stand-in was not marked
`is_test_double`, which it had got away with **because the streaming verb asked no conditions at
all** — the omission could not show while the hole was open.

Nothing else about what the platform does changed.

---

## 2026-08-11 — A staircase, and the three guards that could not see it

Reported from the running console: on a use case's **Members** tab, "Grant access to" and "As" do
not sit on one line. Measured before touching anything — the search input's top at **435**, the
role select and the Grant button at **478**: a **43 px** staircase.

The cause is `FRD-207`'s, exactly: the search field carries a hint under its input, so that field
grew, and with `align-items: flex-end` its control was pushed up while its neighbour's stayed at
the bottom. `.form-inline` had been fixed for this and carries the comment explaining it —
*bottom alignment looks right only while every field is equally tall* — and `.filter-row`, a second
container with the same job, was never brought along. Top alignment plus a fixed label height, the
same treatment; the `padding-bottom` that kept a bare checkbox on the control line becomes a
`margin-top`, because the direction of that correction flips with the alignment.

**Three layers of guard failed to see it, each in a different way, and that is the finding.**

1. `expectFormControlsAligned` only queried `form.form-inline`. The access panel's row is a
   `div.filter-row`, so the case named **`'grant access'`** has been asserting alignment on a
   container it never selected.
2. It grouped controls into **40 px bands** to tolerate wrapping — and a staircase *taller than the
   band* reads as two separate rows. That let 12 px through once before; 43 px sails past it. A
   line is now a set of flex items whose boxes **overlap vertically**, which is what a flex line
   actually is: items on one line always overlap however their inner controls are aligned.
3. It ran straight after `goto` with nothing to wait for, so on a page that had not rendered yet it
   compared nothing and passed. It now waits for a row and **insists it found something to
   compare** — which immediately exposed that `'create use case'` was pointed at a list page with
   no form on it at all, and that the create form is a `stack` anyway, one control per line, where
   no staircase is possible. That assertion is gone rather than repaired.

Each of the three was shown to fire before being trusted: the old CSS restored, the guard run, the
failure read, the CSS put back.

---

## 2026-08-11 — Every model call belongs to somebody

Asked directly after the dry-run finding: _"stell sicher, dass jeder Modellaufruf einem Use Case
oder einem Key von Use Case gehört. Es darf nicht nicht angerechnet werden und es darf keine
undokumentierten Requests geben."_ Three things came out of it.

**The dry run now records what it spent.** `ADR-0013`'s promise is that a model call is auditable,
and the word "dry" describes the **dispatch** that does not happen — never the classifier that
does. Its calls leave the same `pipeline:<step>` rows a served request's do, booked with
`requests=0` (`FRD-125b`), written in a `finally` because a filter that blocked still spent the
tokens it took to decide that. `_injection_verdict` went with it: once `dry_run` needed the
`ModelCall`, the wrapper that threw it away had no callers, and an unreachable helper is a rule the
code claims and does not have.

**Then the inventory, and it found a second hole — measured, not read.** An authenticated caller
who belongs to **no use case** and names none was served: **200, 200 tokens, `use_case = NULL`.**
Charged to no budget, bounded by no use-case rate limit, and outside the model release entirely,
because there was no use case to consult it for. The row existed, so this was never literally
undocumented — it belonged to *nobody*. Both surfaces already had the rule written and switched off
behind a default, the KIRA one in its own words: _an unattributed request would bypass every budget
and limit._ `AIRA_REQUIRE_USE_CASE` now defaults to **true**, and turning it off outside
`local`/demo refuses to start — `ADR-0015`'s shape, because a convenience default is a production
default one variable away.

The **unbound break-glass key** keeps its exemption and it is the only one: a credential that needs
a use case *from Management* is no use when Management is what is broken, and its row still carries
the key prefix and the subject. `must_name_a_use_case` is one function both surfaces read, because
`FRD-126`'s lesson is that a rule restated on a second surface is a rule that differs on one of
them — which is exactly how the KIRA surface once read an empty membership list as "anything goes".

**And the artefact that makes the set complete rather than the answers correct.** The hole was not a
wrong answer anywhere; it was a call site nobody had counted. So
`test_every_model_call_is_accounted.py` parses the source, finds every call that can reach a
provider, and requires each to be on a list with a written justification — the same argument
`test_every_route_is_guarded` makes about authentication. Six sites, five entries. `ping` is
deliberately outside the billable set and a test asserts no adapter's probe ever reaches a
generating verb, because a readiness check that bills somebody is exactly the free unattributed
call this file exists to rule out. Shown to fail by adding a call site. `J12`–`J15`.

**Verifying that live found a third thing, silently wrong for longer than either.** Since `FRD-507`
stage B a model can be reachable *because it is catalogued*, and the pipeline engine still looked
its classifier up **by name alone** — so on such a deployment, which is the ordinary shape of a
Google AI Studio setup, it found no provider and quietly did less: an LLM injection filter fell
back to the heuristic, a router routed nowhere, both with the builder showing them active. That is
`FRD-125`'s defect arriving through a different door. The resolver is passed in per request now,
exactly as `dispatch_with_fallback` takes `provider_of`.

With the classifier finally reached, the provider answered **400**: `gemini-flash-latest` refuses
`thinkingBudget: 0`, which the classifier sends unconditionally because it still bypasses the
catalog's thinking resolution — `FRD-125`'s own unfinished half. And the failure was swallowed:
`(None, None)`, no model call, no row, and a dry-run trace saying `unchanged`, **the same word a
working router uses when nothing matched**. It says `not_asked` now, on the audit row and on the
screen an operator uses to find out whether their pipeline works. The thinking mismatch itself is
then fixed on the same day, and the measurement moved the fix a layer down.

What Google refuses is `thinkingBudget: 0` — with a 16-token cap, with a 512-token cap and alone,
**400 every time**; drop the parameter and the same model answers in one output token. Two things
produced it: the classifier sent an off unconditionally, bypassing the catalog, and `resolve()`
produced one for **any** model declaring no thinking — contradicting its own docstring, which says
`None` means *the model was never going to think and no parameter is needed*. Both corrected.
`FRD-124`'s "off has to be said out loud" is about a model that **can** think, where silence lets
the default win; where there is nothing to switch off, asserting an off is a claim about the
provider's API. The engine's resolver became `declaration_of` rather than `provider_of`, because a
step needs two facts from the same place and asking twice is how they come to disagree.

Then the **allowance** turned out to be the other half of the same defect: a provider bills
thinking *inside* `maxOutputTokens`, so a model that must think returns nothing in a cap sized for
one word. Measured, routing one sentence: 16 → 13 thought tokens and no answer, 32 → 28 and no
answer, **64 → `code`**, 128 and 256 likewise. Two numbers now, four times the floor — and a
**ceiling is not a purchase**: a model that answers in a word is billed for a word, so being
generous costs nothing while being tight makes every classification silently undetermined.

Verified live: the router returned `category: "code"` and the filter `verdict: injection`, both
with rows and prices. `gemini-flash-latest` stays **undeclared** for thinking deliberately — the
measurement says it refuses an off, not which modes it offers, and `FRD-114` FR-7 is that an
unmeasured model is declared with none.

**A lesson about measuring, not about the code.** One repeat round showed every answer empty and
looked like model non-determinism; it was `429 quota exceeded` bodies being read as empty text,
because that script did not check the status code. The instrument was the finding. `J16`–`J22`.

**And the browser suite caught the regression the change itself introduced.** `require_attribution`
is mounted on the whole Gemini surface, so the new requirement reached `GET /v1beta/models` too and
the console's "which models does the gateway serve" started answering 400 to a Global
Administrator, who is a member of nothing by design. The requirement exists to attribute **spend**;
a listing has nothing to attribute. Only the layer that drives that button could see it. `J18`.

---

## 2026-08-11 — A use case reaches the models somebody gave it (`FRD-308`)

The ask: _"wir beschränken Use Cases nicht auf die Modelle … entweder globale admin oder Use Case
admin für einen Use Case erlaubte Modelle freigeben."_ Half of it existed and did not work.

**Measured before it was replaced.** The `allow_check` pipeline step refuses a model outside its
list — and only the one the *caller* named:

```
caller names a forbidden model      → 403   ✅
a model_route step re-targets one   → 200, served
a fallback_models chain reaches one → 200, served
```

It runs **once, before routing**. `requirements.py` had written the rule down in its own docstring
already — _the check that runs before routing protects nothing_ — and attachments, tools, thinking,
schemas and residency are all checked per hop for exactly that reason. The model allow-list was the
one control that was not, and it was also opt-in, buried in the graph editor, and invisible to
anybody who did not open it.

So a release is now a property of the use case, enforced as a dispatch requirement beside
`ModelApproved`. Two gates, two owners: a **Global Administrator** decides what may be used in this
installation at all (`FRD-307`), an **administrator of that use case** which of those it reaches —
`may_admin`, not `may_manage`, because releasing a model changes what a use case *is*.

**Empty means none**, chosen by the owner over the alternative and knowing the consequence: every
existing use case without an `allow_check` step stops until somebody releases a model. What follows
is that the refusal has to be worth reading — it names the model, the use case and who can act —
that the console leads with the empty state instead of a blank list, and that the demo seed
releases explicitly, because a showcase refusing its own ten requests would teach the rule
backwards.

**Three states, not two.** `None` is *no event has said*; `[]` is *somebody released nothing*; a
list is exactly those. The first would be easy to fold into the second and would stop **every** use
case on a partially upgraded stack, whose Management sends no such field — a governance control
arriving as an outage, which `FRD-500` recorded as how a control gets switched off for good. The
same split `FRD-307` made for `approved`.

**A relation in Management, a JSON list in the gateway**, because the two planes ask different
questions: _which use cases would break if I retire this model_ wants a relation (and cleans up on
delete), while _may this use case call that model_ is one row the gateway already fetches — and a
containment query over JSON is written differently on SQLite and Postgres, which `FRD-505` paid for
once.

**The migration carries a decision and only a decision.** An `allow_check` list was a choice
somebody made, so it becomes the release. A use case that never had the step could call everything
approved — and writing the whole catalog into it would have kept it running while showing, in a
console built to record who released what, **a release nobody made**. That is `FRD-122`'s rule
about the audit row applied to configuration: an unverifiable claim is not evidence.

**The picker came out of the first walkthrough**: a checkbox per row is honest and stops working
around thirty, and one real credential offers fifty. `core/ui/multi-select.ts` answers the two
questions separately — chips above for *what did I pick*, search below for *what else is there* —
and its tests are mostly keyboard, because unlike a checkbox there is no fallback underneath a
picker that needs a mouse. Two findings while building it: the first draft advanced the highlight
on the **same keypress that opened the list**, so ArrowDown into a closed picker landed on the
*second* option and the first was unreachable without a mouse; and `.picker`, the class the access
panel's directory results have used since `FRD-209`, turned out to have **no CSS at all** — that
list has rendered unstyled for as long as the screen has existed, the `routerLinkActive` shape
again. Styling it fixed both screens; making the shared class `position: absolute` laid the access
panel's results across the whole page, so appearance is shared and *where it floats* is not.

**The builder was the other half, and it hid an escape.** Every model field in the pipeline editor
was free text — the filter's classifier, the router's classifier, each category target, the default
target, the fallback chain — so it offered exactly what the server refuses and invited naming a
model the use case has no right to. All five are dropdowns over the release now, and both planes
enforce it: Management refuses a pipeline naming a withheld model *by name*, while somebody can
still fix it.

The gateway's **dry run** was the real hole, and it was measured before it was closed: a caller
posted a pipeline naming any model as its classifier and the gateway **called it** — no use case,
no release check, no approval check, no budget, no rate limit and **no audit row**. 1000 tokens
spent, nothing recorded, by anybody with a login. Its own docstring claimed the size bounds meant
"a single call cannot be turned into a free LLM relay". A dry run now names a use case (required),
is refused unless the caller may act on it, and may name only released models. Two consequences:
a Global Administrator is a member of nothing (`ADR-0007`) so they must grant themselves the use
case to test its pipeline — the console says that instead of blaming a gateway setting that works
— and the model a dry run *infers* when a pipeline names none now comes from the release, because
the first registered model became a guess that was guaranteed wrong. `J8`–`J11`, of which **`J9`
survived its first run**: no gateway test checked the fallback chain.

`allow_check` is gone from the vocabulary, the builder, the serializer and the read-model.
`J1`–`J7` — of which **`J4` survived its first run**: nothing defended the consumer's reading of an
absent field, which is precisely the property that decides whether a half-upgraded stack keeps
serving.

---

## 2026-08-10 — Ask the vendor instead of typing it (`FRD-507` stage C)

The ask: _"da wir aktuell mehrere vendors haben, wenn vendor die Möglichkeit anbieten Modelliste zu
nutzen, dann wird ein Dropdown mit modellen erscheinen"_ — a provider dropdown of what is actually
supported, and, where a vendor publishes a listing, the models to pick from.

**Three lists exist and they were never the same list.** What a vendor *offers* a credential, what
the gateway *serves*, and what the catalog *permits*. The console had the second (stage A) and the
third and asked an administrator to **type** the first — which is how `gemini-2.5-flash` came to
stand in a shipped default after Google had withdrawn it from new keys. Asking removes the
transcription and removes nothing from the decision: `approved` still defaults to false, nothing
here writes anything, and `GET /v1beta/providers[/{name}/offerings]` is bounded by **role**
(`CATALOG_ROLES` — one definition both planes read, after `FRD-503` found the two disagreeing about
a kill switch).

**What may be copied is the whole design.** Three things are facts: the name, the vendor's display
name, and its output ceiling — the API refuses a larger request. So are the verbs, because Google
returns an *exhaustive* method list and answers 404 for one missing from it; `createCachedContent`
is how prompt caching appears, and an implementation reading the obvious field finds nothing and
declares no caching for a model that has it. What is left blank stays blank: a price nobody set is
not zero (`FRD-403`), a capability is a measurement (`FRD-131` found a model advertising `tools`
that answers in prose), and `thinking` is shown and never ticked — the flag says a model reasons,
and `FRD-114` needs the modes and budgets no listing publishes. Every capability is therefore
**three-valued**: `null` is *the vendor did not say*, and an OpenAI-compatible listing says nothing
at all. Serialising that to `false` would have pre-filled a form somebody is about to save with a
declaration nobody made — `FRD-114` FR-7 at the moment it is hardest to see, because a half-full
form looks like a working feature either way. The console **names what it copied and what it left**.

Two facts the picker had to carry, because getting either wrong produces a catalog entry that looks
complete and does not work. **`canEnumerate`** is stated, never discovered by trying: a platform
without a listing is not broken, and an error beside it would report a capability gap as a fault,
which sends a reader to a different system. **`cataloguedIsEnough`** says whether declaring the
model is sufficient to reach it — true exactly where the model name is the *whole* addressing
(stage B). Azure is what that distinction was written for: `/openai/models` answers "which models
could this resource run", each needs a deployment created first, and an import from there would be
catalogued, priced, approved and answer 404 with the catalog vouching for it. So `names_models()`
lives on the **routing axis**, and Foundry — which builds the OpenAI adapter class — claims no
provider name and offers no listing, while a plain endpoint does both.

A provider name **does not identify one adapter**: an EU Vertex deployment registers two, Gemini
and Anthropic, one platform and one credential. Keying by name and keeping the last would have
described the provider with whichever registered second and offered a listing answering for one
dialect — `ADR-0011`'s ambiguous routing table in a read-only costume. Grouped, and a name with two
adapters cannot be asked.

Three things fell out that were older than this feature:

- **The readiness probe walked past an adapter with an empty configured list.** It iterated
  `registry.models()`, and since stage B made cataloguing enough to serve a model, that is the
  ordinary shape of a working Google AI Studio deployment: the catalog names the models,
  configuration names none. `/readyz` said **nothing whatsoever** about that upstream, and nothing
  reads as "no such thing" — the wrong half of `FRD-117`'s distinction between *we did not look*
  and *it is fine*. Shown red before it was fixed.
- **The Generative Language adapter had no `ping` at all**, so `/readyz` and `FRD-506`'s
  reachability check both reported it unprobed — honestly, with nothing behind the honesty. The
  listing is that cheap question, and it is a GET, never a generation.
- **The mock declared no provenance**, so a laptop had an empty provider dropdown and the whole
  flow was only demonstrable against a cloud nobody has in CI. It names itself now (`local` only)
  and states the two verbs it really implements and nothing more — a double that claimed every
  capability would make this feature's own subject true by construction. Declaring a *region* for
  it broke every mock request under the EU default, which is the residency control correctly
  refusing a fiction: it claims none, the rule `build_openai_upstreams` already recorded.

Also: two `MockProvider`s registered to serve two models became two adapters claiming one provider
name, which the registry refuses to boot on and refuses correctly — the registry was right and the
expression was wrong, so the mock takes several models.

**Two corrections from the first walkthrough of the running console**, both about the same thing —
a control's size and a control's name. The picker was a dropdown inside the model editor, and a
select of fifty entries inside a form with eighteen fields is something somebody scrolls past: it
is a **window of its own** now, reached from a button beside _Add model_, listing rather than
picking — searchable, scrollable, marking what the catalog already has, and closing as it hands one
model to the editor. And the providers read `generative-language` beside `local`, which names
neither vendor. They carry a **label** now, stated by the adapter (`Google AI Studio`,
`local — OpenAI-compatible endpoint`) with the name kept in brackets, because the name is the
identifier that reaches the catalog and every audit row. A label map in the SPA would have been a
second vocabulary in TypeScript — the drift `FRD-206` and the capability checkbox list both paid
for.

**Test lesson, the same one twice in two days.** The late-arrival case — the provider list
answering *after* the editor is laid out — was written with a stubbed `of()`, which answers inside
the call that starts it, so the editor's own guess was what the assertion read and the deferred
rule never ran. It passed against the broken code. Rewritten with a `Subject`, it goes red, and it
found that the rule had only been written in one direction: a perfectly configured provider stayed
stuck in the text box it was meant to replace for every model opened faster than the gateway
answered. `I1`–`I6`.

---

## 2026-08-10 — Prompt caching, measured first (`FRD-133`)

The ask: agents are expensive because the whole conversation goes over on every turn. The FRD had
been written in advance and deliberately not built, with §5 naming three numbers that had to come
out of `request_logs` before anything was designed — and leaving open that they might say _don't_.

**They said build, and they corrected the premise.** From 26 served OpenCode turns: 99.1 % of a
large turn is repeated content, the median gap between turns is 41 seconds with 13 of 14 inside
five minutes, and 93.3 % of that use case's tokens are input. But it is **not the conversation**:

```
tools              ~21.5 KB   69 %
systemInstruction   ~9.7 KB   31 %
contents           47–1633 B  0.1–5 %
```

The tool declarations and the system prompt are the bill; the conversation is the smallest part.
That is exactly Anthropic's cache hierarchy (`tools` → `system` → `messages`), so **two**
breakpoints catch the 99 % — and it reverses this FRD's own non-goal about automatic placement,
because those two boundaries are drawn by the API rather than guessed from somebody's prompt.

**The first measurement was wrong and pointed the other way.** Comparing the common _string prefix_
of consecutive stored payloads gave **0.5 %**, which would have killed the feature. The
serialisation puts `contents` before `systemInstruction`, so it measured JSON key order. The
plausible number was the wrong one.

**What was already broken.** Anthropic cache tokens were read and folded into `prompt_tokens`
(`input + cached + created`) and priced at 1.0x. A read is **0.1x** and a write **1.25x** or **2x**
— wrong in both directions, and _under_-stating the expensive one. Even a working cache would have
been invisible in reporting, which by §7's own acceptance criterion means unverifiable.

Built in two stages for that reason: the accounting first (provider-independent; three of the four
providers already report the numbers and only Anthropic takes a marker), then the marker. Doing it
the other way round produces a real saving at the provider that AIRA cannot show.

Vendor facts came from the vendors, not from memory — including the answer to §6's open question:
on **Vertex the cache is isolated per organisation, not per workspace**, and AIRA holds one
credential per platform for many use cases. Hence per-use-case and default off: a use case whose
system prompt is itself confidential should not be opted in by somebody else's cost decision.

One rule is deliberately unlike all the others and says so in three places: **a model that cannot
cache is served uncached, never skipped.** Every other capability guards the _answer_ — an
incapable candidate would answer about a document it never saw. This one guards the _price_, and
refusing a request over a price is the opposite of what a fallback chain is for.

Also: `tools_enabled` had existed only in the API since `FRD-131`. Both switches are in the console
now — `FRD-206` inverted, a capability with no way in, which nobody notices because nothing fails.

**Stage C — the console, and which parameters are worth offering.** One: the **lifetime** (`5m` or
`1h`). Everything else about caching is fixed by the vendor or already settled by §5's measurement,
and a control with one correct answer is `FRD-206`'s complaint in a different key. The lifetime is
the exception because **only the caller's own traffic settles it** — an hour costs about double to
write and pays only where the gap between turns regularly exceeds five minutes, which is the
opposite of OpenCode's 41 seconds and might well be true of a chatbot a human reads between. Each
control says what it _costs_, not what it does, and the two catalog price fields say which
direction they go, because that is the surprising part: cached input is a tenth, a cache write is
1.25x or 2x. A field labelled only "cached input price" invites the ordinary rate — which is
already the fallback, and then the wrong figure looks deliberate.

Tuning is empirical only if the effect is visible **beside the setting**, so the consumption panel
(`FRD-603`) grew a **Cached** share next to spend, requests and tokens; its hint names the four
different reasons for 0 %, which have four different fixes.

**The wiring is what the tests could not see.** The mapping tests prove the marker is built right
and say nothing about whether the configuration ever reaches it — four hops (checkbox → event →
read-model row → post-routing lookup) where a dropped setting produces a served request that looks
exactly like one nobody asked to cache. `FRD-124`'s lesson, so the mock now **says what it was
asked** the way it already does for thinking and attachments, and three tests drive the whole path
through the route. It reports no cache _hit_, deliberately: fabricating one would make every
"caching saves money" assertion true by construction, and whether a prefix is really hit is the one
thing only a provider can tell us.

The harness reported `U5` **STALE** rather than passing — its anchor was the constant that became a
function when the lifetime arrived. That is the `N2` lesson working as built: a mutation whose
anchor has moved defends nothing and used to report green about it.

**And then the console could not declare the capability it had just been given prices for.** The
SPA restates the closed capability vocabulary as a TypeScript array, and it was missing **`tools`**
(since `FRD-131`, two days) and **`prompt_caching`** (since this morning). Nothing failed: a Global
Administrator sees five checkboxes and has no reason to think there should be seven, and an absent
checkbox looks exactly like a design decision — `FRD-206` inverted, the same shape as the anomaly
rule nobody could author. The fix is the one this repository has now reached four times
(`aira.rate-limits`, `aira.anomaly-rules`, `use_case_group.granted`): compare the hand-written list
against the constant **in both directions**, since a value the console offers and the gateway does
not know is a declaration somebody believes they made. Each checkbox now also carries what ticking
it commits the platform to, because the consequences differ sharply — most absences **skip a model
from a fallback chain**, `prompt_caching` only changes the price. That map is a
`Record<Capability, string>`, so the compiler refuses an undocumented capability; shown to fail by
deleting an entry, since a guard nobody has broken is a guard nobody has tested.

One e2e guard had to be narrowed and was proved still sharp first: `expectFormControlsAligned`
counted an info hint's trigger as a row control, and a hint lives **inside a `<label>`**, one line
above the field — so every explained field read as a staircase. Excluded by _where it is_ rather
than by what it is, and then a 6-pixel misalignment was injected to watch it fail (12 px did not:
the guard bands rows at 40 px, and 12 crossed a boundary — worth knowing about it).

`U1`–`U9`.

---

## 2026-08-10 — A walkthrough of what was just built, and a demo that ends at a working assistant

Four reports from the running console, three of them defects in the day's own work.

**The explanations misbehaved as overlays.** Hovering a capability's "i" in the model editor made
the window jiggle and some text ran outside its frame. Both measured before anything was changed,
because "it looks wrong" is not a defect report a fix can be checked against:

- The panel inherited `white-space: nowrap` from `.form-inline .field > label`, which every such
  label carries so that the controls under them line up. A 372-character explanation was laid out
  as **one 2210-pixel line inside a 478-pixel box**. The panel already resets `text-transform`,
  `letter-spacing`, `font-weight` and `text-align` for exactly this reason; `white-space` was
  simply missing from the list. **A panel that can be opened from anywhere owns its typography.**
- It was `position: absolute`, which extends the scroll container: open one near the bottom, a
  scrollbar appears, the page reflows narrower, the "i" slides out from under the pointer, the
  panel closes, the scrollbar goes — a flicker loop that never settles. And centred on its button
  it left the container: measured **58 pixels outside the dialog**, with a hand-written escape for
  the last cell of a table, which is a defect noticed once and fixed in one place.

Now `fixed`, placed from the button's rectangle and clamped into the viewport, flipping above the
anchor when there is no room below. The hand-written table escape is gone: one rule for every edge.
**And `fixed` is not always relative to the viewport** — any ancestor with a `transform` becomes
the containing block, which the modal has, so the first attempt landed 201 pixels left of where it
was asked to go. The origin is _read_ now: park the panel at (0, 0), see where that is, subtract.
Reasoning about coordinate spaces is how that bug was written; measuring is how it was fixed.

**Two switches were in the wrong panel, and the panel said so.** Function calling and prompt
caching had been added to the nearest available form — data protection's — so they sat _between_
"store prompts and responses" and "keep them for N days", the pair a reader treats as one setting.
They are now their own section with their own save and their own sentence: turning caching on used
to answer with a message about how long prompts are kept, which is a confident statement about the
wrong thing. Asserted as _what may come between the two controls_, since "the order looks wrong" is
not something a test can be told. The split then produced **two buttons reading "Save"** — three
e2e tests reported it as an ambiguous selector, and a screen reader would announce one word twice.
They say what they save now.

**`make showcase` could not bring up a coding assistant**, while `tools/opencode/README.md` had
pointed at a `coding-assistant` use case since `FRD-132` — an instruction with no destination.
Four missing pieces, and the second is the one worth keeping: **the Management-side model seed did
not declare `tools`** while the gateway-side one did, from the same measurement. Second time that
pair of files has held one fact and two answers. The consequence was silent and total — the
assistant was refused by name and every explanation pointed at the client. There is a
`coding-assistant` use case now (the only one with function calling on), limits sized for an agent
rather than a chatbot, and `tools/showcase_agent.py` writing an OpenCode config that `make
showcase` prints. Verified live: a real tool call through the demo key, audited as
`{"declared": 1, "called": ["read_file"]}` with the credential prefix and a price beside it.

Prompt caching stays **off** on that use case and the description says why: this runtime reports no
cached tokens, so switching it on would show a control doing nothing — in the one place a reader is
most likely to believe it.

**Then `make showcase` was run end to end, from a pruned build cache and a stopped stack, and
failed twice more.** Both failures had the same shape as the pull below: _it works on a machine
that has already done the thing by hand._

The dev Vault runs `server -dev` and keeps everything in memory, so `docker compose down` loses
`secret/aira`. `load_secrets()` fails closed — correctly — and every application container refuses
to boot. So the showcase depended on somebody having run `make vault-init` **after the current
Vault container started**: follow our own documentation, bring the stack down, and the one command
that must always work stops working. A `vault-init` service provisions it now, with the
migrations waiting on it, because ordering belongs in the file that owns ordering rather than in
one of four entry points. **It was written with `profiles: ["demo"]` first, and that was a
regression of its own**: compose rejects the _whole project_ — not the one service — when
something depends on a service the active profiles leave out, so
`docker compose -f … -f …` with no profile answered `invalid compose project`, taking CI's
log-dumping fallback with it, which runs without profiles and exists for the moment something else
has already gone wrong. Guarded by the environment instead (`ADR-0015`'s shape): no address means
nothing to do, and anything but `local`/`demo` means somebody else owns this Vault. A test now
states the containment — whatever enables a service must enable everything it depends on. Two mistakes while writing it, both worth keeping: it wrote all
three known secrets unconditionally, and **Vault ranks above the environment**, so the empty string
won and the stack came up with `no password supplied` — _absent and empty are different answers_;
then, writing nothing, `vault kv put` failed with `Must supply data`, because an empty write is not
a write and the path still did not exist.

And the demo had spent its own budgets. They are calibrated so a handful of requests moves each bar
into the middle of its range, so the second run of a day answered **429 to six of ten requests** —
including the prompt-injection case, whose whole point is to be refused by the _pipeline_. Still
true, and about yesterday. `make showcase` now clears what earlier runs **consumed** (Postgres and
the shared Redis counters, both — clearing one leaves the other refusing for a period nobody can
see) and nothing the demo **is**: configuration, keys and the request log stay, since a spend report
reading zero after every run is the opposite defect. `make showcase-traffic` deliberately does not
reset — filling the bars until a limit is reached is what that target is for. Two consecutive runs
now produce the same thing: ten served, one refused by the filter.

**Then it was run on a machine that had never seen this project** — `.env` deleted, database and
Kafka volumes removed — and it **reported success while serving nothing**: ten requests, ten
refusals, `400 … 'qwen3:0.6b' is not in the model catalog`.

The seed wrote the catalog and never announced it. `local_models` created both `Model` rows and
emitted no event, so Management's catalog filled up and the gateway's read-model stayed empty —
only the viewset emitted, so a model declared through the console reached the gateway and a model
declared by the seed did not. Invisible until `FRD-307` made a catalogued, approved model the only
kind that may be served; from then on an unannounced catalog refuses **everything**. Fourth
instance of two correct halves with no wire between them, after `record_to_outbox`, the missing
Kafka topics and `payload_size`. It emits the **viewset's** `_payload`, because a second
hand-written payload is a second place to forget that prices travel as decimal strings.

And the target reported success over it, because the traffic script only failed on a 5xx and every
one of those refusals was the gateway behaving correctly. Nothing served is a failure now, with a
message naming the two logs worth reading, and the Makefile no longer swallows the script's exit
code. The model pull got three attempts and an explanation: one failed download from a registry a
corporate network may block used to produce a bare non-zero exit, after which compose blamed
`management-seed` — a service that never ran.

Re-run from the same empty state: ten served, one refused by the injection filter.

**And then nobody could log in.** `make showcase` prints five accounts and Keycloak answered
_invalid username or password_ — on a machine whose realm predated the change that mattered.
Keycloak imports a realm **only if it does not exist** (`Realm 'aira' already exists. Import
skipped`), so every edit to the realm file reaches a fresh machine and no other: new groups from
`ADR-0017`, the group mapper from `FRD-209`, a password. The repository already knew and said so —
in a README, which is a demo that works for whoever wrote it. Same argument as `vault-init` one
service over: **state the one-command demo depends on belongs in the stack, not in somebody's
memory.**

`keycloak-init` compares the running realm against the file and re-imports it when something the
file names is absent. Idle otherwise, gated to `local`/`demo`, and never aimed at a directory AIRA
does not own — the product does not write to one (`FRD-209`); this is the demo stack provisioning
its own.

Three defects while building it, and the third is the one worth carrying:

1. The check asked `GET /groups`, which since Keycloak 26 returns the top level without filling in
   `subGroups`, and `GET /users`, which omits service accounts. Every realm looked broken, so the
   first run **deleted a healthy realm** — the tool punishing use that its own docstring warns
   against. It asks `group-by-path` one path at a time now, and ignores users a client creates.
2. Keycloak answers a realm delete immediately and finishes it afterwards, so the create posted
   straight after was overtaken: the script printed `re-imported`, the API said 201, and the realm
   was gone. It waits for the delete to land and reads the realm back before claiming anything.
3. **The repair fixed the directory and corrupted what reads it.** A re-imported realm minted new
   subjects, and `ADR-0007` binds a Django user to the `sub` — so logging in as `ucadmin` showed
   `ucadmin-279b6b7b` in the console, a second account owning nothing. The README documented that
   trap and a manual rebind procedure. The better answer is that **the demo's identities are
   fixtures and must not move**: every user and group in the realm file now carries a stable id,
   derived from its name, so a re-import produces the same subjects and every binding survives.
   The same rule as `FRD-130`'s deterministic demo keys, one identity system over. Verified by
   deleting a user, letting the service repair the realm, and logging in through the real code
   flow with nothing cleaned up in between.

**And the demo pointed at Keycloak without saying which realm.** The admin console _always_
authenticates against `master` — that is where the Keycloak admin lives — and the realm it manages
is a fragment in the URL. So somebody opened it, saw **one** account called `admin` and **no**
groups, and concluded the seeding had failed. It had not: `aira` had six accounts and three group
trees one realm-switcher away, and two different accounts are called `admin` in two different
realms. The showcase now links straight to `#/aira/users`, names both accounts, and **prints what
it just read from the running realm** rather than asserting that seeding worked — a demo that says
"5 accounts created" over an empty console has taught you to distrust it.

Two things the same output was quietly getting wrong: the login table still said `ucadmin`
administers "two of the three use cases" after a fourth was added, and that `ucuser` is in
`kundenservice` when it is now also in `coding-assistant`. Nothing fails when that drifts — the
reader finds the console disagreeing with the instructions and believes the instructions. A test
compares the printed table against the seed's memberships in both directions. And the Makefile
comments explaining all of this were **being printed to the user**: a recipe line starting with `#`
is still a recipe line, and eleven of them had landed in the middle of the demo's own output.

Measured while there: `make showcase` takes **66 seconds** on a warm machine.

**A deployment review of the day's changes** found one regression, and it was in the safety net
rather than in the product. `vault-init` waited for a **healthy** Vault before it could exit, and
the migrations wait for `vault-init` — so a deployment that sets no `VAULT_ADDR` and never touches
Vault was nonetheless behind Vault's health check. It waits for Vault itself now, briefly, and
**gets out of the way** if it never answers: provisioning a development secret store is not worth
stopping a stack for, and a deployment that really needs a secret then fails in the container that
reads it, naming the path. Both helpers were confirmed inert outside `local`/`demo` by running
them as `production`, the compose project is valid under every profile combination, and the test
suite's `conftest.py` — which disables the dotenv — was confirmed absent from both images.

Everything else the day touched is either build-time, demo-only, or `FRD-133`, whose migrations are
additive and whose consumer reads the new fields with `.get(...)`: an older Management's event
leaves cache prices `None` (billed at the ordinary input rate) and caching off.

**Then every request came back 401, and the diagnosis printed for it blamed the model catalog** —
an authentication failure, described as a cataloguing one. A diagnosis confidently about the wrong
thing sends somebody looking in the wrong place, so the traffic script reads the status codes and
says what each means. The cause was a rule working as designed: deleting a use case revokes its
keys, revocation is **terminal** in the read-model (`ADR-0007`), and the demo's keys are
deterministic — so re-running the seed re-announces the same prefix and changes nothing. The stack
looks perfect and every request is refused for ever. `make showcase-reset-keys` removes those rows,
**polls** for the announcement to land rather than guessing an interval, and reports through the
doctor; deliberately its own command, because deleting rows from the read-model authorization is
drawn from is not a habit to encode into something that runs every time. Found while proving it:
the tally counted the embedding call only when it succeeded, so eleven requests reported as "9
served, 1 refused" and the missing one appeared nowhere.

**And the two reports were one event.** "The ollama pull blows up" and "the console is empty"
looked unrelated to the person seeing them: `management-seed` waited for `ollama-pull` to complete
**successfully**, so one failed download meant the seed never ran at all — no accounts, no use
cases, no budgets, no keys. The doctor said it in one line: _1 use case, missing all four demo
ones; 0 models_. `FRD-130`'s rule (a model nobody pulled must not be catalogued) is enforced by
**evidence** now instead of by ordering — the seed runs regardless and asks the endpoint which
models it actually serves. An endpoint that cannot be asked declares **nothing**, because
unreachable is not "serves nothing". That check then dropped the embedding model, because the
catalog says `all-minilm` and the endpoint answers `all-minilm:latest`: an absent tag means
`:latest`, the same family as the colon that once split `qwen3:0.6b`.

**And then the console came up empty.** The data was there — 6 accounts, 4 demo use cases, both
read-models populated — and the list said _"No use cases yet"_, which is a confident statement
about the wrong thing. Roles come from the token's **groups** (`ADR-0017`) and are worked out at
**sign-in**, so a session older than a change to those groups carries no role and sees nothing.
That is `FRD-206`'s rule again: an empty list must say _which_ empty it is. It now says the session
carries no role, that there may well be use cases it is not allowed to see, and what to do about
it.

`make showcase-doctor` reports the chain link by link — Keycloak accounts and groups, Management's
use cases and who may see them, the gateway's read-model — and names the first broken link with
the command that fixes it. Every one of those links has been seen broken this week (a realm older
than its file, a Vault that had forgotten its path, a catalog written and never announced, budgets
an earlier run had spent), and every time the symptom was the same: a console that comes up and
shows nothing, with nothing anywhere saying why. Deliberately **not** part of `make showcase`: a
demo that runs a diagnostic every time is a demo that has given up on working.

**CI failed on two tests that pass everywhere this project has ever been written**, and both were
the same defect wearing different clothes: _a unit test that reads the developer's machine is a
test whose green is about that machine._

`BaseAiraSettings` loads `.env` from the working directory, which is right for `make run-gateway`
and wrong for the hermetic suite — so the tests read whatever `.env` a developer happened to have.
With a Google key in it the provider registry carries a Gemini upstream, and
`test_a_declaration_reaches_the_model_list` had _other_ models to assert were undeclared; without
one the mock serves exactly one model and the assertion had nothing to stand on. And
`test_an_unreachable_upstream_degrades_rather_than_failing_readiness` calls `/readyz`, which makes
**real TCP checks** against Postgres and Kafka: it passed on any machine with the stack running.

A root `conftest.py` disables the dotenv and clears `AIRA_*`/`VAULT_*` for the session. The two
tests then say what they need themselves: one registers a second mock provider so there _is_ an
undeclared model, the other opens a socket and points Postgres and Kafka at it — the probe stays
real, and only the dependency the test is not about is held up by construction. Stubbing
`check_tcp` was the other option and the worse one. Reproduced locally by stopping the stack, which
is the whole point: the suite reports on the code now.

**The showcase advertised a console it had never waited for.** It waited on the gateway alone and
then printed `SPA http://localhost:4200` — so on a machine where the frontend needed a few seconds
longer, the one URL the walkthrough starts at answered nothing. Two ideas of "ready" in one
repository, and this used the weaker one; it calls `wait-healthy` now, which checks the console,
both APIs and Keycloak, and is what CI uses. The output also says which of the four ports have a
user interface and which do not — somebody looking for the console had found Django's 404 on 8002.

Frontend branch coverage was one branch under its gate, all of it in the new `saveCapabilities`:
switching caching **off** now asserts that the banner does not claim a saving, the lifetime is
named in words rather than as `5m`, an unchanged form sends nothing, and a response that omits the
fields reads as off — absence of information is not permission, one plane over.

**The two build systems ran at different levels, and that was not cosmetic.** Both Python images
build from the repository root; the frontend built from `management/frontend`. **Docker reads
`.dockerignore` only from the context root**, so the repository's own ignore file — which excludes
`**/node_modules`, `**/dist`, `**/.angular` — did not apply to the frontend at all. Its `COPY . .`
therefore copied a developer's **287 MB `node_modules` into the builder on top of the tree
`npm ci` had just installed**. Proved with a marker file rather than argued: the host's copy wins.
So the image was built against whatever happened to be on that machine's disk — a different
platform's native binaries for esbuild and rollup, a different Node version, or simply something
stale. A generator of "it builds for me" and of nothing else, and the `npm ci` layer above it was
doing nothing. One context root now, named copies instead of `COPY . .`, and the context is
**1.5 MB instead of 302 MB**. A test requires every image to share the root, shown to fail by
moving the frontend back.

**And before any of those, `make showcase` tried to _pull_ `aira-gateway` and
`aira-management`.** Four services
carried `image:` with no `build:`, because they run a second process out of an image a sibling
builds — the consumer, the relay, the retention job, the seed. Compose pulls anything it is not
told how to build, and those images exist on no registry. It failed in the way that is hardest to
notice: **only on a machine that had never built them**, because everywhere else the tag was
already lying around in the local store. So it worked for everyone who had run the stack and broke
for exactly the person the target exists for. Every service names its build now, through two
anchors, and a test parses both compose files and refuses a service that names a locally-built
image without saying how to build it — shown to fail by deleting one.

---

## 2026-08-10 — Google AI Studio works, and importing what it serves (`FRD-507`)

A real key, tested end to end: through AIRA, governed, audited and priced —
`gemini-flash-latest`, provider `generative-language`, region `global`, 16 + 55 tokens, 142 300
nanos, outcome `served`. Three findings on the way, and the third was mine.

**`gemini-2.5-flash` is listed and unusable.** Google's own listing offers it; the first request
answers `404: this model is no longer available to new users`. Listed is not usable — which is why
`FRD-506` splits declared, served and reachable into three facts rather than one green tick.

**`AIRA_GEMINI_MODELS` and `AIRA_GEMINI_BASE_URL` were never passed through by compose.** An
operator could set either and nothing happened: the third instance of that defect in this file
after the three timeouts of 2026-08-08. A knob that is not wired is worse than an absent one.

**And wiring them broke the adapter within the minute.** `${AIRA_GEMINI_BASE_URL:-}` passes an
_empty string_, which overrode the default and produced `UnsupportedProtocol` — an error message
about an upstream, describing our own configuration. Same rule as the Vault provisioning earlier
the same day: **absent and empty are different answers**, and `_empty_means_unset` deliberately
leaves `str` fields alone because for most of them empty is a real answer. A base URL is not one of
those, and now says so.

The stale default went with it: `gemini-2.0-flash,gemini-1.5-flash` — two models a key issued today
cannot use. A default naming something unusable produces a 404 that reads as our fault.

**`FRD-507` — importing what the adapters already serve.** Asked whether creating models in the
console makes sense at all. The _decision_ does and the _transcription_ does not: one key listed
**50 models**, 36 able to generate, none of them approved by anybody. So the catalog screen asks
the gateway what it serves, marks what is already catalogued, and offers the rest as a **draft** —
filled in with **provenance only**. Price, capabilities and the release checkbox stay untouched,
because a vendor's flag is a claim (`FRD-131` found a model that lists `tools` and answers in prose)
and a price nobody set is not zero (`FRD-403`). Nothing is created until somebody saves, and
`approved` still defaults to false.

The browser found what the component tests could not: the listing returns Google's resource form,
so the import would have catalogued **`models/mock-1`** — an entry no request can ever match, and
one that looks right in the table. Stripped at the boundary where the wire shape stops.

## 2026-08-10 — The one upstream nobody was measuring (Google AI Studio and residency)

Asked whether AI Studio could be supported, or whether the Vertex work had closed that door. It is
not closed and never was: `FRD-304` built it first, and it is what
`https://generativelanguage.googleapis.com/v1beta` is. One variable, an API key rather than a
service account, and the same governed path as every other model.

Answering the question found the hole. **Three of the four adapter families measure their region
against `AIRA_ALLOWED_REGIONS` at startup** — Vertex, the OpenAI servers, Foundry. The AI Studio
one did not. It declared `region="global"` on every model, honestly, so the value reached the audit
row; nothing ever compared it to the policy. Under the shipped EU default a deployment would
therefore serve traffic through an endpoint that names no region and guarantees none, and the
evidence would say it was compliant.

**An enforced control that one path bypasses is worse than one that is missing everywhere.** Same
shape as `:embedContent` skipping the pre-dispatch gate, and as the KIRA surface's inverted
membership check: a rule stated once and applied in all but one place.

`FRD-115`'s rule applies unchanged — a region this deployment does not permit is a **startup**
failure, because a gateway that sometimes leaves the EU is the same problem discovered later and by
somebody else. AI Studio stays entirely usable: name `global` in `AIRA_ALLOWED_REGIONS`, and the
question turns from something a person remembers into a line in the configuration and a region on
every audit row.

The case-by-case tests would not have caught the next one, so the guard is structural: every
`build_*_upstream(s)` in the upstream layer must mention `check_region`. Its first run flagged
`build_token_source`, which builds a **credential** and has no region — narrowed to the two
suffixes the layer actually uses, then proved sharp again by removing the new call. A guard that
fires on the wrong thing gets narrowed once and believed; one that gets switched off gets removed.

## 2026-08-10 — Two roles nobody could use, removed

`ADR-0017` abolished `use-case-admin` and `use-case-user` on 2026-08-09: administering or belonging
to a use case is a **grant on that use case**, not a property of a person, and `may_admin`,
`may_manage`, `is_member` and `scope_queryset` all needed no change to prove it. They stayed in the
vocabulary anyway — in the `Role` enum, in the Keycloak realm file, in the seed (which created a
Django group for each and assigned them), in the console's specs and in `ROLES.md` and
`DEPLOYMENT.md` as if they still meant something.

**A role somebody can be given that does nothing** is the plainest version of the defect `FRD-206`
is about: an absent capability reads as a boundary, a present one with no effect reads as a broken
system, and the reader then distrusts the permissions that do work. Removed everywhere, and the
realm and the code are now compared **in both directions** by a test — a role in the code the realm
cannot confer is unreachable, and a role in the realm the code does not know is a promise nothing
keeps. Fifth instance of that answer, after the Kafka topics, the capability vocabulary, the emit
map and the console's checkbox list.

Three things fell out of doing it:

- **The seed was wiping the groups it does not own.** `user.groups.set([role_group])` also removed
  the `kc:/…` groups `sync_user_roles` writes from the token, so running the seed silently
  un-granted every demo user's use-case access until their next request repaired it. It touches
  the role groups and nothing else now.
- **A branch that could no longer fire.** `parse_role_groups` refused a role that "is not conferred
  by a group", which became unreachable once the only two such roles left the enum. Removed — an
  unreachable guard is not a guard — with its _message_ folded into the unknown-name refusal,
  because those two names appear in older `.env` files and are exactly what somebody will type.
- **A mutation that would have passed for the wrong reason.** `O1` added `Role.USE_CASE_ADMIN` to
  the oversight set; the mutated file would no longer import, and an `AttributeError` is not a
  property being defended. Re-anchored onto `IT_SECURITY`, which is the meaningful one: it sees
  every use case and deliberately not every figure.

Also corrected: `FRD-133`'s status line said "both stages" after three were built.

## 2026-08-10 — Who answers for a credential, and who made it (`FRD-604` Stage B)

Stage A was four sentences and a badge: the console had recorded the issuer all along and never
said what the name meant, so an investigator read a colleague's name beside a rogue agent and
concluded a human had typed it. Stage B is the arrangement that makes a **shared** credential
honest.

`owner` and `issued_by` are two different questions. The owner answers for the credential — a
technical account for a team — and is the name every audit row carries, correctly: a row describes
what called, not who authorised the credential months earlier. The issuer is the human who created
it, and that is the fact the obvious alternative destroys: signing in _as_ the technical account
needs shared credentials for a **governance** console and records "svc-kundenservice issued a key",
which nobody can act on.

A **string**, not a foreign key, like `UseCaseGroupGrant.granted_by` and a suspension's author:
who did something is a fact about the past, so deleting the person must neither delete the record
nor be prevented by it. Blank when they are the same person — a distinction nobody asked for must
not appear on every row, or the one row where it matters gets skipped.

**The two refusals matter more than the feature.** An owner the directory does not know is refused,
because an accountability chain ending in a string is not one; and an owner with **no access to
this use case** is refused, because attaching a credential to an uninvolved colleague would put
their name beside an agent's traffic _deliberately_ — this FRD's own defect with the sign reversed.

One deliberate deviation from the FRD, written back into it: the owner is **typed, not picked from
a directory**. The constraint is not "a real identity" but "an identity with access to this use
case", the server checks exactly that, and a picker over the membership list would have been
narrower than the rule — access can come from a group grant, and a service account granted that way
belongs to no membership row, which is exactly the chatbot case.

Each refusal shown to fail first. The "ordinary key records no issuer" test needed the **inverse**
mutation, as `N50` and `FRD-604` §10 already say: a test asserting an absence cannot go red when
the code that fills the column is deleted, only when something starts filling it always.

---

## 2026-08-09 — A security read of the whole code (`ADR-0018`)

The request path held up: 192-bit keys compared in constant time, JWTs with a pinned algorithm and
`exp`/`iat`/`sub` required, payload access gated and recorded, bodies and schemas bounded, no
`eval`, no raw SQL, no disabled TLS verification, and DRF authenticated by default. Frontend
dependencies clean; tokens in `sessionStorage`.

**Three of the four findings were in the space between the services**, and they share one shape:
a link AIRA trusts completely and could not be told to verify.

**The event bus had no authentication and no way to add it.** `apply_event` writes what arrives on
the config topics straight into the read-model the gateway's authorization comes from. Anyone able
to reach the broker could publish `api_key.created` with a hash of their choosing, or
`use_case_group.granted` naming a group they are in, and hold administrator access to any use case
— no credential, and **no audit row**, because from the gateway's side nothing unusual happened:
configuration arrived, as configuration does. Applying events without question is right _if_ the
bus is authenticated; there was simply no setting that could make it true. Both planes take a
broker identity now and refuse `PLAINTEXT` outside `local`.

**Nothing required the identity provider to be reached over TLS.** The JWKS is where signing keys
come from: over plaintext anyone on the path substitutes a key set and mints tokens that verify.
Same for Vault, whose address carries the AppRole login. One rule
(`aira_common.transport_security`), read by both planes, with **loopback exempt** — a sidecar
terminating TLS on `127.0.0.1` is normal, and a rule that gets worked around by setting
`AIRA_ENVIRONMENT=local` switches every other check off with it.

**`X-Forwarded-For` was read from the left**, under a docstring assuming a proxy that _overwrites_.
The nginx this repository ships **appends**, as every default configuration does — so the left end
was the caller's to write, and it lands on every audit row, drives `FRD-505`'s incident filter, and
keys the failed-authentication bound. Rotating one header therefore made the brute-force bound
unreachable. Read `AIRA_TRUSTED_PROXY_HOPS` entries from the **right** now; a chain shorter than
that did not come through those proxies and the socket peer is used instead. The old test asserted
the leftmost entry — it had **written the vulnerability down as the expected behaviour**, which is
the sharpest example in this round of a test agreeing with the code instead of the requirement.

**And a comment that claimed a protection the code did not provide.** The Vertex transport built
its model segment with `httpx.URL(path=f"/{model}").path` beside a comment saying "the model
segment is encoded". It leaves `/` and `..` untouched and _decodes_ `%2f`, so `..%2f..%2fx` came
out as `../../x` — worse than the input. `AzureRoutes` had solved the identical problem correctly
one directory away with `quote(..., safe="")`.

**Two structural guards, because both holes were invisible rather than wrong.**
`test_every_route_is_guarded.py` walks the app and requires every route to authenticate or be on a
written list — the Gemini surface's _entire_ protection is one `dependencies=[...]` argument at
mount time, and nothing said so. Building it taught its own lesson: this FastAPI keeps routers
nested behind `_IncludedRouter` and applies those dependencies from the include context, so a
guard reading only `route.dependant` reports the correctly protected routes as holes and gets
"fixed" by exempting them. Inheriting the context is the difference between the file guarding
something and excusing it.

**Test quality**: twelve assertion-free tests turned out to be legitimate "does not raise" cases
with their positive counterpart beside them. The real gap was the catalog validator — thirteen
refusal branches with no test, in the module `FRD-114` relies on to stop a declaration that cannot
work. 83% → 99%, 24 cases, one per wrong shape rather than one with everything wrong, because a
validator that stops at the first problem passes the second kind and leaves an operator fixing one
field per attempt.

`W1`–`W4` guard the four fixes. Nothing about what the platform _does_ changed.

**And the live suite caught what the default gate could not.** `tests/integration/` is excluded
from the default run (`-m 'not integration'`), so `ADR-0017`'s migration passed it over even though
`grep realm_access` had listed the files: one test asserted `claims["realm_access"]["roles"]` and
raised a `KeyError`, and five fixtures created a use case as an account that may no longer. **A
test layer excluded from the default run is a layer a migration forgets** — the same shape as
`FRD-206` and `FRD-505`, arriving from the other direction.

---

## 2026-08-09 — A role is held through a group, and only through a group (`ADR-0017`, `FRD-605`)

Owner's rule: **group memberships are the single point of truth**, and individual memberships are
set in Keycloak or by an external system driving it. AIRA had two mechanisms answering "who is
this" — realm roles for the five roles, groups for use-case access — which is `FRD-209`'s defect
one level up, and `FRD-209` was written to remove exactly that shape.

Two ways to get there with Keycloak, and the difference is the whole decision. **Mapping the realm
role onto a group** costs no code and leaves the single point of truth a _convention_: nothing stops
an administrator also assigning the role to a person, and a screen that reports it is a witness, not
a rule. **AIRA mapping group → role** makes a direct assignment structurally inert. The requirement
was the guarantee, so: `AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;…`, and
`realm_access.roles` is not read at all.

**The other two roles cease to exist.** `use-case-admin` and `use-case-user` were never
organisation-wide facts about a person — administering a use case is a group's relationship to
_that_ use case, and `UseCaseGroupGrant` has held it since `FRD-209`. Evidence that they had been
redundant for months: `may_admin`, `may_manage`, `is_member` and `scope_queryset` needed **no
change**, and `IsUseCaseUser` turned out to be defined, exported and used by nothing.

**Smaller than expected in the data plane, sharper than expected in the tests.** The gateway's
whole role vocabulary is oversight/governance/incident, all three organisation-wide — so its diff is
**one call**. Management needed `sync_user_roles` and three predicates, each re-derived from what it
_meant_: creating a use case is a Global Administrator's act (a narrowing — the old role let anybody
administering one use case create another); the directory picker is "administers **any** use case",
because taking it from the people who add members is `FRD-206`'s defect inverted; running a model
test is `FRD-504`'s own sentence, _whoever may call a model may test one_.

The migration of thirteen test files **was** the audit. The shared helper refuses the two dead roles
**by name** instead of granting nothing, so every call site had to be looked at — and a blanket
rewrite to `global-admin` made the _boundary_ tests pass for the wrong reason, because a Global
Administrator is refused by nothing. They use a caller with no organisation-wide role now, which is
what a use-case administrator is at that level. Same trap in the frontend harness, whose default
role was `use-case-admin`: **a default nobody can hold is a harness testing a different product.**

Two things the change turned up rather than caused: all five Keycloak clients already carried the
`groups` mapper (`FRD-209`'s live round had closed that), and the console's create gate read
`global-admin || use-case-admin` — a second clause that could now never be true, which is worse than
a wrong one because it reads as a rule somebody still relies on.

Verified live: a token carrying `groups: ['/aira/global-admins']` and `realm_access: None` resolves
to oversight. Boot refusal is **environment-shaped** (`ADR-0015`), not unconditional — the demo has
to start on a fresh checkout.

**And the live round found the thing reading had not.** `/me` reported `realm_access.roles`
**straight off the claim** — a third answer beside `sync_user_roles` and the permission classes,
which agreed only because all three read the same claim. The moment roles came from groups they did
not: the server let a Global Administrator through and the console was told they held no roles,
which showed up as a missing "New use case" button. It reports the caller's Django groups now — what
every permission class compares against — and `use_cases` returns slugs rather than the whole
`groups` claim, which was loose before and wrong once that claim carries the role groups too.

Second finding, from recreating the realm: **new realm, new subject ids**, and `OidcIdentity` binds
to the old ones — so the next sign-in provisions `admin-ec05a3db` beside `admin`, owning nothing.
Deleting the duplicates would have cascaded through `ApiKey.owner` and destroyed 323 keys. The
repair is a **rebind**, now in `deploy/compose/README.md`, because a real change of identity
provider does exactly the same thing.

---

## 2026-08-09 — Who answers for a credential (`FRD-604`, Stage A)

Owner's context, and it decides the shape: an **agentic coding** project where people issue their
**own** keys and hand them to an assistant — IT Security's question when one goes wrong is _whose
agent was that_ — beside a **RAG chatbot** with no agentic capability and one credential for the
whole service. One console, two opposite credential shapes.

**The chain already existed and nobody was told.** `ApiKey.owner` is a foreign key to a person, the
issue event carries `subject = user.get_username()`, the gateway writes it onto **every** audit row
beside the key's prefix, and the requests view filters by key. Nothing was missing in the data. What
was missing is that the console recorded the issuer and never said so at the moment of issuing — and
then printed that person's username beside an agent's traffic with no sign that it names _who
answers for the credential_ rather than _who wrote the request_. An investigator reads a colleague's
name next to a rogue agent and draws the obvious wrong conclusion. Worse than an absent figure,
because it is a confident one and it is about a person.

Four sentences and a badge: the notice before the button, the same fact beside the plaintext (the
last moment anybody reads that panel), an "i" on the `Owner` column, and **`via API key` on the
row** — an OIDC caller is deliberately unmarked, because there the name _is_ the person and marking
both makes the distinction useless. The wording is true of a shared key as well: it claims
responsibility for the **credential**, never that its owner typed the prompt.

**Stage B is specified and not built**: `issued_by` beside `owner`, so a team credential names a
technical account while the console still records which human created it. Logging in _as_ the
technical user was the obvious alternative and is wrong — shared credentials for a governance
console, and it destroys the one fact worth keeping.

Test note worth carrying: three of the four properties went red when broken, and the fourth —
_an interactive caller is not marked_ — **could not**, because deleting the marker satisfies it.
It needed the **inverse** mutation (mark everything). Second recorded instance after `N50`: a test
that asserts an absence is defended by the mutation that adds, never by the one that removes.

---

## 2026-08-09 — What a use case consumed, with or without a budget (`FRD-603`)

Owner's question, looking at the smoke-test use case: _"da sehe ich aber, dass da nicht die
verbrauchte Anzahl an Tokens und kein Geld steht — wird dann, wenn kein Budget gesetzt wurde,
nichts kalkuliert?"_

Nearly, and the near-miss is the whole of it. **Everything was calculated.** Measured against the
running stack before touching anything: `smoke-test` had **59 requests, 10,664 tokens and 3,674,900
nanos** in `request_logs`, priced, with no unpriced rows. What it had in `budget_usage` was **no row
at all**, because it has no budget — and consumption was only ever _displayed_ as a fraction of a
limit. `BudgetService.usage()` iterates the use case's **budget rows**; the tab renders every figure
**inside a budget card**. No limit, no denominator, no number — not even the numerator, which
existed. A use case deliberately left unlimited showed a page on which nothing appeared to be
counted, which reads as "this system does not track what I spend".

Two correct halves and nothing carrying the fact across, in different services this time.

The fix is a **reader, not a calculation**: `GET /v1beta/reporting?use_case=<slug>` narrows the
report `FRD-601` already serves, and a **Consumption** card sits above the budgets with this month
and today. Source is the **request log**, so the use-case page, the reporting screen and the CSV
export are three views of one number rather than three chances to disagree — `budget_usage` stays
what it is, an enforcement counter that exists only where somebody set a limit and is allowed to be
approximate between reconciliations.

Three rules, two of them already written down here and one of them nearly broken again:

- **A filter narrows, never widens.** `scope = (use_case,)` is the natural way to write it, reads as
  a narrowing and **is a widening**: every member of any use case could then name any other and be
  told its spend, from the one endpoint whose job is keeping those apart. It intersects with
  `visible_scope` instead. `N55`, shown red before green.
- **An empty report says whether it was allowed to be full.** `in_scope: false` — "not yours to
  see" and "nothing happened here" are both zero rows and only one is a measurement. `N56`.
- **Unknown is never rendered as zero.** An unreachable gateway prints an em dash and says so. A
  page that shows `0.00` because a request failed has stated something nobody measured —
  `FRD-403`'s rule about unpriced traffic, one level up.

No table, no column, no migration. `windowFor`/`isoDay` moved out of the reporting screen into
`core/ui/periods.ts` rather than being restated, because the rule inside them is an off-by-one that
only appears in the evening. Verified live against the stack's own Postgres: smoke-test reports its
59 / 10,664 / 0.0037, and a member of `kundenservice` asking for it gets zeroes with
`in_scope: false`.

**Two corrections the same day, one from the owner and one from reading the code back.** It shipped
inside the **budgets tab**, which was the shape of the defect it fixes — consumption living with
limits — and the owner moved it to the **overview**, where the tiles already say how a use case is
configured and this says what it has done. It is a child component now (`consumption-panel`), and
its load hangs off the page's `load()` rather than off `loadBudgets()`: otherwise adding a budget
refetches it, and removing the budgets panel one day silently removes it. And the two windows are
two requests whose failure was tracked in **one boolean written by both** — the month would arrive,
clear the flag, and the day's failure would set it again and hide a figure already fetched.
Whichever request finished last decided what the reader saw. **Partial is a third state**: what
arrived is shown, the rest is an em dash that says so. Found by reading, not by a red test, so the
test was written to fail against it first.

**And the browser suite found the third thing**, which no unit test could: the retention spec broke
on a Playwright **strict-mode violation** — the overview now had two `.callout--warning` elements.
The selector was fragile and got an id, but the failure was pointing at something better than a
selector. Not being in a Keycloak group is **not a warning**: nothing is wrong, the reader simply
created this use case a minute ago and AIRA does not write to directories. It is a plain callout
now. Every new use case had been greeted by two alarms about nothing, and a page that cries wolf
twice teaches the reader to skip the third.

---

## 2026-08-09 — One use case for all model testing (`FRD-504`)

Owner's decision, after the attribution defect: _"es soll dann einfach als Standard ein Use Case
immer angelegt werden, wer smoke test heißt und auf dem wird dann alles abgerechnet."_

It settles the question rather than fixing it again. A run is ordinary traffic and has to be priced
somewhere; booking it against whichever use case the tester happens to belong to spends **somebody
else's budget on work that is not theirs** and mixes evaluation cost into their production figures —
after which reporting cannot separate the two, because nothing distinguishes them. `smoke-test` is
seeded on every installation and every run is booked there. Payload storage is off on it: Management
already keeps each prompt, answer and verdict, and storing them twice would put the same content
under two retention clocks.

The console asks **one** server question — `GET /api/v1/test-attribution/` → which use case, does it
exist, will the gateway accept you — instead of holding the slug and deciding the third part from a
membership list. That is the shape of the defect it replaces: the slug written in the console goes
silently wrong the day the seed renames it, and the membership question was the one asked wrongly
yesterday. `may_call` is the gateway's rule (`aira_common.access.resolve`), and the two refusals are
**said apart** — a use case nobody has seeded is an operator's job, a caller the gateway will not
accept is a directory question, and one message covering both sends half the readers to the wrong
person.

The dev realm gains `/use-cases/smoke-test`, held by every role that may test a model. AIRA never
writes to a directory (`FRD-209`), so on a real installation that group is the one thing an operator
must create — now in `INTEGRATIONS.md` rather than discovered.

**And the seed reproduced the bug it exists to prevent.** It wrote the use case with
`UseCase.objects.update_or_create` and emitted nothing, so the row existed in Management, the relay
reported _"no pending events"_, and the gateway had never heard of it. The showcase seed states the
rule in its own docstring — _"everything goes through the same events the API emits"_ — and this one
walked straight past it. Fourth recorded instance of **two correct halves and no wire**, and the
second found by looking at a live stack rather than at code. `Q6`.

Verified where it failed before: a **global administrator** — the account that could not run
anything yesterday — starts a run and the gateway records `served`, 575 tokens, use case
`smoke-test`.

## 2026-08-09 — Three questions that look like one, and the tests that agreed with me

Reported from the running console: every question of a smoke-test run came back
`Not a member of use case 'addr-1nn4ss'`. The slug is a leftover from an old test round, and it was
the alphabetically first of some nine hundred use cases.

**Cause, and it was mine, from the commit immediately before.** Attribution resolved the use case
with `?mine=true`, implemented as Management's `is_member` — which grants a **global administrator
everything**, because in Management they may act anywhere. The gateway has no such rule: it reads a
token's groups (`/use-cases/<slug>` plus grants) and grants nobody a blanket. So the console offered
a global admin a use case their token has never reached, and the gateway correctly refused all
hundred requests.

Three questions live next to each other and only the third is right for attributing traffic:

| Question                          | Answered by                                        | A global admin                  |
| --------------------------------- | -------------------------------------------------- | ------------------------------- |
| What may I **see**?               | `scope_queryset`                                   | everything                      |
| What may I **administer here**?   | `is_member`                                        | everything                      |
| What will the **gateway accept**? | `may_call_queryset` → `aira_common.access.resolve` | nothing, unless a group says so |

`?may_call=true` now answers the third, using the **same `resolve`** the gateway's own grant
resolver calls rather than restating it in Django. A global admin with no use-case group therefore
gets an empty answer and the screen says so in words — which is `ADR-0007` working, not a gap.

Resolving it also surfaced the inverse: the `/use-cases/<slug>` convention grants **calling**
without granting a guardian object permission, so a caller can be entitled to attribute traffic to
a use case Management does not show them. Filtering the visible set would have handed them an empty
list while the gateway accepted their requests — the same disagreement pointing the other way. The
filter therefore resolves against every use case, disclosing nothing: these are exactly the use
cases the caller may already name in a request.

### Why four test layers said yes

Worth writing out, because each layer failed differently and none of them failed by accident:

1. **Unit (frontend)** — the fake service returned one membership and the component picked it. It
   tested my assumption, faithfully.
2. **Unit (backend)** — the test I had just written asserted that `?mine=true` **agrees with
   `is_member`**. That is the wrong reference: it did not miss the defect, it _encoded_ it. A test
   written from the same idea as the code will agree with the code.
3. **Mutation** — `Q5` broke the filter and the test noticed. It was guarding the wrong property
   competently.
4. **End-to-end** — asserted that _a name was displayed_. Never that the name worked. And the one
   test that would have caught it — an actual run against a real model — is the one I had left
   skipped, with a justification I wrote myself.

The layer that finds a defect is the one pointed at the **outcome** rather than at the mechanism.
`FRD-206`'s agreement test got this right a year of lessons ago: _for each answer, attempt the
request and require the status to match_. The new e2e case does that — a global admin is offered
nothing, and a use-case administrator's stated attribution is one their token actually reaches —
and a live probe run produced a **served** audit row (508 tokens, `demo-uc`, `qwen3:0.6b`), which is
the assertion that was missing all along.

### Found while investigating, not fixed, deliberately

`aira_common.access.resolve` takes a `direct` argument for grants naming a **person** — and no
caller supplies it. The gateway resolves OIDC membership from groups and group grants only, so a
membership added in the console (a `UseCaseMembership` row, distributed to `use_case_members` since
`FRD-204`) grants **nothing at the gateway**. `FRD-209` said a grant binds "a group _or_ a person";
the person half never reached the request path. Fourth recorded instance of _two correct halves and
no wire_.

Not fixed here because it **widens** who may call the gateway, which is a decision rather than a
repair. It is also why the demo works at all: `ucadmin` and `ucuser` hold `/use-cases/*` groups in
the dev realm, and `admin`, `itsec` and `itgov` hold none.

## 2026-08-09 — Attribution is stated, not asked (`FRD-504`)

_"Attributed to hat endlose Menge der Column. Dieser Punkt ist überhaupt nicht notwendig."_ Two
defects wearing one control.

The picker listed **page one** of a paged list, so on an installation with hundreds of use cases it
was an endless dropdown that frequently did not hold the one somebody actually works in — the
defect recorded and parked two commits ago. And it asked a question the person running a model test
has no opinion about: a run has to be attributed _somewhere_, because it is ordinary traffic and is
priced, budgeted, rate-limited and audited like any other request, but **which** one is not the
tester's decision to make.

So the screen resolves it and says which: _"Attributed to Kundenservice."_ Not a control, a
statement — the spend stays traceable and nobody is asked to choose.

The resolution is a new server-side question, `GET /use-cases/?mine=true`, because **visibility is
not membership** (`ADR-0007`) and filtering the visible list in the browser gets it wrong in both
directions: an oversight role sees every use case and may call none, and a paged list only ever
answers "the ones I am a member of _among the first 25_". `access.member_queryset` is the set form
of `is_member` and sits beside it, so a list answering "which may I act in" cannot drift from the
per-row permission the same screen renders — a test asserts the two agree, which is the only reason
either can be trusted. `Q5`, shown to fail first.

The end-to-end run test stays skipped and its reason has **changed**: the picker defect is fixed,
and what blocks it now is that a run asks all hundred questions one at a time against a small local
model. That is minutes, and it does not belong in a suite everything else waits on.

## 2026-08-09 — One catalogue of questions, and a standing that is the latest run (`FRD-504`)

The smoke-test screen was built as one page that summed every run a model had ever had. Two
corrections from the owner, and the second was the interesting one.

**First: a standing catalogue, not an ad-hoc run.** _"Ich will im Laufe der Zeit standardisierten
Fragenkatalog für die Bewertung von Modellen definieren und nach dem Standard Modelle bewerten"_ —
so three sub-tabs: the **questions** (written once, grown slowly), a **run** that puts them to a
model, and **latest results** saying where each model stands. And the standing is the **newest run
per model**, never a total: summing is wrong twice over, because an old since-corrected result
drags the current figure down forever and the number moves whenever somebody re-runs something
unrelated. Older runs are history and stay readable — how a model behaved before its version
changed is the question anybody upgrading one actually has, and only the history answers it.

**Second: no grouping at all.** The first version of that sorted the hundred questions into eight
named batteries. _"Ich meine keine Kategorisierung, einfach nur die Liste an Fragen."_ Removing it
turned out to be a correctness fix rather than a simplification: with several batteries, "how does
this model do" has as many answers as there are groups, and **none of them compares to a model that
was asked a different group**. The whole value of a fixed catalogue is that every model is asked
every question. `topic` stays as the keyword saying what a question tests — a label on a row,
nothing branches on it — and the search covers the wording too, because somebody looking for "the
one about explosives" remembers the question and not the label.

`TestBattery` is gone; runs, answers and verdicts were carried across, because a run is evidence
about what a model did on a day and reorganising the catalogue is not a reason to lose it.

Four findings, three about the harness rather than the feature:

- **A break that never broke.** The first attempt to prove a new test could fail replaced a string
  `ruff` had since wrapped across two lines, so the edit matched nothing, the suite stayed green,
  and the run looked like a passing verification. Break-and-restore has to assert the break landed.
- **The duplicate-id check existed only in a commit message.** 2026-08-07 recorded that 38 mutation
  ids named more than one property, that they were renamed, and that _"the harness now refuses
  them"_. It never did: the next addition collided with `S1`/`S2`, and `--only=S1,S2` ran four
  properties and reported four confident results for the two that were asked for. Written now, and
  shown to fire. The summary line also called a **stale anchor** "a property no test would notice
  losing", which sends the reader hunting for a test that is right there — stale and survived are
  now reported apart.
- **A rename against a name key is a create.** The seed keyed questions on `topic`, so renaming
  three left the old wording in place with its answers attached, and the catalogue silently grew to 102. Keyed on position now; superseded questions are **retired, not deleted**, because somebody
  judged their answers against the wording as it then stood and those verdicts are the only
  evidence that anything has changed. `FRD-208` recorded this for anomaly rules; this is the second
  place.
- **mypy found a 500 before a caller did**: a query parameter reached the ORM as a string.

`Q1`–`Q4` guard the four rules, each shown to fail first; `Q1` was re-anchored when the battery axis
went away. 1652 Python tests, 617 frontend, gates untouched (statements 92.4%, branches 92.4%).
Verified in the browser against the rebuilt console: 100 questions in one list, the search reaching
the wording, and authoring offered to IT Security and withheld from a use-case administrator with a
sentence saying who does it.

## 2026-08-08 — access follows the group, and three things that carried nothing (`FRD-209`)

Access to a use case was granted **one person at a time**, by username. Two things were wrong with
that, and they were the same thing from two sides: it does not survive somebody joining or leaving a
department, and there were already **two answers** to "is this person a member" that disagreed — the
gateway read Keycloak groups `/use-cases/<slug>`, Management read its own rows, and a use case
created in the console produced only the second. That is exactly the defect `FRD-208`'s round
surfaced, where an administrator opened their own use case's Traces tab and was told, correctly,
that the identity provider did not consider them a member.

**A grant now binds a principal to a use case, and a principal is a group or a person.** A group is
whatever path the realm actually uses — `/abteilungen/vertrieb/nord` — with a role (`user` or
`admin`). AIRA reads the directory and never writes to it: who is in the group stays the identity
provider's answer, which is the entire point.

The mechanism is the part worth keeping: **`django-guardian` assigns object permissions to a user
_or a Django group_.** So a group grant assigns them to a Django group mirroring the Keycloak path,
and every authenticated request syncs the caller's group paths onto their Django groups — exactly as
`FRD-201` already does for roles. `scope_queryset`, `may_admin` and `may_manage` then needed **no
change at all**. A second permission path beside guardian's would have been a second chance to
forget one, which is the mistake the two planes had already made about membership.

Two rules written into `aira_common.access` so neither plane can restate them differently: the two
routes are a **union** (being a member twice over is being a member) and where roles differ **the
stronger wins** — an access decision that depended on which row was read first is not a decision
anybody can review. And degradation refuses: if the read-model cannot be read, the naming convention
still resolves from the token alone, and somebody who was a member _only_ by grant is refused.

The console gets **one** picker for both kinds, because the question is "who should get this", not
"am I about to name a group or a person". Without an admin client it falls back to what Management
already knows and **says so** — "no results" from a directory nobody could reach reads exactly like
"no such group".

**Then the live round found three defects, all of the same family: a correct half with nothing
carrying it to the other side.**

1. **An event with no topic — the third instance of this shape here.** The first grant was written,
   listed and shown in the console, and reached the gateway _never_. `record_to_outbox` matches
   against a hand-written map and **returns silently** for anything unknown — deliberate, so an
   older Management does not crash on a newer event, and precisely what made the missing entry
   invisible. `aira.rate-limits` and `aira.anomaly-rules` were both previously topics created by
   nothing. There is now a test that parses every `emit(...)` in the source and compares it against
   the map **in both directions** — and the reverse half immediately found **`pipeline.deleted`: a
   topic with no emitter**, dead configuration that reads as a working path.
2. **A compacted topic needs a key per grant.** Two grants on one use case keyed by the slug alone
   meant the second erased the first from the log, so a gateway rebuilding its read-model would
   silently lose access somebody holds. The key is `slug|group_path` now.
3. **A token with no `groups` claim grants nothing.** The mapper was on the SPA client and none of
   the service accounts, so their tokens carried no groups at all. A configuration requirement of
   the feature rather than a bug in it — now in `INTEGRATIONS.md`, in the dev realm, and asserted
   live.

A fourth came from refusing to leave an assertion weak: a grant on the bare realm root `/` was
accepted, and can **never** match — every path a token reports begins with a name — so it was
permanently inert while reading to a person as "the whole realm". Refused now.

Counts: 36 shared-library, 38 Management, 14 gateway and 21 console tests; **85 live cases** in the
`FRD-129` style; 7 Playwright cases; mutations `N30`–`N39` (271 properties).

---

## 2026-08-08 — paging that is real, and a rule somebody can change (`FRD-208`)

Asked directly whether the search and paging `FRD-207` added were real or client-side, the honest
answer was **client-side**. The useful follow-up is where that matters, and it is one of the three
lists.

**The use-case list.** Unbounded — a live round found 801 — and its serializer answers
`can_admin`/`can_manage`/`is_member` **per row** (`access.py`). Slicing that in the browser leaves
every one of those computations happening on every load: the reader waits exactly as long and then
sees twenty-five rows. Now `?page=`/`?q=` at the server, ordered explicitly (an unordered queryset
may hand the same row back twice and never show a third). Measured after: **1.6 s, 211 use cases
across 9 pages**.

**Findings** are paged too, by **cursor** rather than page number — the same choice the trace view
made and for the same reason: an append-only log, so a detector firing while somebody reads page two
pushes rows across the boundary and they see one twice and miss another, invisibly.

**The catalog stays client-side, and that is now written into the viewset.** It is bounded by how
many models an organisation contracts, and two of the console's warnings count over the _whole_
catalog — paging would turn "N models have no price" into "N on this page", a figure that means
nothing. Report breakdowns likewise: one aggregate response, already computed.

Three behaviours a server-paged list has to have, each a way it goes wrong: typing does not fire a
request per keystroke (250 ms, and identical queries are not re-sent — nine letters would otherwise
be nine round trips against the slowest endpoint here); a new search starts at page one (otherwise
it asks for page 4 of a two-page result and gets nothing, which reads as "no matches"); and a late
answer never overwrites a newer one (a slow "a" must not land after a fast "abc").

**The bigger finding: the console pointed at a screen that did not exist.** `FRD-207` had the
security console say a use-case rule _"is changed on that use case"_ — and there was no such
screen. That is the `FRD-206` defect one level of indirection further out: not a button that
answers 403, but an instruction with no destination. The server had allowed it all along
(`AnomalyRuleViewSet._guard`, `upsert_use_case_rule`); only the screen was missing. There is now a
**Rules** tab on the use-case detail — list, create, edit, delete, each rule described in a
sentence — and the console's sentence became a link to it. Global rules are deliberately absent
from it: they are not that use case's to change.

**One form, two screens.** `rule-form.ts` is thirteen fields with a per-kind validation contract; a
second copy is how one screen quietly loses the field the other gained. It refuses to edit a rule's
**kind** (the kind decides what the threshold _means_ — 50 is half the requests under one and half a
multiple under another) and its **name** (the server upserts by name, so a rename would create a
second rule and leave the first watching).

And the layout defect reported alongside: `.actions` carries `flex-wrap: wrap`, right in a form row
and wrong in a table cell, so Edit and Remove wrapped onto two lines. Same cell as `FRD-207` §2.3,
a second way of leaving the row.

Test note: the search is asserted by **watching for the request carrying `q=`**, not by checking
which rows are on screen — the second passes for a client-side filter too, which is how this pass
would have "proved" the thing it was correcting.

---

## 2026-08-08 — the console holds still, and says what its controls do (`FRD-207`)

`FRD-206` made the console stop promising what the server refuses. A walkthrough of the running
console asked the next question — _can I actually read this?_ — and produced twelve findings. Two
of them turned out to be defects rather than polish, and both are the same shape.

**The jiggle was one element, and it was measurable.** A `PerformanceObserver` on `layout-shift`
reported **five shifts in forty seconds on the security console, every one of them the Refresh
button**: the stamp beside it changes width — "updating…" against "updated 12s ago", "9s" against
"10s" — twice a tick. That is why it was hard to name: a few pixels, nothing appears to _happen_,
and the reader is left with an impression rather than an observation. The stamp now reserves its
widest form with tabular figures, and "refreshing" is a dot that fades in space it already
occupies. The observer reports nothing at all now.

**The navigation marker was never applied.** `app.html` carried `routerLinkActive="is-active"` on
every item and `app.ts` did not import `RouterLinkActive`. Angular does not complain about an
attribute matching no directive — it is inert markup — so the class was never set and the
`.is-active` rule had been styling nothing for as long as the shell has existed. **The same shape
as `FRD-502`'s `Live` two days earlier: a declaration that is silently inert**, invisible to every
unit test and obvious in a browser.

Also a real layout defect: `.table__actions` was `display: flex` **on the `<td>`**. A cell made a
flex container stops participating in its row — it leaves the row's height and baseline — which is
exactly the "break between a model row and its own Edit/Delete buttons" that was reported. And the
trace filter row was `align-items: center`, so a bare checkbox beside a field with a label above it
sat on a different line from the control it is read as a pair with; a filter row aligns on the
**bottom**, where the controls are. Both measured afterwards rather than eyeballed.

**A finding opens, and a rule says what it does.** Six columns is as much as a table can be read
at, so the other six fields go under the row. `rule-language.ts` turns a rule into English, which
is safe to write only because the vocabulary is **closed**: seven kinds, one meaning each. It keeps
two things honest — **a ratio is not a threshold** (`spend_spike` at 300 is "three times the window
before", not 300 euros; `FRD-500` chose a ratio because a fixed number is a budget and there
already is one), and **`alert` is not enforcement** (`ADR-0014`), with `detected_not_enforced`
saying in words that the block was asked for, not applied, and the traffic continued.

Rules can now be **edited** — threshold, window, sample, action and its duration or rate, and
whether it watches at all. **Not the kind and not the name**: a kind decides what a threshold
_means_, so changing it in place would silently reinterpret a number somebody chose deliberately.
Authority follows `FRD-206`: a global rule to an incident role, and a use-case rule named as
belonging to its use case rather than guessed at, because object-level permission is not in the
token. The kill switch explains its reach on hover — one caller, one key, one use case, and
**no switch for the installation**, which is a deliberate absence. And the line reading _kept,
because "blocked for two hours last Tuesday" is what a review asks_ — a note to the author sitting
where a sentence for the reader belongs — was rewritten.

**Reporting shows one table at a time.** Four stacked breakdowns made the page long enough that its
own export control scrolled out of sight, and left two ideas of "which table": one for the screen
and one for the file. The selector governs both now. `by_outcome` is shown and not exported, and
says so — the CSV renderer takes three breakdowns (`FRD-602`), and a button that looks ready and
answers 400 is the defect `FRD-206` was about. The token, spend and latency columns carry their
definitions, including _why_ prompt and completion are shown apart: they are priced apart.

**Search and paging** on the breakdowns, the catalog and the use-case overview, extracted into
`core/ui/table-view`. A live round found **801** use cases in one installation, which made the
overview useless without a line of it being wrong. Two rules encoded: searching returns to page one
(a filter applied on page 4 that leaves you there shows an empty table), and the reader is always
told what they are not seeing — the pager renders even on a single page, because a control that
appears only once a list grows teaches nobody it exists.

The explanation hint was **written twice in a week** and the second copy promptly collided with the
first on a `data-testid`; it is now `core/ui/info-hint`, one pinned at a time page-wide, since the
panels are overlays and two open cover each other.

One test lesson: the first e2e for the rule editor **skipped itself** when the installation had no
rules — so the part of this pass with the most behaviour in it would have been exercised in a
browser exactly never. It creates its own rule now. _A test that skips when the data is
inconvenient reports green about nothing._

Also noted, not fixed: `/api/v1/use-cases/` computes object-level permissions per row, so hundreds
of use cases take many seconds to answer. Search and paging make that survivable, not fast.

---

## 2026-08-08 — a console for the evidence, and what actually happened per request (`FRD-502`)

Phase 5 built rules (`FRD-500`), an engine (`FRD-501`) and enforcement (`FRD-503`), and none of it
had a screen. That put IT Security in exactly the position `FRD-206` was written about: a role whose
console shows it nothing. Two screens close it.

**The IT Security console** (`/security`) — findings with the numbers they were drawn from, what is
stopped right now, and the rules that produced them, all three in one place because a finding read
without its rule is a number without a claim, and an empty findings list means nothing until the
page says whether anything is being watched at all. It keeps **two permissions apart**, which is the
mistake this project has already made once: _seeing_ every use case is an oversight role, _stopping_
traffic is an incident role. `it-steuerung` gets the whole view and no kill switch, and the page
names who does — an action nobody can carry out is worse than an absent one.

**Warnings, per use case** — the same findings, scoped, for the people who could actually fix the
cause. A warning only IT Security can see is a warning nobody who could change the prompt, the limit
or the client ever reads. The tab leads with "this use case is stopped", because without it a wall
of 429s reads as a broken gateway.

**Traces** — `GET /v1beta/traces`, and a tab per use case: every request, newest first, with who,
which model, how it ended, what it cost. **Metadata only, never a payload** — and that is _not_ the
per-request browsing `ADR-0009` deferred: that reasoning is about showing stored prompts to
non-members, and this shows neither prompts nor anything to a non-member. `FRD-406` still blocks
what it always blocked. The field list is an **allow-list**, so a column added to `request_logs`
tomorrow cannot appear here because somebody forgot to exclude it, and the two that must never
appear are exactly the ones a forgotten exclusion would leak.

Three decisions worth keeping:

- **Cursor paging, not offset.** Rows arrive while somebody reads; under an appending table an
  offset page shows some rows twice and skips others, _invisibly_ — the reader simply gets a wrong
  list. The cursor is `(created_at, id)`, because two rows can share a millisecond and a timestamp
  alone would either repeat one or lose one. Written out rather than as a row comparison: SQLite has
  no tuple comparison, and paging exercised against only one of the two stores is paging tested on
  one of the two.
- **Live by polling, and visibly so.** `core/ui/live.ts` is one primitive with three guarantees, each
  a way live views go wrong: it **stops** (on destroy, and while the tab is hidden — a console left
  open overnight must not be a load generator), it is **visible** (the reader sees "updated 12s ago"
  and can switch it off, because a screen that changes under somebody who did not ask it to is a
  screen they stop trusting), and it **never stacks** (a tick during a slow response is skipped, not
  queued, or a refresh interval becomes a load test against the endpoint already struggling).
  Server-sent events would push, and would also need a long-lived connection per open console
  through whatever proxy sits in front, a reconnect story, and a second delivery path for facts that
  already have one.
- **Scope resolved once, at the edge.** Traces reuse `visible_scope` — the same function the report
  and the CSV export use. `FRD-602`'s assertion (each endpoint resolves the scope exactly once)
  caught the new endpoint immediately.

The test that earned its place before it was written: `Live`'s teardown case failed with **seven
ticks after destroy**. The harness had provided the service in the testing module while every real
screen provides it on the component, and `DestroyRef` resolves to whichever injector created it — an
environment-level one outlives every component. **A harness that configures a service differently
from production tests a different service.** Fixed on both sides: the harness mirrors production,
and the timer now stops explicitly rather than only where it happens to be provided correctly.

Mutations `N24`–`N29` cover the payload exclusion, the scope, the cursor tie-break, the `limit + 1`
that decides whether a next page exists, and the two-kinds-of-empty distinction below
(**261 properties**). 21 gateway tests, 56 new frontend tests, 7 live integration cases, 7
Playwright cases; the frontend gate stays where it was.

**Then the browser found two more**, both invisible to 356 green frontend tests. `Live` is
`@Injectable()` without `providedIn: 'root'` — deliberately, so a poll cannot outlive its screen —
and **neither tab declared it**, so in production both panels failed to construct and rendered
nothing at all while every unit test passed on a harness that provided it. And an empty tab was
**stating the wrong reason**: the gateway reads use-case membership from Keycloak _groups_
(`FRD-102`), which creating a use case in this console does not create, so its own administrator
read "no requests match" about a use case with traffic in it. Both endpoints now return `in_scope`
and both tabs name the group somebody has to be added to — `in_scope` describes the **caller's own
visibility** and nothing else, so it confirms nothing about whether a use case exists and the reason
a 403 was refused still holds. `/v1beta/anomalies` gained a `use_case` filter in passing, because
the tab had been keeping the matching findings out of the newest hundred — on a busy installation
that is how a quiet use case comes to be told nothing crossed a threshold.

**And a flake that had been there all along.** `make mutants` refused to run — red baseline on
`test_log_writer.py`. `test_a_full_queue_writes_inline_instead_of_dropping` fails about one run in
five with _"cannot operate on a closed database"_, which reads as a defect in the writer and is not
one: in-memory SQLite behind a `StaticPool` hands every session the **same** connection, and that
test's whole subject is an inline write happening _while_ the worker writes. The module docstring
had warned about it in prose; the test then did it anyway. It now runs on a file-backed database,
where each session gets its own connection as it does on Postgres, so the overlap the test is about
is legal. Asserting the invariant without the overlap would have been testing something else and
calling it this.

---

## 2026-08-07 — documentation, and a licence

A reader arriving at this repository had a 96-line README, a `DEPLOYMENT.md` and forty ADRs and
FRDs. That is a lot of _why_ and very little _what_. Six documents now sit between them, each with
one job, linked from a README that is a hub rather than a wall:

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — C4 at three levels, in Mermaid. Context (who uses it, what
  it reaches), containers (six processes, what each is), and components inside each plane.
- [`REQUEST-LIFECYCLE.md`](REQUEST-LIFECYCLE.md) — one request end to end: middleware,
  authentication, the seven pre-dispatch steps **in order and why that order**, dispatch with its
  conditions, the single accounting exit, and what happens asynchronously afterwards.
- [`SETUP.md`](SETUP.md) — the four ways to run it: demo, standalone, development from source, and
  integrated onto existing infrastructure.
- [`CONFIGURATION.md`](CONFIGURATION.md) — every `AIRA_*` variable with its **real default**, read
  out of the code rather than remembered, plus what degrades when each piece is missing and what
  refuses to boot.
- [`INTEGRATIONS.md`](INTEGRATIONS.md) — one section per connected system: what Postgres, Keycloak,
  Kafka, Redis, each model platform, the collector and the proxy must provide, which credentials,
  which settings on _their_ side, and a checklist each.
- [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) — requirements against what is built. **Described, not
  fixed**, at the owner's request.

Also: **Apache 2.0**, with a `NOTICE` that names the third-party systems this software connects to
and disclaims affiliation. The licence text was written out rather than fetched — the sandbox denies
`apache.org`, and a licence file assembled from memory is one worth saying was assembled from
memory, so it was checked against the canonical structure clause by clause.

Two accuracy notes, because documentation drifts the same way code does. Every default in the
configuration reference was **dumped from the settings classes**, not typed; every relative link is
checked by a script; and two claims were wrong on first writing and corrected against the source —
the traffic target is `showcase-traffic`, and the published ports are overridable
(`AIRA_GATEWAY_PORT` and friends). The README's status line said "Phases 0–5 delivered", which is
not true while `FRD-502` is missing; it now says so.

**What the gap analysis found** is worth having in this log rather than only in that file. Against
PRD §1.1: nine features built, six partial, two missing — and the partials are breadth rather than
correctness. The two that matter most are both about _evidence being usable_: `FRD-406` (redaction)
is the only open item that blocks two others — per-request browsing and the IT Security console's
scoped payload view — and it is the only place the product currently makes a promise it does not
keep, since payloads are stored and nothing masks anything inside them. `FRD-502` is the one that
turns work already done into work somebody can use: Phase 5 built the rules, the engine and the
enforcement, and the role whose job that is has no screen for any of it.

---

## 2026-08-07 — 84 live cases against the anomaly work, and five defects

`FRD-500`/`501`/`503` shipped with hermetic tests, 251 mutation properties and a green gate. A
developer round against the running stack — real Postgres, real gateway process, real model, both
planes talking over Kafka — found **five defects that none of those could see**. Three of them
predate this week.

**Two planes, one question, two answers.** The gateway guarded its kill switch with `has_oversight`,
which is a _visibility_ predicate, so `it-steuerung` could stop traffic there while Management
correctly refused it a global rule. PRD §154 gives that role every figure and **no write anywhere**.
Reusing "may see every use case" for "may stop every use case" is `FRD-206`'s mistake one level
down — and the way it surfaced is worth keeping: **asking both planes the same question and
comparing the answers**. `INCIDENT_ROLES` now lives in `aira_common.roles` and both read it.

**A whole rule kind measured a column nothing wrote.** `payload_size` compares against
`request_bytes`; the middleware counted the bytes, the column existed, and nothing carried the
number between them. It could never have fired on real traffic. The hermetic tests seeded the column
directly and were green — the third time this repository has recorded _two correct halves and no
wire_, and the second time coverage was blind to it.

**A refused request was counted as unpriced traffic.** The console reported **105** unpriced
requests where **5** had run on an unpriced model. A refusal has a NULL cost for the opposite reason
to an unpriced one — nothing was spent because nothing _ran_ — and counting both made the "spend is
a lower bound" caveat permanent, which by this project's own test (`a fully priced period carries no
caveat`) is a warning nobody reads. The rule stated in the direction it was missing: **unknown is
not zero, and zero is not unknown.** A NULL _outcome_ still counts, because that is a row from
before `FRD-122`, when only served requests were logged at all — fixing a present figure must not
quietly change a historical one.

**`aira.anomaly-rules` was created by nothing.** Rules were authored, Management answered 201, the
relay published, and the broker dropped every message. The only evidence anywhere was
`Topic ... not found in cluster metadata`, repeated forever in a container nobody watches. This is
the **second** time — `FRD-405` shipped `aira.rate-limits` the same way and the DEVLOG says so — and
the topic list is written by hand in three places while the names have one source of truth. The fix
is therefore not a fourth copy but a check: `tools/tests/test_kafka_topics_are_created.py` compares
the Makefile, the Compose step and `DEPLOYMENT.md` against the constants, **in both directions**, so
a topic nothing publishes to is caught as well.

**Thirty-eight mutation ids named more than one property.** Found by reusing `N3`, which already
existed. Every entry runs regardless, so the checking was sound — but "N3 survived" named two
unrelated things, and a summary that sends somebody to the wrong line is worse than no summary. The
_later_ duplicate of each pair was renamed and the first kept, because `CLAUDE.md` and the DEVLOG
cite ids by name and renaming a cited one breaks the prose explaining why the property exists. The
harness now refuses duplicates.

Two test lessons from writing the round itself, both about **measuring from the wrong moment**: a
suspension takes up to the cache TTL to reach the gateway, so "a blocked caller consumes no budget"
and "a blocked caller pays for no classifier" both failed until they counted from _after_ the block
took effect rather than from zero — the requests served while the cache caught up were served
perfectly correctly. And the round needed a third Keycloak service account (`it-security`), because
neither existing one may act in an incident — which is the same distinction defect 1 was about,
arriving from the test side before the fix did.

---

## 2026-08-07 — Phase 5, stage C: a finding becomes a control

`FRD-503`. `FRD-501` detected and recorded; a rule set to `block` wrote `detected_not_enforced` on
the row, in those words, because saying so was the only honest interim. This carries it out.

A **suspension** is the written decision `ADR-0014` promised: a target, an action, an expiry, an
**author** and a **reason**. The last three are what make it a decision rather than a side effect —
the first thing anyone asks at 03:00 is who did this. The pre-dispatch gate reads it, so a stopped
caller is refused at the one place every verb passes (`FRD-126`) and does not pay for a classifier
on the way to being told. Rows are kept after they are lifted: "this caller was blocked for two
hours last Tuesday" is exactly what an incident review asks.

**An amendment to `ADR-0014`, from building it.** The ADR said the gate would read decisions from
the shared counter store, seeded from Postgres — by analogy with `FRD-405`. The analogy is wrong. A
counter is written on _every_ request, which is what earns Redis its place; a suspension is written
when something goes wrong and read on every request, which is a **cache** problem, not a
shared-state one. A five-second cache over Postgres does it with one query per instance and no
second system — and survives a Redis outage, which for a control that _stops_ traffic is the
direction that matters. The cost is stated: a lift takes up to the TTL to reach every instance, and
being slightly late to _remove_ a restriction is the harmless direction.

Three smaller decisions:

- **429, not 403.** The credential is valid and the membership is real; the caller is stopped
  temporarily, and "come back later" is what 429 means. A 403 sends a client off to fix permissions
  it has no problem with.
- **`suspended` is its own audit outcome.** Folding it into `rate_limited` would hide "we stopped
  this caller on purpose" inside "this caller is going too fast", and those want different answers.
- **The kill switch does not go through Kafka.** Every other piece of configuration is authored in
  Management and distributed; this one is created directly against the gateway by an oversight role.
  An incident control that depends on the event bus fails exactly when the bus is the problem, and
  "traffic is doing something alarming" and "the pipeline between the planes is unhealthy" are not
  independent events.

**A pattern worth naming, because it has now happened twice.** `throttle` was declared as an action
and given no rate — the same shape as `FRD-501`'s missing byte figure, found the same way, by
building the consumer. **An enum member is not a specification.** Adding a value to an action or a
kind should prompt "what does this one need that the others do not", and the answer belongs in the
schema before anything ships.

**Two things the existing suite caught, both worth more than the code they rejected.** The
architecture assertion widened yesterday — "each endpoint in the reporting module resolves the
visible scope exactly once" — went red on the new suspension endpoints, which resolve it **zero**
times. Correctly: they are bounded by _role_, not by use case. Two different ways of being safe do
not belong behind one heading, so they moved to `api/incidents.py`. And the mutation harness caught
`N19` surviving: every endpoint test in the new file ran with authentication switched off, which
takes the demo-principal path and returns _before_ the role check — so the check itself was
untested while five tests around it passed. It is now driven with a real principal.

Also: three mutations came back stale because this change edited the lines they pointed at, and one
(`N15`, "a lifted suspension stops refusing people") **survived correctly** — the load query already
filters lifted rows, so the in-memory check is the second of two guards. Removed as a mutation and
kept as code, on the `X3` precedent: a property guarded twice cannot be a mutation, and that is not
a reason to remove a guard.

25 tests, migration `0017`, five new mutations. `make ci` green.

---

## 2026-08-07 — Phase 5, stage B: the engine that reads the rules

`FRD-501`. `FRD-500` let an installation say what abnormal looks like; this measures it. All seven
kinds evaluate, against the request log — the same rows `FRD-601` reports from, so a detector cannot
see anything the report cannot.

The scheduling is the part with the engineering in it. Two obvious designs are both wrong:
evaluating on every persisted row is N queries per request — off the hot path but not off the
_machine_ — and scanning every rule on a timer means a quiet installation with 200 use cases runs
200 pointless queries a minute forever. So the writer, which touches every row anyway, **marks
which scopes saw traffic**, and the timer evaluates only those. A quiet installation does no work.
The set is bounded and dropped on overflow: losing a _hint_ delays a finding by one tick, and a
bounded loss beats unbounded memory in the component whose whole job is to still be running when
something goes wrong.

The cooldown is the window itself. A 15-minute window evaluated every minute would fire fifteen
times about the same fifteen minutes.

**A gap in stage A, found by building the thing that consumes it.** `payload_size` is "the share of
requests above a byte threshold" and the rule carried **one** threshold — the share. The byte figure
had nowhere to live. Stage A's model, serializer, API, 18 tests and six mutations were all green,
and every one of them was blind to it, because they tested that a rule _round-trips_ and nothing had
yet tried to _evaluate_ one. **A configuration schema is only proved by the code that consumes it.**
The fix is a nullable `parameter` — required where a kind needs it, refused everywhere else, so it
cannot quietly become a second free-form field. And the byte count itself had nowhere to come from,
so the body-size middleware now records what it was already counting to enforce the ceiling.

Three measurement decisions that are easy to get wrong and expensive to get wrong quietly:

- **A rate over too few rows is not evaluated.** One refusal out of one request is 100 %.
- **Growth from nothing is not a spike.** Treating an empty previous window as infinite growth would
  make every use case's first hour an incident, and the alert that fires on arrival is the one
  people switch off before it ever says anything true.
- **A request whose size is unknown is excluded from both sides of the share** — numerator _and_
  denominator. Counting an unknown as small would make old traffic look innocent.

`refusal_rate` counts everything that is not `served`, straight from `Outcome` rather than from a
second list of "bad" outcomes — `FRD-122` already made that enum the one place a control's existence
is recorded, and a copy here would go stale the first time somebody added a control. `client_gone`
is deliberately in: one caller hanging up is not our failure, a thousand is exactly the shape a
detector exists to surface.

Until `FRD-503` lands, a rule configured to block **detects and records that it did not enforce**,
in those words on the row. A control displayed as active and doing nothing is the defect `FRD-125`
exists to prevent; saying so is the minimum honest interim.

**An existing architecture assertion caught the new endpoint, and was right for the wrong reason.**
`FRD-602` left a test asserting that `visible_scope` is resolved exactly once _in the reporting
module_ — meaning "the CSV path did not grow its own". The anomaly list is a second, legitimate
endpoint scoped by the very same function, so the count went to two and the test went red. It now
says what it meant: **each endpoint** in that module resolves the scope exactly once — which is the
stronger property, because it also catches an endpoint that resolves it **zero** times.

30 engine tests, 3 more in Management, migration `0016`, seven mutations (`N7`–`N13`). `N12` came
back **STALE** rather than surviving — `ruff format` had reflowed the line it pointed at — and was
re-anchored; a mutation whose anchor moved protects nothing.

---

## 2026-08-07 — Phase 5 begins: what an installation considers abnormal

`ADR-0014` + `FRD-500`, stage A. The gateway has recorded everything since `FRD-122` and nobody was
watching. Phase 5 carries three of the owner's central features (PRD §1.1) — anomaly detection,
incident response, and blocking dangerous requests beyond the injection filter — and they are the
_evidence_ half of the product. The governance half is built.

The design decision came first, because the two halves pull opposite ways. Detection worth having
looks **across requests**: a caller whose refusal rate jumped, a use case whose spend tripled
overnight, a credential suddenly used from a new address. Response worth having happens **before**
the damage. §3 forbids putting analysis on the request path, and an engine that can only describe
what already went out is a report rather than a control.

`ADR-0014` settles it: **detection is asynchronous, enforcement is not, and they meet at a written
decision.** Evaluation is fed by the request log — the same rows, so a detector cannot see anything
the report cannot, and "the alert says X but the report says Y" is not a reachable state. It also
means detection sees **refusals**, which is where much of the signal is: a thousand rate-limited
requests _is_ the anomaly, and a detector fed only served traffic would be blind to exactly the
caller worth noticing. Actions are written decisions with an **author**, an **expiry** and a
**record** — an automatic block with none of those is an outage with a good reason.

This stage is the rule itself: what to watch, over what window, above what threshold, and what to
do then. Seven kinds, a **closed** vocabulary on the same argument as `FRD-114`'s capability flags
— the tempting alternative is a rule engine (field, operator, value), and it fails on the first
review: `p95_latency > 900` reads perfectly and is unimplementable against a store with no
percentile function, which `FRD-601` already ran into and said so.

Three decisions worth keeping:

- **`alert` is the default, and that is a safety property.** A detection system whose first setting
  is `block` blocks the wrong thing once and is then switched off forever. A rule is a hypothesis
  about what abnormal looks like until somebody has watched it be right. Deliberately the opposite
  default from `FRD-125`'s classifier, for a reason that generalises: _that_ control had already
  been chosen, configured and displayed as active, so failing open made it a badge without a
  control.
- **A ratio is not a threshold.** `spend_spike` compares against the preceding window rather than a
  fixed number, because a fixed number is a budget and there is already one. What it catches is a
  change of _shape_ — €4/day for a month then €40 today is worth a look under a €100 cap, and no cap
  expresses that without being lowered until it refuses normal traffic.
- **A global rule is IT Security's to author.** Its effects land on use cases its author may not be
  able to see, so the _API_ says so rather than the UI (`FRD-206`'s rule, applied to a second
  surface). A global rule is nonetheless **visible to everybody** — a rule that can block your
  traffic is a rule you are entitled to know about, whoever wrote it.

Two shapes that cost nothing now and would have cost a debugging session later: `use_case` is
**NULL** for a global rule rather than an empty string, because "" is a use case named "" that
matches nothing while looking like it matches everything — and a consumer event that carries no
`use_case` key at all is **skipped rather than treated as global**, since widening the reach of a
rule that can block traffic is the wrong way to be forgiving about a malformed event.

18 Management tests, 5 gateway consumer tests, six mutations (`N1`–`N6`, all caught), migration
`0015`. Next: `FRD-501`, the engine that reads them.

---

## 2026-08-07 — the console stops promising what the server refuses

`FRD-206`. A walkthrough of the running console, role by role, produced fourteen findings. Most were
cosmetic. Three were not, and they shared one shape: **the console was answering questions only the
server can answer, and answering them generously.**

A use-case _user_ was shown "Add member" and "Remove" on every row; using either produced a `403`
from the screen that had just invited the click. IT Security signed in to an empty console. Anyone
who could open the pipeline builder could rearrange a graph they could never save.

The cause is structural rather than a slip. Object-level permission lives in `django-guardian` rows,
so it is **not in the token**, so `/api/v1/me` cannot carry it — the console had no way to know and
filled the gap with an assumption. The fix is that the object says what this caller may do
(`can_admin` / `can_manage` / `is_member`), computed by `apps/usecases/access.py` — **the same three
predicates the viewset enforces with**, extracted from private methods so both sides read one
definition. Restating the rules in TypeScript would have been the same defect with an extra copy to
forget.

The test that matters is not "the reader sees no button" but an **agreement test**: for each of the
three answers, the corresponding request is attempted and its status must match what the object
reported. Two mutations (`Z23`, `Z24`) hardcode a reported permission to `true` and are caught by
it. `G1` and `G3` were re-anchored, because this change moved the code they pointed at and a
mutation whose anchor has moved protects nothing.

Three smaller decisions came out of it and generalise:

- **An action nobody can carry out is worse than an absent one.** An absent action reads as a
  boundary; a present one that fails reads as a broken system — and the reader's next move is to
  distrust the figures on the same page. So every withheld action is replaced by one sentence
  naming who performs it, and read-only stays _usable_: members, budgets, limits and the pipeline
  are all still visible, and the dry-run panel still runs, because none of that changes anything.
- **Read-only means inert, not un-saveable.** The builder's graph sits in a native
  `<fieldset disabled>`, so the add/remove buttons inside it cannot be used either. Hiding Save
  alone would let somebody rearrange a pipeline for nothing — the same defect one step later.
- **`is_member` and `can_manage` are separate answers, and so is visibility.** An oversight role
  sees every use case and belongs to none of them (`ADR-0007`), so it must not be offered a key; a
  member belongs to one without administering it, so it must be.

`IT Security` was the other half of the same mistake: `scope_queryset` used one role set for both
"sees every use case" and "sees every figure". PRD §154 gives that role the first and not the
second, and folded together it saw nothing at all. Split into `OVERSIGHT_ROLES` ⊃
`GOVERNANCE_ROLES` — oversight decides visibility, governance decides spend.

And a message that was true and still wrong: the budgets tab told a reader "the gateway does not
count you as a member of this use case" while they were looking at their own name in the Members
tab. Both statements were correct — the gateway takes membership from the Keycloak group
`/use-cases/<slug>`, Management from its own table. It now says exactly that, because the remedy is
a group and not a table.

The rest of the walkthrough, fixed in the same pass: the session now renews itself (`offline_access`

- silent refresh — an expired token was reporting "invalid credentials" on every screen, which reads
  as the data being untrustworthy rather than the session having ended); creating a use case is a
  button and a window that ends on the new use case's **settings**, since one with no members, no
  budget and no limits is not finished and the list is what makes it look finished; "slug" became
  **technical id**, filled in from the name and described by what makes it matter (it is permanent and
  appears in every API key; the name is not); the model editor became a window that names the model it
  is editing; the reporting cards got short headings plus an info button holding the sentence that
  says what each figure counts — "Refused by a control" was breaking the card row, and the answer to a
  heading that does not fit is not a smaller font; and the export row and the catalog's Edit/Remove
  pair got the spacing they never had, the latter because two buttons touching invite the wrong one
  and one of them is destructive.

Also documented rather than left in the DEVLOG alone: `FRD-130` (the demo showcase), which the
previous entry referenced without a document existing.

**A session that has ended is a login, not an error.** Reported after the rest of this pass: a
token going invalid — the tab left open, or Keycloak restarted — produced "invalid credentials" on
every panel at once. True, and the wrong thing to say: it reads as the backend rejecting the
person, and the next thing doubted is the figures on the same page. A `401` on `/api` or `/gw` now
drops the dead token and starts the login, which is the only action available anyway. `403` is
deliberately left alone — that is a real answer about a real permission, and logging somebody out
over it would hide the boundary behind a login screen. One login is started however many requests
fail together, and the path is carried through `state` and restored, but **only if it is a
same-origin path**: `state` survives a round trip through the browser, so treating it as a
destination would be an open redirect with extra steps. Two endings, both checked in a browser
against a real token that was then broken: with the Keycloak session alive the round trip is
invisible; with it gone — the reported case, reproduced by ending the session through the admin
API — the login form appears.

**Two defects I shipped and had to be told about.** The smaller one first: the reporting screen's
new info buttons showed nothing. They carried a `title` attribute — a native tooltip needs a long
hover, never appears on a touch screen, and is invisible to a keyboard — so a control sat there
looking clickable and did nothing when used. That is the exact defect this pass was written to fix,
committed inside the fix for it. The first repair opened it on click, which worked and was still
the wrong answer — an "i" is a thing you point at, and the report had said so. It now shows on
**hover**, on **focus** for a keyboard, and stays pinned on a click for a touch screen, which has
no hover to offer; the panel is positioned rather than in flow, so the card does not grow under the
pointer. An e2e case exercises it as a real hover, because only a browser can tell "renders a
tooltip attribute" from "shows the reader anything" — which is how the `title` version passed
review at all.

**The larger one: the console would not load at all.** The
session-renewal fix (above) added `offline_access` to the requested scopes to get a refresh token.
This realm does not permit offline tokens, so the code-to-token exchange came back
`not_allowed` — and Keycloak answers _that_ failure without CORS headers, so the browser reported
a CORS error naming neither the scope nor the realm setting. The page went blank after a
successful login, which looks like a crash and is nothing of the kind.

Two things worth keeping from it. `offline_access` was the **wrong instrument** even where it
works: the authorization-code flow already returns a refresh token, and `offline_access` asks for
one that outlives the SSO session — a credential a governance console has no business holding. And
the reason it reached the running stack is that **I ran three of the four test layers and skipped
the fourth**, on a change that lives only in the fourth: no unit test can perform an OIDC
redirect, and `e2e/tests/auth.spec.ts` — which does — would have failed on the first run. The
config is now pinned by a unit test that says _why_, but the layer rule is the real lesson: a
change to the login flow is an e2e change, whatever else it touches.

That run also turned up sixteen e2e failures — every one of them a test driving a screen this
pass deliberately changed, which is what an e2e suite is _supposed_ to do when the UI moves. The
creation form became a button and a window, so the shared `createUseCase` helper drives that
instead; "the inputs are cleared after a successful POST" became "the window is gone and the page
moved on", which is the same zoneless property observed where it now lives. Two changed meaning
rather than mechanics and were rewritten rather than repaired: the governance role no longer
_clicks_ Issue key and reads the refusal, because the console does not offer it any more; and the
three disabled navigation tabs are gone, so the property they encoded (the console follows the
roles in the token) moved to a chip per role in the header, carrying `data-role` so it stays
assertable without depending on the wording. Three more were the same story a level down: the
model editor's Save moved into a window footer and reaches its form by `form=`, so the tests
address it that way — which is also what proves the association still works; and the
consumption-hidden message changed wording deliberately, so the assertion follows the new
requirement (name the Keycloak group _and_ say it is not the member list on the same page) rather
than the old sentence.

Two demo-seed defects fell out of asking the _running_ stack who could manage what, rather than
reading the declaration: `itgov` was still administering `personalwesen` and `itsec` still belonged
to `kundenservice`, both from declarations long since changed. **A membership left behind is not a
stale row, it is live permission on a use case** — the seed now reconciles to what it declares and
revokes what it removes. And `personalwesen` no longer belongs to an oversight role at all: it was
there so the demo could show a use case `ucadmin` cannot touch, at the cost of teaching the opposite
of what IT Steuerung _is_.

**Found while running the gates: `make ci` was already red**, and not because of anything in this
change. `ruff` is declared as `>=0.9` and `uv.lock` had moved to 0.16.1, whose formatter targets
`py314` and applies **PEP 758** — `except A, B:` without parentheses. Eight committed files were
therefore unformatted against the very tool the gate runs. Reformatted, and `Z19`'s anchor moved
with the line it points at. Worth knowing for next time: a lock refresh can redefine a _format_
gate across the whole tree without a single source line being edited, and nothing announces it
except the gate itself.

---

## 2026-08-07 — a demo somebody can walk through

`FRD-130`. `seed_demo` created five roles and one user each, which lets you log in as every role and
look at five empty screens. This gives each of them something to see, and picks the content so the
differences between the roles are **visible rather than described**.

Three use cases, each making one governance decision concrete: `kundenservice` stores prompts with
the shortest retention that still supports an incident review and runs the heuristic injection
filter; `entwicklung` is higher-volume and carries rate limits instead of a tight budget;
`personalwesen` has **storage switched off** — the figures are still collected, the prompts are not.
`ucadmin` deliberately administers only two of the three, because switching to that account and
finding two instead of three is the fastest way to show the scoping is real rather than a filter in
the frontend.

Budgets across every axis the UI offers — cost, tokens, requests; use-case and member scope; day and
month — and `tools/demo_traffic.py` drives **real** requests through the gateway against the local
model, including one prompt-injection attempt that the filter refuses. Inserted rows would have been
consistent; they would also have been a story about the product rather than the product.

Ollama joins the `demo` profile with a separate pull step, so the server's health check stays honest
— a container reported "healthy" only after a multi-hundred-megabyte download makes every restart
look like a hang.

### Four things the first run got wrong

**The seed declared no models.** `local_models` gated on `AIRA_OLLAMA_URL` while this stack is
configured with `AIRA_OPENAI_SERVERS` — the named-server form `FRD-123` moved to when a self-hosted
fleet turned out to be several machines. The catalog came up empty and the use cases pointed at
nothing. Either form counts now.

**801 use cases.** A demo database accumulates the fixtures of every test run that ever pointed at
it, and a global administrator opening a list of `burst-3i6g5l` and `dryrun-xkroyc` learns only that
the list is long. `--fresh` now means _every_ use case, not just the ones the seed made.

**And then `--fresh` killed the keys for ever.** Deleting a use case revokes its API keys, and
revocation is **terminal** in the read model on purpose — `api_key.created` must never resurrect
one. Announcing a delete for a slug the same run then recreates therefore permanently revoked the
deterministic demo keys: three use cases, three keys, 401 for ever. Recreating the same slug is a
**reset, not a retirement**, and the events have to say so. The rule is right; the seed was wrong
about which event it was sending.

**The budgets showed nothing.** A plausible-looking €0.50 monthly cap against a local model priced
in fractions of a cent per million tokens sits at 0.02% after a walkthrough — technically correct
and useless. They are calibrated against what the demo traffic actually costs: after one run the
bars sit between a third and two thirds, and two more runs reach a limit.

`make showcase` starts the lot and prints who to log in as.

---

## 2026-08-07 — a developer round against the running model

`FRD-129`. After two structural changes to the request path and three defect fixes, a walk through
the system the way somebody using it would: both surfaces, ordinary journeys, dropped connections
on every path, and every figure checked **in the database** rather than in the response body.

**47 live cases**, and the shape of them is the point. Nothing asserts on the _content_ of an
answer — `qwen3:0.6b` is a real model and a poor one, and asserting its accuracy would be testing
somebody else's work and flaking. What is asserted is what the gateway promises: that a request is
recorded, weighed, priced and bounded, and that the **two surfaces leave the same facts behind** for
the same work. That last one is checked by comparing audit rows rather than by reading two code
paths, because a step skipped on one surface is invisible in its own tests.

Everything the last days built held: the tokens in the row equal the tokens in the response, the
budget counter equals the sum of the rows, a batch of five is counted as five, an exhausted budget
refuses without paying for a classifier, a dropped connection leaves a row on every path, and both
surfaces are rate-limited.

### Two findings

**A declaration nobody had measured.** The catalog said this model offers the `minimal` thinking
mode. The server accepts `none`, `low`, `medium`, `high`, `max` — and refuses `minimal` **by name**.
The seed file had deliberately declared no thinking at all, with a comment saying to add it "when
the integration run says so"; a hand-written entry had filled it in from the enum instead. The run
has now said so, and the seed carries the measured set. Declaring from the vocabulary rather than
from a measurement is the mirror image of the mistake `FRD-114` was written to prevent.

**And the error it produced was worse than the error itself.** The caller received
`502 UNAVAILABLE`, "Upstream returned 400." The provider had said, precisely, _invalid reasoning
value: 'minimal'_ — and that was discarded. An operator reading `UNAVAILABLE` checks a status page;
the fault was in their own catalog.

An upstream **400** now answers `400 FAILED_PRECONDITION` and carries the provider's reason. Same
argument as `NoCapableModel`: "the provider refused the body we built" is operator-fixable, an
outage is not. The test that encoded the old rule had a comment giving the reason to change it —
_"a 400 from the upstream reflects our config"_ — and it does, which is exactly why calling it an
outage misleads.

**Only 400**, though. The old test was right about the other half: a 401 or 403 is about _our_
credentials, the caller cannot act on it, and the provider's message may name the credential. Those
stay masked, and a test now pins that too.

Mutations `Z21`/`Z22`. **231 properties.**

---

## 2026-08-07 — the third surface was a thought experiment, and it is withdrawn

`FRD-106` — an OpenAI-compatible surface exposed to callers — is **not wanted**. It was raised to
push a question about generalisation, and it did its job: _if a third surface were added, would it
write those six steps a third time?_ Yes. That answer produced `FRD-126` and `FRD-128`.

Worth separating two things that share a name. The OpenAI **wire dialect** stays and is untouched:
Azure Foundry and the self-deployed fleet speak it, as an _upstream_ (`ADR-0011`). What is
withdrawn is an OpenAI-shaped **API surface** pointed at callers. Only one of those was ever
deferred rather than declined, and the ROADMAP now says which.

The consolidation does not need the surface to justify itself, and the docs now say so rather than
leaving a reader to infer that two structural changes were speculative work for a cancelled
feature. What they actually fixed was already in the code:

- four of six paths lost the audit row when a caller hung up mid-answer,
- the KIRA surface had no rate limiting at all after one control moved one function over,
- the KIRA streaming path never received the disconnect fix the Gemini one earned.

None of that needed a third surface to be real. The hypothetical was the lens, not the reason.

---

## 2026-08-07 — a request the caller abandoned is still a request that happened

`FRD-128`, the second of the three steps, and it started with a question rather than a failure:
_have all the paths been tested with a dropped connection?_

No. Streaming had been — Gemini's by closing the iterator and by a live client walking away,
KIRA's the day before (`FRD-127`). **Every non-streaming path had not, and all four lost the audit
row.** A caller who went away while the model was still answering made a request that reached the
upstream, spent tokens and spent money vanish from the record.

Six paths, each with its own copy of `hold → dispatch → check → price → settle → record`, and the
guarantee is the _order_. Two of the six were right. `accounting()` owns it now, shielded, and the
surfaces went from **twelve** direct calls to **zero**.

A caller who abandons a request is recorded with status **499** and outcome `client_gone` — nobody
is sent that status, it exists so the audit can tell that case from a served one, and it is its own
outcome because "clients keep hanging up" is a different thing to investigate from "the provider
keeps failing".

### Three things that cost a draft each

**The accounting has to run inside `hold`, not around it.** Outside, `hold` sees an unresolved
reservation on the way out and gives it back — then the settle books it again. One request, settled
once and released once.

**`hold` owns the release.** An explicit release in the exit counted the give-back twice.

**An embedding produces vectors and reports no tokens**, which is not the same as producing
nothing. Conflating them would release a whole batch's reservation and leave batched traffic
invisible to a request limit.

### Two tests that were asserting the wrong thing

Both were coupled to _where_ something happens rather than to _whether_ it happens, and both went
quiet instead of failing when it moved. One monkeypatched `routes.record_request` and stopped
intercepting the moment the write moved into the shared sequence. The other counted calls to
`release` through a delegating stand-in — which `hold`'s internal `self.release(...)` never passes
through, so it was testing the wrapper. Both now read the row and the counter.

### And two of my own mistakes worth recording

A string replacement removed `'    reservation = ...'` as a **substring** of the eight-space
version, leaving four stray spaces that silently re-indented the next line. Then a heuristic
"repair" pass made it worse by de-indenting an `except`. The lesson is not subtle: line-based edits
need line-based matching, and a repair driven by a guess about what broke is a second break. Both
were caught by the syntax check within a minute, which is the only reason this paragraph is about
drafts.

Mutations `Z17`–`Z20`, with `Z17`/`Z18` re-anchored onto the shared sequence — the hand-written
finisher they pointed at is gone. The shield still has **no** mutation, and the reason is
`FRD-110`'s, re-verified by `FRD-127`: no hermetic test can tell a generator close from a socket
drop, so a harness claiming to guard it would claim a proof nobody has.

---

## 2026-08-07 — the fix the second surface never got

`FRD-127`, and the first of the three steps that came out of assessing what a third API surface
would cost.

The Gemini streaming path wraps its accounting in `finally` + `asyncio.shield`, with a long comment
explaining that a caller dropping a real socket **cancels the response task**, so a bare `await`
there loses the settle and the audit row. It was found as a 1-in-8 integration flake. **The KIRA
streaming path had neither** — no `finally` at all.

That is what duplication does, and where it leaves it: the surface written second did not receive
the fix the first one earned.

The window is different here, and the difference is why copying the Gemini test would have proved
nothing. This surface's "stream" delivers **one terminal event carrying the whole answer**, so the
accounting happens _before_ anything is yielded — hanging up after the first chunk finds the work
already done. What is exposed is the long await in the middle: a caller who goes away while the
model is still thinking. The upstream was called; the request then vanished from the record.

Now one shielded exit accounts for every way out — served, refused, or cancelled — and a stream
that produced nothing chargeable is **released** rather than settled, because booking a request
against somebody who received nothing would spend a request limit on a caller who hung up. The
status for that row is `499`: nobody is sent it, because there is nobody to send it to, and it
exists so the audit can tell that case from a served one.

### The test that had to be corrected before it could be trusted

Written first, it reproduced the defect exactly — model reached, no row. Then it turned out to pass
**with and without the shield**. It proves the `finally` exists, which was the real gap here, and it
cannot prove the shield matters: in-process cancellation and a dropped socket are not the same
event, which is precisely what `FRD-110` recorded when it declined to add a mutation for Gemini's
shield.

So there is no mutation for this one either, and the reason is written next to it. A harness that
claimed to guard the shield would be claiming a proof nobody has — worse than a harness with a gap
that says so. `Z17` guards the row, `Z18` guards the release, and the shield is the integration
layer's to check.

An earlier draft of `Z17` added a `_no_shield` passthrough to the production module for the harness
to swap in. Production code shaped by its own test harness is the wrong direction; the mutation is
a one-line edit now.

---

## 2026-08-07 — a surface parses; the layer decides

`FRD-126`. Prompted by a question rather than a failure: _why are there two pipelines with six
steps each — would emulating an OpenAI interface spawn another six?_ It would have.

There were never two pipelines. There is one, and there were **two hand-written choreographies
around it**. `api/serving.py` was extracted precisely so both surfaces could share everything below
the wire format, and its docstring says a surface owns "parsing its own wire format, rendering its
own error envelope, and its own routes". It shared the _steps_. Nobody noticed it had not shared
the _order_:

    Gemini                          KIRA
    check_not_empty                 check_not_empty        ⎫
    guard_before_work               guard_before_work      ⎪ _prepare()
    run_pipeline                    run_pipeline           ⎪
    check_declaration               check_declaration      ⎪
    resolve_thinking                resolve_thinking       ⎭
    enforce_pre_dispatch            enforce_pre_dispatch   ← written out in three handlers

That distinction is the whole story of the last two days. **Every guarantee this layer makes is a
guarantee about the order** — rate limit before the pipeline or a refusal is paid for; declaration
and thinking after routing or they are checked against a model that never serves the request;
reservation last or it is made against the model the caller _named_. None of that can be expressed
by a function that knows only its own step, which is why the same gap kept coming back wearing
different names: `:embedContent` bypassing the gate, then the KIRA surface losing rate limiting
entirely when one take moved one function over.

`prepare_for_dispatch` owns the order. The KIRA surface went from six of these calls to **zero**.
And the rule is now a test — `test_surface_layering.py` parses each surface and fails on a direct
call to any step, the same shape as the vendor assertion in `test_vertex.py`, for the same reason: a
layering rule only a reviewer enforces is a rule the _next_ surface breaks, and the next surface is
the one nobody is watching yet.

The evidence that this was a move and not a rewrite is that **no test changed**: 887 hermetic and
316 live, green before and after.

**Four mutations came back `STALE`** — not "survived": the harness distinguishes "this property is
undefended" from "this mutation no longer applies", and all four pointed at lines this change moved
into the shared sequence. Re-anchored there. A fifth (`Z13`) was **removed**: it claimed "the
compatibility surface takes the same early gate", which was a distinct property only _because_ each
surface took the gate for itself. Now its anchor and `Z11`'s are the same line, and two mutations
on one line measure one thing twice. What it really claimed is enforced structurally by
`test_surface_layering.py` — the same call `X3` got, for the same reason.

### And the honest limit of this change

Asked what a third surface would now cost, the answer turned out to be _half of it_. The
pre-dispatch order is shared; the **post-dispatch** order — hold, dispatch, check, price, settle,
record — is still written out **six times**, three verbs in each surface. Same shape, one step
later.

It has already cost a defect. Gemini's streaming path wraps its accounting in `finally` +
`asyncio.shield`, with a comment explaining that a client dropping a real socket _cancels_ the
response task and a bare `await` loses the settle and the row — found as a 1-in-8 integration flake.
**The KIRA streaming path has neither.** The surface written second never got the fix the first one
earned, which is precisely what duplication does and precisely where it leaves it.

---

## 2026-08-06 — the filter that was configured, displayed, and doing nothing

`FRD-125`. Third finding of the same live round, and the worst of them.

A use case configured the LLM prompt-injection filter to **block**. An injection was sent. The
gateway answered **200**, and the model complied with it — it printed a system prompt.

The cause is one line, and it is the same line as the day's other findings wearing a third face.
The classifier asks the model for a one-word answer inside a four-token allowance, and it dispatches
**straight to the provider**, bypassing the catalog-based thinking resolution the serving path
performs. So it never says "do not think". A reasoning model thinks by default. All four tokens went
on reasoning, the answer came back empty, and the verdict was a `bool`:

    "INJECTION" in ""   →   False   →   clean

The same bug had silently disabled `model_route`, which returned "no category matched" for every
request — indistinguishable from a router whose categories genuinely never fit.

**A verdict now has three values.** `undetermined` covers an upstream failure, an empty reply, a
reply containing neither word, and a reply containing _both_ — "SAFE, no injection attempt here"
was asked for one word and gave two, and picking a winner would be a precedence rule nobody could
predict from outside (the argument `FRD-111` already makes about two `thinkingConfig` spellings).

**And it blocks by default**, which reverses the old "fails open". That reversal deserves its
sentence: the old behaviour was defended as "a classifier outage must not take down legitimate
traffic", which is a real concern and the wrong answer — `FRD-405` settled the identical question
for rate limits with _the moment a control stops working is the worst moment to stop applying it_. A
filter that passes everything while the builder shows it as active is not a degraded control, it is
an absent one wearing the badge of a present one. `on_undetermined: allow` restores the old
behaviour for anyone who wants it, as a choice, on the audit row.

Two smaller things fell out. A filter that ran and **passed** now records that it did — "the filter
found nothing" and "no filter was configured" used to look identical afterwards and call for
opposite conclusions. And mutations `P1`/`P2` were **re-anchored**: they pointed at a line this
change moved, and a mutation whose anchor has moved protects nothing.

### An operational finding that is not a defect

Against `qwen3:0.6b` the LLM filter answers `INJECTION` to everything, including "What is 2 + 2?".
The gateway is correct; the model is not a usable security classifier at that size. Worth saying
because the builder makes the LLM mode look like the stronger option: **it is exactly as good as
the model behind it**, and the heuristic — which cannot be undetermined, because a regex either
matches or does not — has no such failure mode.

### And a test lesson

Two live assertions had to be rewritten because they were testing the _model_, not the gateway. A
seed reproducibility check that failed one time in three (this server's first generation after a
cold context differs — its prompt cache, not our seed), and a router check asserting that a 0.6B
model picks the right category. Both replaced by the property that is actually ours: the classifier
gets an answer at all. The second one asserts the **old** call shape still returns nothing, so if
that ever stops being true the test says so rather than passing for a new reason.

### The other half: it was not being paid for either

Counting model calls rather than reading code again. One caller request with an LLM step makes
**two** model calls and left **one** audit row. The classifier's tokens were invisible three ways at
once: `FRD-601` reported a spend they were not part of, `FRD-403`'s _"unpriced traffic is counted
apart, never as zero"_ was broken by counting them as **nothing at all** — the one thing that rule
exists to forbid — and `ADR-0013`'s auditable model access had a model call in it that nothing
recorded.

Each pipeline call now leaves its own row, named `pipeline:<step>` so reporting can separate what a
use case _asked_ from what _governing it_ cost, and is booked against the budget with
**`requests=0`**: the caller made one request, and counting the classifier as a second would inflate
every request figure and could trip a request limit for traffic nobody sent.

The hook lives in `run_pipeline`, in a `finally`, and the collector is **passed in** exactly as
`decisions` already is — so a step that _blocked_ still reports what deciding to block cost, and
both surfaces get it because both call that function. A hook per surface boundary is the shape that
let `:embedContent` slip past the pre-dispatch gate.

The number is the part worth keeping: against the real model, **the classifier's call costs roughly
as much as the answer it guards**. A use case running an LLM filter was reporting a little over half
its actual spend.

### The refusal that was billed for

Follow-up question from the owner — _do the filter costs count against the budget?_ — and then a
measurement of what "over budget" actually did. The pipeline ran **before** the budget guard, so a
use case one request past its limit kept running its LLM injection filter on every subsequent
request: all refused with a 429, all billed for the classifier. A 20 000 cost limit, one served
request, seven refused, **72 400 spent and still climbing**. A client with a retry loop spends
without bound. That is a denial-of-wallet wearing a budget's name.

`guard_before_work` runs the two controls that need no model — the rate limit, and _is this use case
already over_ — before the pipeline. The reservation stays where it was, because it is made against
the model routing chooses. Same probe now: spend stops at **25 600** and does not move across six
further refusals.

The owner's decision, asked and recorded: a bounded overshoot is an acceptable price for the
security step running. What was never acceptable was the unbounded one.

Two drafts died on old lessons. The gate belongs **before the verb branch**, not inside
`run_pipeline` — embeddings have no pipeline, so the tidier placement would have left
`:embedContent` unlimited, the same verb and the same way as `FRD-405` B3. And `units` has to be
computed before the gate: the first draft took one unit early and _commented_ that the batch weight
was taken again later. It was not. A batch of 500 metered as one request, by a comment asserting a
rule the code did not have — caught by a test that already existed, which is the only reason this
paragraph is about a draft rather than about production.

Also: four budget stand-ins in the test suite had to inherit the new method rather than stub it.
A stand-in more permissive than the thing it replaces is how a control comes to be tested against
something that cannot refuse — `CLAUDE.md` §3 names it, and adding a method to the real service is
exactly when it bites.

### Recording it is not enforcing it

Asked afterwards: _do the filter costs actually count against the budget?_ They were being written
to Postgres — the system of record — so reporting was right. `FRD-405`'s guard reads the **shared
counter**, and a Postgres-only write reaches it only when the counter expires and rebuilds, up to
`COUNTER_TTL_SECONDS` later. A small cost cap and four requests: the counter read 41 000 against a
limit of 40 000 and the next request was served.

Both stores now. The live re-run refuses the third request at 40 200, naming the cost budget.

The test written for this **passed against the broken code** on the first attempt, and the reason is
worth more than the fix: on a _cold_ counter the guard seeds from Postgres, so a Postgres-only write
is visible anyway. The test never reached the path it was named after — the exact trap `CLAUDE.md`
§3 already lists — and it now warms the counter before it measures anything.

### And a stale number, which is the same defect in prose

`CLAUDE.md` claimed the harness guarded **124** properties. It guarded 220. Every update to that
figure across this release was a string replacement whose anchor did not match — so each one
changed nothing, reported success, and moved on. Six no-ops in a row, none of them checked.

That is precisely the failure this release has been about, arriving in the documentation instead of
in a request: an operation accepted, apparently successful, and not performed. It gets the same
answer. `tools/tests/test_documented_counts.py` compares the stated figure against the harness and
fails when they disagree, and `tools/tests` is now in the default `testpaths` — a check nobody runs
is a check nobody has.

### Two survivors, and what each of them was

The harness reported two properties undefended on the first full run, and both were my own doing.

`Z8` — _a pipeline call is booked against the budget_ — survived because every accounting test
asserted the **audit row**, and the app under test had no budget configured. Booking zero tokens
changed nothing anybody was looking at. The fix is a test that configures a budget and counts;
without it, an unbudgeted classifier is not a rounding error, because measured against a real model
it costs about as much as the answer it guards, so a use case at its limit would keep spending past
it.

`Z2` — _an upstream failure is undetermined, never clean_ — survived because **its anchor had
moved**: part (b) lifted that `return` out of `verdict` and into `classify_text`, and a mutation
whose anchor no longer matches cannot break the property it names. This project already knew that
rule; what is new is that the harness now demonstrates it rather than asserting it, because it
reported the property as _undefended_ instead of quietly passing. Re-anchored.

Chasing `Z2` also turned up a second copy of the router's logic — `classify` had been left
re-implementing what `classify_text` does, and its `except UpstreamError` branch was already the one
no test reached. Two copies of one rule, about an hour old. It now delegates.

Mutations `Z1`–`Z10`. **220 properties defended.**

---

## 2026-08-06 — the refusal that ran before the boundary

Same live round, second finding. `FRD-122` §12.

`FRD-122`'s rule is that the audit log records what was **asked**, not only what was served, and it
was closed at the route's exception boundary — one site, deliberately. One refusal never reaches
that boundary: the request-body ceiling is pure ASGI middleware and answers **before any route
runs**. A 20 MB body was refused with a 413 and left no trace at all.

Found by posting one and counting rows. Not by reading the code, which is entirely consistent about
this rule everywhere the code can be read — the gap is in a place the rule was never applied.

The fix is small and its shape is the point: both exits from the decision (a declared
`Content-Length` over the ceiling, and a body that declared none and was cut off mid-read) record
through **one** function. A new closed-vocabulary outcome, `request_too_large`, rather than folding
it into `invalid_request`: "somebody keeps posting 20 MB" and "somebody sent malformed JSON" are
different operational facts and a shared bucket hides the first inside the second.

**The row carries no identity.** The credential in the header has not been verified at that point,
and recording it would let anybody write another system's name into the audit trail by sending one
oversized request. An unverifiable claim is not evidence — the same rule as "unpriced is not free"
and "undeclared is not permitted", pointed at identity. The body is not stored either: it is over
the ceiling, and keeping what we refused to read would undo the reason for refusing it.

And what stays unrecorded, said out loud rather than left to be discovered: **a 401 leaves no row**.
That is a decision. A request that never presented a valid credential is a security event and
belongs with anomaly detection and incident response (`FRD-500`/`501`/`503`), not in a usage log
where it would surface in spend reports as a refusal attributed to nobody. Written into `FRD-122`
so whoever builds those finds the question already asked.

Mutations `Y9`–`Y11`. **210 properties defended.**

---

## 2026-08-06 — twelve fields, eleven silences

A local model made this findable. `FRD-124`.

Twelve fields a legitimate Google client can send were posted at the running gateway. **Eleven came
back 200 and did nothing.** `stopSequences` — unbounded output. `seed` — a different answer every
call, which is the exact failure a seed exists to rule out, presented as the model being creative.
`tools` — prose where a function call was expected. `safetySettings` — a governance control applied
nowhere. `candidateCount: 3` — one candidate, and one answer where three were asked for does not
look like a partial failure, it looks like the model had one thing to say.

The project has a rule for this and has had it since `ADR-0012`: **a chain must not be able to
degrade a request silently.** A model that cannot read the PDF is skipped, never sent the prompt
without it, because a dropped attachment produces a fluent wrong answer with a 200 and the caller
blames the model. That rule was pointed at the _model_. It was never pointed at the _surface_ — and
a field the surface drops is the same defect one step earlier.

### The one that started it

`thinkingConfig: {mode: "disabled"}`. The dialect mapped `disabled` to an **absent**
`reasoning_effort`, with a comment saying "there is no 'off' value; the absence of the parameter is
off, as with Anthropic." Measured against a real reasoning model: sent no `reasoning_effort` it
thinks anyway — absence selects the _model's_ default, not off — and it spent the whole 600-token
allowance doing it. Empty answer, `MAX_TOKENS`, 200. The reasoning is stripped from the response by
design, so the caller sees a model that failed to answer, not a setting that was ignored. The same
server sent `"none"` answers in twelve tokens.

There was a unit test asserting `"reasoning_effort" not in body`. It was green because the code and
the test came from the same wrong idea. **Off has to be said out loud.**

### What was built

Three answers instead of two:

    portable and supported     → carried to the dialect       topP, seed, stopSequences, …
    known but out of scope     → refused, saying why          tools, safetySettings, cachedContent
    the dialect cannot say it  → the candidate is skipped     top_k on OpenAI, seed on Anthropic

The third reuses the requirement mechanism that already carries region, media types, schemas and
thinking. `SamplingExpressible` is the fifth to share it — and the first that is a property of the
**dialect** rather than the model, because no catalog entry can say whether `top_k` exists. That
depends on the wire format the request will travel over, and no dialect has all six:

    Gemini      top_p  top_k  seed  presence  frequency  stop
    OpenAI      top_p    —    seed  presence  frequency  stop
    Anthropic   top_p  top_k    —       —         —      stop

Refusal rather than best effort, for the reason that decides every one of these: `seed` on a Claude
candidate produces a perfectly good answer that simply is not reproducible, and **nothing in it
differs from a correct one**.

### Reversing FR-7, on evidence

`FRD-100` FR-7 had the request models ignore unknown fields, so real Gemini clients sending extra
keys were not rejected. Both halves of that argument turn out to be wrong: Google's own API rejects
unknown fields, so leniency was never the compatible choice; and the fields clients actually send
are ones that change the answer. Strictness is one-directional — **responses keep ignoring extras**,
because a provider adding a field must never break a caller.

### Two test lessons, one of them repeated

The hermetic tests for `SamplingExpressible` exercised it directly, never through the route. A
mutation removing it from the route's requirement list left every one of them green: two correct
halves and no wire between them. **That is the second time in one day** — the CSV export's scope
test had the identical shape, built the file itself instead of downloading it, and survived the
mutation that made the endpoint ignore the caller's scope. Both are fixed by driving the real
endpoint; both were invisible to coverage, which saw every line run.

And the integration tests here assert **behaviour, not wire bodies**: a seed makes three identical
requests return one answer, a stop sequence truncates the output, thinking off produces an answer.
None of that can be established by inspecting a dict — which is exactly how the thinking defect
survived a suite that appeared to test it.

38 hermetic tests, 9 against the real model, mutations `Y1`–`Y8`. **207 properties defended.**

---

## 2026-08-06 — the usage export, and the same dependency lesson twice

`FRD-602`. CSV is a **renderer on the existing reporting endpoint**, chosen by `Accept` — never its
own endpoint, because `FRD-601`'s visibility rule is one function and a second entry point is a
second chance to forget it. That is how an export comes to return more than the screen: a
governance failure delivered as a _file_, forwarded, saved, impossible to recall. The test asserts
on the file's bytes, and a second one checks by source inspection that `visible_scope` is resolved
exactly once.

The format details are small and none of them are obscure: a BOM so Excel reads `süd` as a name,
CRLF because RFC 4180 says so, quoted keys because a use case called `vertrieb, süd` would
otherwise shift every figure on its row one column left — a spreadsheet that is _wrong_ rather than
broken. Commas rather than semicolons, and the download panel says Excel may ask about the
separator, which is the honest alternative to picking the other surprise.

### The lesson this project has now learned twice

`aira_common.secrets` imports `httpx`. Every hermetic test passed, a live Vault read worked, and
the **management migration container died on `ModuleNotFoundError`** — because `httpx` was a
_gateway_ dependency and a workspace `uv sync` installs everything into one environment.

The line directly above it in `libs/pyproject.toml` is a comment explaining that `pyjwt` was added
for exactly this reason, after exactly this failure. **A shared library's dependencies cannot be
validated by any environment that also installs its consumers** — and this repository's dev
environment, its test runner and its coverage gate are all such an environment.

So `libs/tests/test_declared_dependencies.py` now parses every module in `aira_common` and fails on
any third-party import the package does not declare. Shown to fail with the declaration removed. It
costs milliseconds and replaces a failure that costs a deploy.

---

## 2026-08-06 — diagnostics, and a probe that would have proved nothing

`FRD-117` FR-1 to FR-6. The design centre is one sentence from §5.2: **a health check must not be
able to take down a healthy service.** The predecessor's `/health` probes every registered model on
every call, which makes readiness as slow as the slowest upstream — so one degraded provider evicts
pods that were serving perfectly well, and against a paid endpoint it bills for the privilege.

So the probe runs in the background and `/readyz` reads the last verdict. A live test asserts ten
readiness probes finish in under five seconds, and another asks the model server directly whether
probing loaded anything — because "the probe never generates" is exactly the kind of claim that
decays into a convenient call somebody added later.

**The first draft would have proved nothing.** It probed by calling `provider.models()`, which is
_local configuration_ evaluated once when the registry is built: it cannot fail later and says
nothing about the network. Every verdict would have been a confident green describing nothing —
worse than no probe, because a green board gets acted on. It surfaced while writing a test with a
provider that raises, discovering such a provider cannot be registered at all, and following that
back. Adapters now implement an optional `ping()`, a GET of a listing; one without it is reported
`probed: false, "not checked"`, because _we did not look_ and _it is fine_ are different answers.

The case that mattered most could only be staged live: stop the model container and watch. `/readyz`
stayed **200 `ready`** with `degraded: true`, and cleared when it came back. A load balancer keeps
the instance — the signal is for an alert, not an eviction, and that distinction is the feature.

`x-trace-id` is pure ASGI, mounted outermost. `BaseHTTPMiddleware` would run the app in a separate
task and lose the span context, so the header would be absent exactly when a span exists; and
outermost because the responses that most need correlating are the ones an exception handler
produced. Confirmed on a deployed gateway: the 401 carries one.

CORS refuses `*` with credentials **at startup**. Browsers reject that combination, and a server
implementing it by reflecting the origin lets any site a user visits call the API with their
credentials. A misconfiguration that only appears under a browser is one that
ships.

**FR-7, the second OpenAPI 3.0 document, is not built** — it serves a legacy portal this deployment
does not have, and a generated document nobody reads silently stops matching the routes. Said
rather than quietly skipped.

---

## 2026-08-06 — Vault, finally reading from the thing that was already running

`CLAUDE.md` §2 has said "secrets only in HashiCorp Vault" since Phase 0, and Vault has been in the
Compose stack for as long — with **no code reading from it**. Every credential this system holds
was an environment variable, which is exactly the state the policy exists to prevent.

`aira_common.secrets` does the AppRole login and the KV-v2 read; a pydantic `VaultSource` puts it
above the environment for both planes. **A settings source rather than an injection into
`os.environ`**, and that is the security half rather than a style choice: values in the environment
are readable from `/proc`, inherited by every subprocess, and reach any library that dumps the
environment on a crash.

Fail closed is the whole design. A configured Vault that cannot be reached stops the process,
because the alternative turns a broken secret store into a _silent downgrade_ — the environment in
that scenario usually holds a stale or development value, so the service starts, looks healthy, and
is wrong. `ADR-0007` established the principle for `SECRET_KEY`; this extends it to every
credential. "Vault is down" and "nobody wrote that key" are **different exceptions**, because they
call for different actions by different people.

Tested against the Vault in the stack, with a **real AppRole** the suite creates — its own policy,
scoped to its own path, removed afterwards. That is what makes the least-privilege case rest on
Vault's decision rather than on our mock: the same credential that reads this test's path must fail
on another one, and it does.

### The test that could not fail

"No value ever reaches a log" was written first with pytest's `caplog`. It passed. It would also
have passed against a loader that printed every secret in full — these logs go through structlog
and never reach the stdlib handler `caplog` watches. For the one property whose failure is a
career-ending incident, a green that means nothing is worse than no test at all. It captures
through `structlog.testing.capture_logs` now, and the same trap is worth remembering anywhere else
this project asserts on log output.

One mutation survived and it was **my test's fault, not the code's**: `V5` says a secret-id file
that cannot be read is _named_ rather than fallen through, and the assertion matched only on the
variable's name — which the "no secret-id anywhere" message also contains. It passed against a
version that silently gave up. Matching on what _distinguishes_ the two messages catches it, and
the harness earned its keep again by pointing at an assertion rather than at a line of code.

Rotation is a restart, and that is written down as a decision rather than left as a gap: live
re-reading needs a refresh loop, lease renewal and a story for in-flight work, and it would put
back exactly the availability dependency FR-5 removes.

---

## 2026-08-06 — Foundry, and the claim ADR-0011 was making

The third platform, and it cost a routing axis. `FoundryTransport` (endpoint, credential,
api-version) × the **unchanged** OpenAI dialect × `AzureRoutes`. The dialect gained nothing; the
mappers gained nothing.

`ADR-0011` claims transport × dialect × model identity is enough structure for a third vendor.
**The diff does not leave `upstreams/`**, so the claim survives its first real test — and the
architecture assertion caught the first draft, in which `AzureRoutes` had been written into the
_dialect's_ package. A dialect that names a platform is one the next platform cannot reuse, so it
moved to `upstreams/foundry/`. The assertion now refuses "azure" above the platform packages, with
one stated exemption: `residency.py` names every cloud's regions on purpose, because a list that
could not name Azure's would be the per-cloud list `ADR-0012` §6 rejected.

The addressing is the part with money in it. Azure puts a **deployment** in the path — a name
chosen by whoever created the resource, saying nothing reliable about the model. If that name were
allowed to be the model name, every use case's pipeline config would embed Azure resource naming,
and pricing would break _quietly_: `FRD-403` prices by model, a deployment called `production` has
no price, and unpriced traffic is counted apart rather than as zero. Nothing would fail; the spend
figure would simply stop being complete. So the response is attributed to the model the caller
named, and `F1` is the mutation that says so.

Two smaller decisions. **One adapter per region** rather than one adapter carrying a region:
provenance is per model, and flattening a fleet would put a residency claim on a row the request
did not satisfy — worse than recording none. And `headers()` became **async**, so an Entra token
can be minted and refreshed rather than read once at construction; the captured version works for
an hour and then fails for the life of the process, which is a failure only a long-running
deployment ever sees.

Not verified against a real subscription — there is none here, and saying so is the honest half of
"done". 18 hermetic tests, mutations `F1`–`F6`.

---

## 2026-08-06 — 174 edge cases against the running API, and four defects

A sweep of everything a caller can get wrong: malformed bodies, unusual text, every shape of bad
credential, impossible options, attachments that are not what they claim, both surfaces' error
vocabularies, wrong HTTP methods, a burst of fifty bad requests at once. Each case asserts three
things rather than one — **never a 500**, a status a caller can act on, and a message that _names_
the problem. The third is the half most suites skip, and it is what "understandable" means in
practice: "validation failed" is a correct answer and a useless one.

Four defects, all reaching a deployed gateway, none visible to a suite that only sends requests it
already believes in.

**A malformed body became a 500 on the KIRA surface.** Its `details` array is pydantic's
`errors()`, and whenever a _custom_ validator raised — ours does, for "a part carries either text
or data" — that list carried the original `ValueError` **object** in `ctx`. Not JSON serialisable,
so rendering the refusal raised, and the framework turned the caller's mistake into our error, on
the one surface whose contract _is_ its error shape.

**The same surface could not render a shared control's refusal at all.** `api/serving` is
deliberately surface-agnostic and raises its own error type; the KIRA renderer had no branch for
it, so every one of those refusals fell through the catch-all and became a 500. A control that
works but cannot be _reported_ on one of the surfaces it protects.

**A non-positive output cap was accepted.** `maxOutputTokens: -1` returned 200 — and `words[:limit]`
with a negative limit does not mean "no limit", it drops the end of the answer. A truncated
response, a 200, and no explanation.

**A request that asks nothing was served and billed.** `parts: []` → 200. `FRD-113` FR-7 already
refuses an empty _embedding_ input and names the reason — it prevents a class of accidental no-op
billing — and the argument had simply never been applied to generation.

Plus a consistency finding: an unroutable path answered with the framework's own
`{"detail": "Not Found"}`, a different shape from every other error the same API produces, handed
to the caller least equipped to deal with one. Each surface now renders routing errors in its own
envelope.

### And one thing the harness would not let me claim

`X3` — "a validation detail carries nothing unserialisable" — was written as a mutation and never
went red. The reason is that the fix is **doubly enforced**: a flag on `errors()` _and_ a
comprehension that copies two named fields, either sufficient alone. No single-line edit reproduces
the 500. So it was removed rather than kept, and the harness's notes gained the rule: a property
guarded twice cannot be expressed as a mutation, and that is not a reason to weaken the guard.

The other two survivors (`X4`, and `T10`/`E8` before them) were the same too-narrow test selection
for the third and fourth time. That warning has earned a concrete rule now: **name the files whose
tests you expect to fail, not the file the code lives beside.** They are unrelated, and the second
is the one that comes to mind.

18 hermetic tests hold the four defects, because a defect found at the outer layer belongs in the
innermost one that can hold it.

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
tokens, use case, credential. That is the only way to know the shared controls were _run_ rather
than merely present. A KIRA caller meets the same budget and gets the 429 in the predecessor's
vocabulary (`EXTERNAL_KI_API_TOO_MANY_REQUEST`), which is exactly what a compatibility surface
should do: same control, its own words.

**Two mistakes of mine, each made more than once, worth recording because they are the failure
modes of this _kind_ of test rather than of this system.**

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
system's behaviour. What matters is _that_ a machine is identified.

**A fourth test mistake, and the most instructive.** The full integration run — which finishes
long after a file run does — failed on the streaming case that passed in isolation. The helper
looked up its audit row by **model** and not by use case, which seemed sufficient because each test
has its own use case and cleans up after itself.

It is not sufficient, for a reason that is the system working correctly. The audit writer runs
beside the request path, so it can flush a row _after_ the fixture teardown has deleted rows. The
row survives as an orphan — 493 of them in this database — and the next test reads it as its own.
Those orphans are **right**: an audit row must not vanish because somebody deleted a use case, and
that is `ADR-0013`'s whole point. The test had assumed the opposite of a deliberate property.

All four of this session's test mistakes had the same shape: **a test failure that looks exactly
like a system failure**. For a missing audit row that is the most expensive confusion this project
can have, which is why they are written down rather than quietly fixed. Scoping the query to the
test's own use case also cut the suite from 11 minutes to 94 seconds — the old version spent its
time waiting for rows that were never going to be its own.

**173 mutations, all defended.** The nine added this round (`O1`–`O8`, `B8`) were caught on their
first run — and one _older_ entry surfaced as undefended: `B3`, "unknown cost is counted apart, not
summed as zero", whose anchor had been absorbed into the new upsert. Repointed at the line that now
carries the rule and shown to fail before being accepted, because an entry that has never been red
claims a protection it has not demonstrated. That is the second time this session an anchor moved
with a refactor; it is the harness's most common false report and the reason it names them rather
than skipping.

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

**1. A model name may contain a colon.** `model:method` was split at the _first_ one, which was
correct for as long as Google was the only vendor. A self-hosted model is called `qwen3:0.6b`, so
the split produced the model `qwen3` and the method `0.6b:generateContent`, and the answer was
**"Model 'qwen3' not found"** — a message naming a model nobody asked for, pointing at the catalog
instead of at the parser. The verb never contains a colon and the model may, so it splits from the
right.

**2. A comment claimed a rule the system did not have.** `build_openai_upstreams` said a locally
declared region was "recorded, not checked" — and the first real request came back _"runs in
'on-premises', and this request may only be processed in [...]"_, because `RegionAllowed` quite
correctly checks every model that declares one. The comment described an intention; the code had a
rule; the rule was right. So a server now declares **no** region unless the operator names one —
no claim, nothing to enforce, a laptop keeps working — and naming one opts in to both the evidence
and the check, which happens **at startup** rather than as a 400 on every request.

**3. The budget counter was racy in two ways, and one of them was silent.** Twenty concurrent
requests against a fresh budget produced two **500s**: `record` read the counter, inserted it when
absent, and committed, so two requests arriving as the _first_ of a period both inserted and one
lost on the primary key — a 500 for a request that had already been served and charged for.

The quieter half has no error at all. `record.tokens += n` reads the loaded value and writes an
**absolute** one, so two overlapping writes discard an increment. The counter that is supposed to
be the system of record drifts _below_ the truth, in the direction that spends money, under exactly
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
green suite against it proves the gateway is _self-consistent_ — which is the failure the mutation
harness exists to warn about, one level up.

So Ollama joins the stack behind a `verify` Compose profile. **Built as the OpenAI dialect, not
against Ollama's native API**, and that is the whole reason it was worth doing now: `ADR-0011`
already said the OpenAI wire format arrives regardless of `FRD-106`, because `FRD-120` (Azure
OpenAI) needs it. Building against the native API would have been a fourth dialect serving only us.
This way `FRD-120` shrinks to a transport, and the deferred OpenAI _surface_ gets cheaper too.

The dialect turned out to have its own version of a trap the other two already taught us. Anthropic
splits usage across two events, so a last-event-wins mapper reported zero input tokens for every
stream. Here, **usage arrives in a final chunk with an empty `choices` array** — a mapper indexing
`choices[0]` loses it — and the vendor reports no usage on a stream _at all_ unless
`stream_options.include_usage` is sent. A stream that reports no usage is _released_ rather than
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

### What is _not_ verified yet, and why that is written here

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
produce the _same number_, and they do: resolved after routing against the model that will serve
the request, then handed to `enforce_pre_dispatch` as `extra_tokens`. `None` and `disabled` stayed
distinct on purpose: the first means the model was never going to think, the second means it
_would have_ and this request is switching it off, and collapsing them lets a declared default
quietly win over a caller who asked for none.

**Structured output** turned out to be the clearest case of `ADR-0011` rule 3. One flag,
`structured_output`, over three unrelated mechanisms: Gemini has a schema parameter, Anthropic has
none and needs a forced tool call read back out of a `tool_use` block, Azure has a third. The flag
says _whether_; the dialect owns _how_. The schema itself is parsed rather than passed through, so
an unknown field is an error **naming the field** — and then forwarded, never executed, because
re-validating would mean running caller-supplied regexes over provider output on the hot path,
which is the exposure `ADR-0007` already refused by a different door.

§5.3 is the part that justifies the design and it is the test that had to be written to fail first:
with a fallback chain, checking the capability against the model the _caller named_ protects
nothing. The primary declares it, the primary fails, the fallback answers in prose, and a caller
calls `JSON.parse` on it — surfacing days later as a bug in somebody else's code.

**Embedding** carried a control bypass. `FRD-405`'s bucket took one token per request, so a batch
of 500 admitted as one request would have turned a limit of 10 per minute into 5 000 texts per
minute: intact on paper, gone in practice. The bucket now takes a `cost`, in the same all-or-
nothing Lua pass, and the budget books n requests. A batch too large for the bucket's _capacity_ is
refused with a message naming which of the two said no, rather than a `Retry-After` that would
still be wrong an hour later.

### Three things the tests found

The suite caught a **regression in my own design**: the plan had the predecessor's default task
type filled in by the mapper, which meant every embedding against a model nobody had declared task
types for was refused as though the caller had asked for something impossible. The default is a
_surface's_, applied only where the model declares it — and an explicit undeclared type is still
refused. Naming a type we cannot verify is a request; naming none is not.

`check_declaration` compared `method == "embedContent"`, so the new batch verb demanded the
_generation_ capability — refusing every batch against an embedding-only model and accepting one
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
one before dispatch" lived in `check_declaration` _and_ in `embedding.validate`, so removing either
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
non-`disabled` default thinking mode is refused**, because the predecessor _applies_ that default.
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
way to be sure no step was _skipped_ rather than merely present.

Seven existing mutation anchors followed their functions into the new module and were repaired.
A mutation whose anchor has moved protects nothing, which is exactly why the harness reports a
missing anchor instead of skipping it — one of them (`M23`, "every verb passes the pre-dispatch
controls") also had to have its _text_ corrected, and it briefly survived until it did.

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
spelling out why it is not merely tidy: a dropped attachment produces _no error_. It produces a
fluent, confident answer about a document the model never saw, returned with a 200, and the caller
reports that "the model is hallucinating" and looks for the fault everywhere except where it is.

So a model that cannot read what was sent is refused **by name**, with the types it lacks, and the
message distinguishes _undeclared_ (a catalog gap somebody closes in a minute) from _declares no
attachment support_ (a fact about the model). Checked after routing at every hop, on the mechanism
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

A question from the owner — _is the region set for the whole gateway, or can it be bound to a use
case?_ — turned into a smaller and more urgent finding than the one it asked about.

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
anyway, because the _per-hop_ check is the part that would otherwise be got wrong when residency
becomes a per-use-case property. Media types (`FRD-110`) and the schema capability (`FRD-112`) plug
into the same mechanism.

One existing test had to change, and the change is the point: it asserted that an exhausted chain
raises `UpstreamError` — it encoded the misleading behaviour. It now pins both halves: a chain with
nothing to offer is not an outage, and an upstream that _was_ tried and failed still is.

**A follow-up question found the real version of the same mistake.** _"Wird es auch für Azure
`westeurope` funktionieren?"_ — and the honest answer was: the mechanism yes, the configuration no.
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

**Residency is enforced rather than intended.** A configuration that _can_ express a non-EU region
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
concatenated rather than reduced to the last, cache tokens counted as input because they _were_
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
removing the block-type filter changed nothing, because the _field name_ differed too. It passed
for a reason that had nothing to do with the property it was named after. Rewritten to put the
reasoning in a `text` field, which is what actually holds the selection to being by **block type**;
a second test pins that an unknown future block type is dropped too.

The integration layer also caught a race it had always had: the test asserted that _its own_ relay
published the event, and on a full stack the `management-relay` container usually wins. It now
asserts the outcome — the row reaches the gateway — rather than who delivered it.

Verified: 699 hermetic tests (97.1%), **116/116 mutations caught**, 69 integration tests, 239
frontend and 46 browser tests. Not built here, deliberately: the thinking, structured-output and
attachment mappings, because the canonical core does not carry those fields yet and a mapper for a
field that does not exist is a guess.

---

## 2026-08-06 — Stufe 1: the model catalog becomes a runtime authority

`FRD-114`. The catalog held prices; it now holds what a model may be _asked to do_, and the gateway
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
accepted must not be called _prices_. That cost four raw-SQL integration tests an update, which is
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
  means a _served_ request went unrecorded, and failing loudly is the defensible answer to that.
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
connections (_easily extensible_), then document handling, then the review findings** — with the
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
_approximate_: KIRA applies a model's default thinking when the caller sends none, so Stage A either
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

The owner restated what AIRA Gateway _is_, as seventeen features. It now sits in **PRD §1.1** with an
honest status column, because a list like that is only useful if the gaps are in it too.

Three things the table makes visible that prose was hiding:

**The governance features are largely built; the evidence features are not.** Budgets, limits,
routing, fallback, self-service pipelines, roles — done. Auditability, incident response, anomaly
detection, model smoke tests — missing. Those four are what make a governed system _defensible after
the fact_, which is the half you need on the day something goes wrong.

**Feature 5 is more specific than "store requests and responses".** _"Welches System wann was womit
aufgerufen hat"_ — and checked against the code, the **system** is exactly the part we cannot
answer. An API key has a `prefix` (its identity) and a `subject` (the person who issued it); the
audit row records the subject and never the prefix. Five keys issued for one use case by one
administrator are one identity in the log. The consequence lands precisely where it hurts most: a
leaked key can be revoked, but the blast radius cannot be assessed — which requests came from it,
what they asked, over what period, none of it separable from its siblings' traffic. Added to
`FRD-122` as FR-5.

**Feature 3 settles `ADR-0010`.** Naming KIRA-API compatibility as a _central feature_ is the
decision that ADR was waiting for. Accepted as Option C — the compatibility surface is built, with a
sunset date and its usage visible in reporting, because that is the half that keeps a compatibility
layer from becoming permanent. `FRD-107` is unblocked.

New: **`FRD-504` — model smoke tests and jailbreak batteries.** IT Security's question is not "was
this request allowed" but "does the model we approved for the whole organisation still refuse what
it should". Two design points I want to keep visible:

- **A rate, not a verdict.** Models are sampled: the same jailbreak prompt can be refused nine times
  and answered on the tenth, and _that is the finding_. A single-run boolean would show green nine
  times out of ten and the tenth would look like a flake to re-run away. So each case runs _n_ times
  and the result is "3 of 20", never "failed".
- **Two modes, because the pipeline would block the test.** `FRD-300`'s injection filter exists to
  block exactly the prompts a jailbreak battery sends — run through it, most cases never reach a
  model and the run says nothing about the model. So: _through the pipeline_ (does our filter catch
  it — the first honest measurement of whether that filter earns its place) and _direct to the
  model_ (does the model resist it), reported side by side. The interesting cell is the one where
  the filter misses **and** the model answers.

Runs go through the gateway's own path under a dedicated internal use case, so their spend is
attributed and bounded rather than exempt — and the result page states that it is one battery on one
day and not a safety statement, for the same reason unpriced traffic is counted apart: a figure that
reads as complete when it is not causes worse decisions than an absent one.

---

## 2026-08-06 — "Auditierbares Hirn": a scope sentence, and four places we do not earn it yet

Direct model access confirmed — the Vertex publisher and endpoint APIs, not the platform's agent
surface. That closes the last open question in `FRD-115` §11 and `FRD-119` §11.

What came with it is worth more than the answer: _the gateway's job is to provide **auditable
brains** for AI use cases._ That is a scope sentence, and it settles questions that had not been
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
   attribute — and spans are **sampled**. So _why_ a model was chosen, for the one component that
   makes a judgement about a caller's prompt, is durably recorded nowhere.
4. **Degradation is global, not per-request.** `DegradationLog` says what is broken _now_; an audit
   needs what was broken _then_. A request budgeted on the racy Postgres fallback is
   indistinguishable from one with the atomic guarantee.

`FRD-122` closes all four: one recording site at the route's exception boundary (not one per
`return _error(...)` — that is the shape that let `:embedContent` bypass the pre-dispatch gate);
`requested_model` alongside `model` so existing reports and indexes keep their meaning; decisions
but **never the classifier's reasoning text**, which is model output about a caller's prompt and
inherits every question the prompt has; and the degraded set frozen onto the row.

One thing decided rather than deferred: recording refusals means a caller in a retry loop writes a
row per attempt. That is the right increase — a retry storm is _precisely_ the event the audit
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
dialect is needed on the _Vertex_ transport, not only on Foundry. `ADR-0011`'s separation starts
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
changes _what comes back_ — an attachment a model cannot see, a thinking mode it cannot honour, a
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
authentication, and a different notion of what a model _is_ — Azure addresses a customer-named
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
  price, and unpriced traffic is _counted apart rather than as zero_ — so the spend figures would
  not break, they would quietly stop being complete.
- **Capability flags say whether, never how.** Three vendors, three unrelated structured-output
  mechanisms (a schema parameter, a forced tool call, a `json_schema` response format) and two
  reasoning shapes (token budget, effort level). The flag stays a boolean; the mechanism lives in
  the dialect.

Two pleasant confirmations. `FRD-111`'s canonical thinking model — `mode` + optional `tokens`, taken
from the _predecessor's_ vocabulary — turns out to cover Azure's `reasoning_effort` levels, a vendor
it was not written for. And Azure reports reasoning tokens separately, which finally answers
`FRD-111` FR-6's open verification for at least one vendor.

One planning consequence: the **OpenAI wire format now arrives as an upstream whether or not
`FRD-106` is ever built.** Once canonical ⇄ OpenAI exists in one direction, the deferred OpenAI
_inbound_ surface is largely that mapping reversed plus a router. The decision to defer it stands;
the estimate behind that decision does not, and should be revisited when it next comes up.

`ADR-0011` and `FRD-120` written; `FRD-114` (model identity and addressing), `FRD-115` (shared
token source, region allow-list generalised), `FRD-111` and `FRD-112` (the third mechanism) amended.

---

## 2026-08-06 — Model Garden answers one question and opens another

Two facts landed after the parity FRDs were written, and both change them.

**EU residency applies.** `FRD-115` moves from "worth doing" to required: our current adapter calls
a global endpoint and cannot make a residency statement, so it is not a production candidate no
matter how complete the rest becomes. The FRD now also _enforces_ it — an allowed-region list, a
model configured outside it refuses to start, and provider, publisher and region recorded on every
audit row. Configuration alone would not hold: someone adds a model in `us-central1` because that
is where a preview launched, and nothing objects.

**Access is through the Gemini Enterprise platform's Model Garden — Gemini _and_ Anthropic**, one
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
  own test. Their token _count_ still reaches usage, because they were billed.
- Anthropic's thinking budget is drawn from `max_tokens`, so `budget < max_tokens` becomes a
  validation rule and the catalog must refuse to hold a combination that cannot work.
- **There is no `responseSchema`.** Structured output is a forced tool call — one tool whose
  `input_schema` is the caller's schema. So `FRD-114`'s `structured_output` flag means "by some
  mechanism", the adapter refuses schema fields it cannot express faithfully rather than dropping
  them, and `FRD-112` §5.3's post-routing capability check stops being defensive and becomes
  load-bearing.
- **No embeddings at all**, so `FRD-113` is Gemini-only and the capability declaration is what
  enforces it — before dispatch, not by an adapter raising deep in the stack.

New `FRD-119` for the dialect; `FRD-115` rewritten as the _platform_ (transport, OAuth, region,
registry) with the two dialects above it. The seam matters: put authentication in the adapters and
it is written twice, put body mapping in the transport and a third vendor rewrites it. `FRD-110`'s
media-type allow-list becomes an intersection of what AIRA accepts and what the target model
accepts, checked after routing for the same reason the schema capability is.

This is also the first honest test of `FRD-100`'s claim that the canonical core is
provider-agnostic — until now "two upstreams" meant two spellings of Google's format. `FRD-115` §10
carries an architecture assertion for it: if the diff reaches outside `upstreams/`, the core is
Gemini-shaped and we should fix the core rather than smuggle a vendor field through it.

One question deliberately left open in `FRD-115` §11: whether "Gemini Enterprise" here means Model
Garden _raw model access_ (assumed throughout) or the agent platform's own API, which is not a
model API and would model grounding and server-side conversation state that our canonical core does
not have. One authenticated `curl` against the project's `publishers/anthropic` endpoint settles
it, and getting it wrong is a rewrite rather than a correction.

---

## 2026-08-06 — KIRA parity: the programme, and where the gap actually is

The predecessor's requirements arrived with the instruction
that AIRA must carry all of them. Reviewed against the code rather than against our own
documentation, the result was not what the phase history would suggest.

**In breadth we are well ahead of the contract we have to carry.** Use cases with object RBAC,
self-service keys, budgets down to spend, cross-instance rate limits, the pipeline, Kafka config
distribution, the management UI, retention and cost reporting are all ours to keep.

**In the core request path we are behind, and further than it looks.** `CanonicalMessage` carries
exactly one field, `text: str`, and the Gemini surface's `Part` requires `text` — so a request with
`inlineData` is not merely unmapped, it is **rejected with a 400**. The predecessor accepts
documents and images in fourteen MIME types, controls the thinking budget, and forces JSON output
against a schema. None of that exists here. Its embedding path takes eight task types, batches and
two dimensionalities; ours takes one string.

Two findings I had not expected to matter as much as they do:

- **Vertex AI, not the Generative Language API.** The contract assumes EU-regional endpoints
  reached with a service account; we call the global endpoint with an API key. If a data residency
  requirement sits behind that — and an EU-regional assumption is decent evidence — then no amount
  of feature parity makes our adapter a replacement.
  `FRD-115`, and it may be the most schedule-critical item in the programme.
- **Vault is in the stack and nothing reads from it.** `CLAUDE.md` §2 has said "secrets only in
  Vault" since Phase 0; every secret actually comes from an environment variable. `FRD-116`. This
  becomes pressing rather than untidy the moment a service-account _private key_ is involved.

Eleven documents written: `ADR-0010` plus `FRD-107`, `FRD-110`–`FRD-118`, `FRD-602`.

**The one open decision** is in `ADR-0010`: does AIRA also serve the predecessor's _wire contract_,
so clients migrate by changing a URL, or do the clients move to the Gemini surface? My
recommendation is the compatibility surface **with a stated sunset date and its usage visible in
reporting**, because the alternative couples our decommissioning date to the slowest consuming
team, and until they migrate their traffic is ungoverned — which is the whole thing the budgets and
limits exist for. Recorded as _Proposed_; `FRD-107` stays blocked until it is decided. Everything
else is contract-independent and can start immediately.

Three security settings the FRDs fix independently of the contract, each written down so it is a
decision rather than an omission: TLS verification stays on; CORS is an origin allow-list, never
`*` with credentials; and `GET /models` requires authentication. A fourth is close to it —
resolving group membership from the UserInfo endpoint on **every request** would make each
authenticated call depend on Keycloak
being up and fast; `FRD-118` §11 asks whether that requirement even applies to us before anyone
builds it.

Three design points inside the FRDs are worth repeating here because they are the ones most likely
to be got wrong quietly:

- **An attachment costs tokens no character count predicts** (`FRD-110` §5.3). The pre-dispatch
  reservation would estimate a 20 000-token PDF request as a sentence, reopening under documents
  precisely the race `FRD-405` closed for text.
- **A batch must not be a way around a rate limit** (`FRD-113` §5.3). One token per request means a
  caller limited to 10 requests a minute can embed 5 000 texts a minute. A batch of _n_ takes _n_.
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
asserts the _shape_ of the config, because the behaviour it protects only appears after a
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
retention _idempotence_ test, because the SQL-level `is_not(None)` still sees the difference. The
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

One thing deliberately _not_ done: refusing a key at authentication time because its use case is
unknown. It looks like cheap defence in depth and is not — keys and use cases arrive on different
Kafka topics with no ordering between them, so a freshly issued key can legitimately reach the
gateway before the use case it belongs to, and the check would refuse it.

Proved end to end over the real event path: the key answers 200, the tombstone is applied, the
same key answers 401. Three mutations added (`make mutants` is now 29), including one asserting
that the request log is _not_ deleted — with a local import inside the mutation, so it fails on a
test rather than on a NameError, which would have counted as caught for the wrong reason.

---

## 2026-08-05 — Proving the tests can fail (`make mutants`)

Prompted by the obvious question after the review: the suite was green, coverage was 99%, and
seven real defects were in there anyway. How?

Three different mechanisms, not one.

1. **The tests were written from the code, not the requirement.** A test named "both scopes apply
   and the stricter wins" asserted _alice is refused_ — which is what the code did. The
   requirement said more: _and it must cost the use case nothing_. Test and code came from the
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
not consume the whole use case. The code took a token from the wide use-case bucket _first_ and
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
disconnect test I wrote first passed against the _old_ structure — going through `TestClient`
buffers the whole body, so it never reached the path it was named after. Each fix was proved by
restoring the defect and watching the test fail.

---

## 2026-08-05 — Rate limiting, atomic budget reservations, and the audit write off the hot path

`FRD-405`, decided in `ADR-0008`. Three defects with one cause: the gateway acted on state it had
already stopped being sure about.

**Nothing limited how fast a caller could consume.** Measured on the running stack, one request
opened six to seven separate database sessions — so a client in a retry loop exhausted the
connection pool, and the first casualties were the _other_ use cases. A budget states how much
may be spent, never how fast.

**A budget could be exceeded by a multiple.** `guard` read the period's usage, dispatch ran, then
`record` booked it. Requests in flight were invisible to each other's guard, so twenty concurrent
requests all passed a limit with room for one. Since `FRD-403` that limit is a sum of money, which
made it an accounting defect rather than a cosmetic one.

**The audit write blocked the answer.** `record_request` was awaited before the response
returned, contradicting `CLAUDE.md` line 55 — _persistence must not block the request path_.

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
deliberately _not_ to allowing everything: Redis being down is when infrastructure is already
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

1. A `<label for="x">` that also _wraps_ its input makes a real browser forward the click twice:
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
response bodies; the row and its metadata stay. A seven-day _row_ retention was the obvious first
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

**Still open, and stated in the FRD**: content redaction. Retention decides _when_ a payload
goes; nothing yet masks sensitive values _inside_ one while it is kept.

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

**Caveat**: the workflow runs on push, but a green run only _blocks_ a merge if branch protection
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
- **Per-request cost in `request_logs`**, so spend can be _reported_, not only capped.
- **UI**: the budget tab leads with a spend limit and a spend bar; a new **Models & prices**
  screen (read-only unless global-admin).

**Two decisions worth recording.**

_Money is an integer, never a float._ Amounts are nano-units (10⁻⁹ of the currency) in `BIGINT`,
via the new `aira_common.money`. Floating point cannot represent 0.1 exactly and a spend figure
is the sum of millions of small charges; `NUMERIC` would be exact on Postgres but SQLite — which
the tests run on — stores it as a float, so the tests would not exercise production behaviour.
Amounts therefore also cross API boundaries as decimal **strings**, never JSON numbers.

_Unknown is not zero._ A request on an unpriced model did cost money; AIRA just cannot say how
much. Booking it as `0` would make the spend figure silently too low — the worst failure mode for
a number somebody is accountable for. It is counted under `unpriced_requests`, excluded from the
cost total, does not consume the cost budget, and is named in the UI. In the same spirit, the
display never renders a non-zero amount as `0.00`; it widens its precision until it is truthful.

Also fixed along the way: adding a positional `cost_nanos` to `BudgetService.record` made the
existing callers pass their _timestamp_ as an amount. Both extra arguments are keyword-only now —
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
   was declared on _aira-gateway_. The shared dev virtualenv hid it; the isolated management
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
  property changed from _code_ schedules no re-render. Clearing the create-use-case form and the
  member/key/budget forms from their success callbacks therefore left the submitted text sitting
  in the inputs, and switching a budget to member scope did not reveal the username field. All
  form state moved to **signals** with explicit `[ngModel]`/`(ngModelChange)` binding; regression
  tests assert the DOM, not just the model.
- **Nothing failed silently any more.** Every load and every mutation now reports its outcome:
  a new `errorMessage()` helper unwraps the shared `{"error": {...}}` envelope (including DRF's
  per-field `details`) so the server's own wording is shown. This mattered most right after the
  ADR-0007 pass: a use-case viewer clicking "Issue key" got a 403 and _no feedback at all_ — the
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
  **504 → `504 DEADLINE_EXCEEDED`**; everything else (upstream 4xx from _our_ key/config, upstream
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
  - `use_case_members` (Alembic 0002); `worker` (aiokafka) + `decode_event_type`. `make kafka-topics`
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
  stopped** (in-memory SQLite, fake JWKS, mock provider). The earlier curl checks were _manual_, not
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

- **Problem addressed**: an OIDC token authenticates the _identity_, not _which use case_ — a user
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

## 2026-08-08 — The security round: every finding fixed, nothing taken away

A full read of the code after four weeks on authentication, roles and group grants
(`ADR-0015`, `FRD-406`). The instruction was to fix every finding **and keep the framework's
functionality**, which turned out to be the harder half and the more interesting one: the demo,
the published demo key, `?key=` authentication, the CLI break-glass key, a laptop's
zero-configuration start and a useful `/readyz` all had to survive their own fixes.

**The one that mattered was found by sending a request, not by reading.** The KIRA surface asked
`if memberships and header not in memberships` — so an **empty** membership list meant "anything
goes" rather than "nothing". A caller belonging to no use case at all could send
`X-AIRA-Use-Case: somebody-elses`, get a real answer, and have the tokens billed to that use
case's budget and written into its audit trail. The Gemini surface refused the identical request
in both of its selector forms. Proven live; `request_logs` showed the row, attributed to the
victim. Cause: the rule existed correctly in one place and was **restated by hand** on the second
surface — the same shape as `FRD-126`'s pre-dispatch order, `FRD-206`'s permission predicates and
`FRD-602`'s export scope. It now lives in `use_case_refusal`, returns a _reason_ rather than
raising, and each surface wraps it in its own envelope, which is the only thing that should
differ. The deliberate exception survives inside it: an **unbound** API key stays unrestricted,
because break-glass exists for the moment the control plane is gone.

**A convenience default is a production default, one variable away.** `ADR-0007` made Management
refuse to boot outside `local` with development defaults; the gateway — which serves the traffic,
holds the upstream credentials and writes the audit trail — read `environment` for telemetry and
acted on it nowhere. `aira_gateway.security` now mirrors it: open routes, the published Postgres
password, and OIDC with no audience each stop the process, all reasons named at once. The check is
**environment-shaped rather than stricter defaults**, and `AIRA_DEMO_MODE` exempts outright — a
hardening pass that breaks the demo is a hardening pass that gets reverted.

Six more, each with the same character — a rule that was right in one place and absent in another:

- **A credential was redacted in the trace backend and written verbatim to the access log.**
  `?key=` has been kept out of exported spans since `ADR-0007`; the web server's own request line
  went to stdout intact, which is the _more_ widely readable of the two. A logging filter now
  rewrites the arguments (not the formatted message — uvicorn formats after filters run), and
  deliberately redacts any string argument carrying a query rather than the one positional index
  uvicorn happens to use today.
- **A claim that is absent is not a claim that passed.** PyJWT verifies `exp` when present and
  accepts a token carrying none — a credential that never expired. `exp`, `iat` and `sub` are now
  required. The audience stays optional in the verifier and **required by deployment**, so a
  laptop keeps working against a realm with no audience mapper.
- **The verdict is public; the diagnosis is not.** `/readyz` stays unauthenticated (a probe
  carries no credential, and one answering 401 reports every pod unhealthy), but the body naming
  the database host, Kafka host, every upstream and the current fallbacks now needs an
  authenticated caller. Locally, everything is shown.
- **A control that needs a verified identity cannot bound a caller who has none.** Every limit
  `FRD-405` built is keyed by use case or member. Authentication _failures_ are now bounded per
  source address — **counting refusals only**, so a working credential never touches the bucket
  and the bound can be low enough to be worth having. Behind an untrusted proxy the whole
  deployment shares one bucket, and that is tolerable _because_ of the refusals-only rule: the
  worst case is somebody else's typo answered 429 instead of 401.
- **A key with no end date has to be inventoried.** First shipped as an _optional_ expiry, on the
  argument that "an expiry which cannot be omitted is one somebody sets to the year 3000" — and
  that argument is about the **maximum**, not the default. Corrected the same day: every key is
  bounded, `AIRA_API_KEY_DEFAULT_DAYS` (30) applies when nobody names one and
  `AIRA_API_KEY_MAX_DAYS` (180) is refused past rather than truncated. Neither plane can mint an
  unbounded key, the break-glass CLI included — a credential minted by hand during an incident is
  precisely the one that outlives its reason. Keys issued before the change keep working and are
  marked "no end date" in the console: expiring them would be an outage chosen on the operator's
  behalf, and a silent one.
- **`create_all` beside Alembic** let a partially-deployed stack undo a migration (`FRD-114`
  recorded it happening). It now runs for SQLite only, and the consumer — which is where it bit —
  does not call it at all, since it already waits for `alembic upgrade head`.

**`FRD-406` finally does something.** The `Redactor` hook has existed since Phase 1 and was a
no-op, so a stored prompt was a verbatim copy of whatever a caller sent. The redaction is
deliberately **narrow**: credential shapes only (AIRA keys, `AIza…`, `sk-…`, `Authorization:`,
JWTs, PEM private key blocks), because names, customer numbers and prose are _the work_ — a
redactor that mangles them produces payloads nobody uses, and the deployment then switches storage
off entirely, which is strictly worse than storing them. An unusable pattern **stops the gateway**
rather than silently matching nothing, which is the `FRD-125` failure exactly: an absent control
wearing a present one's badge. Deployment patterns are **additive**, never replacing, or the first
organisation to name its own token format stops redacting Google keys.

Two test notes. The redaction requirement is proved twice on purpose — once against the class and
once by posting a prompt at the route and reading the stored row back, because `FRD-124` and the
CSV export both recorded on one day that a requirement exercised only against the class leaves the
wiring undefended and coverage cannot see the difference. And the authentication-bound tests run
with `redis_url=""`: the first version failed at the _first_ request because a Redis left running
on the machine still held the bucket from a previous run — a bound that is a property of the
process must be tested as one.

Mutations `H1`–`H20` (the `S` prefix was taken; the harness refuses a duplicate id, which is how
that was caught). Two self-inflicted findings worth recording, both about _where_ a thing was
written: the new mutation block was inserted into `survivors = [...]` in `main()` instead of into
`MUTATIONS`, so all seventeen were reported "undefended" **by construction** and none had ever
run; and a `validate_<field>` method does not execute for a field the caller **omitted**, which is
exactly the case that had to end with a date — every existing test issued an unbounded key straight
past it. Both were caught by a suite that disagreed with the code, which is the point of having
one. `A4` was **re-anchored**: adding the required-claims list turned the JWT
options into a multi-line dict, and a mutation whose anchor has moved protects nothing.

## 2026-08-08 — Agents and coding assistants: the gap, measured against the code

The named use cases are a RAG chatbot, semantic search over embeddings, and **connecting coding
assistants such as OpenCode**. Checked against the code rather than the documentation, two of the
three already work and the third does not work at all.

- **Semantic search**: covered. `:embedContent` with batching and task types (`FRD-113`), budgeted,
  priced, audited. Vector storage is the caller's by `ADR-0013`.
- **RAG chat**: covered for the generation half — documents (`FRD-110`), structured output,
  streaming, `systemInstruction`. Retrieval is the caller's.
- **Coding assistants**: blocked on one field. `tools` and `toolConfig` are refused with a **400**,
  and an assistant's entire loop _is_ tool calling — it asks which file to read, gets a function
  call rather than prose, executes it itself, and sends the result back as the next turn.

**The refusal is right and its stated reason is wrong**, which is the finding worth keeping.
`api/gemini/schemas.py` cites `ADR-0013` — but that ADR says, in the same words it has always had:
_"The gateway may pass a tool definition through … but never executes anything."_ Passthrough is
explicitly in scope; execution is not. The message conflates them, so a reader arriving at that
error concludes the whole area is closed by decision. The real reason is different and was written
down nowhere: **`CanonicalRequest` has no field a tool declaration could travel in.** That is a
capability gap, and a capability gap gets built; a boundary does not. `ADR-0013` now says so
explicitly, and the same clarification was needed for caching: a cache _handle_ (Google's
`cachedContent`) is provider-side state and stays refused, while a cache _marker_ on content the
caller sends in full every time leaves the request self-contained and is a price, not a boundary.

Three FRDs, in an order the owner set:

- **`FRD-131`** — tool calling carried through the canonical core, never executed. **Per use case,
  default off**: a use case that summarises documents has no business declaring functions, and the
  smallest set that needs it is the right set to have it. Catalog capability, checked **per hop** so
  a fallback skips an incapable candidate rather than answering without tools — a 200 that a client
  will then parse as a function call. Two traps written down before they are hit: on Anthropic,
  structured output is _already implemented as a forced tool call_ (`FRD-119` §5.5), so tools plus a
  response schema is refused by name rather than silently losing one; and in the OpenAI dialect a
  tool call's arguments arrive **fragmented across chunks**, so a naive mapper emits several half
  calls.
- **`FRD-132`** — which surface an assistant needs, **measured before it is chosen**. Stage A points
  OpenCode at the running gateway and records what actually breaks; stage B builds a surface only if
  stage A says so. Reviving `FRD-106` is much cheaper than when it was withdrawn, because
  `FRD-123` built the OpenAI dialect as an upstream and a surface is largely its inverse. This
  project has repeatedly learned that a contract chosen by reading is a contract maintained forever.
- **`FRD-133`** — prompt caching, written now and **built last, by owner decision**: the assistant
  work must stand at full price so the saving is decided from `request_logs` rather than from an
  estimate. The FRD keeps open the possibility that the measurement says don't build it.

Two governance consequences recorded before they surprise anybody: an assistant makes **many model
calls per human instruction** — the `FRD-125b` shape at scale, except the calls are genuinely the
caller's — so limits and budgets calibrated for a chatbot trip immediately and "requests" means
something different on the reporting screen; and **a tool result is content the model reads**, which
the injection filter cannot see. Same blind spot `FRD-110` recorded for PDFs, one step sharper,
because the content comes from the caller's own machine and the model is about to propose the next
command.

**Five stale tests, each stale for a different reason, and each found by a layer above the one
that could have prevented it.**

_A shared database accumulates other people's global rules._ Three anomaly tests asserted on
`events[0]` after a tick. A tick evaluates every rule that applies to the scope — and a **global**
rule (`use_case IS NULL`) applies to every scope there is. Sixteen `alert` rules left behind by
earlier e2e runs meant `events[0]` was somebody else's finding, so the assertions read `alert`
where they expected `blocked`, `throttled` and `detected_not_enforced` while the product was doing
exactly the right thing. They now select **their own** `rule_id`. Latent since the rules were
written, visible only once enough junk had piled up — the worst kind of test to leave standing,
because it fails long after the change that exposed it.

_A test I wrote hours earlier and never ran._ The e2e case for key expiry asserted that leaving the
lifetime empty produces a key showing "never" — true when I wrote it, false the moment the bound
landed, and I updated the unit and API tests without touching it. It now asserts the opposite,
plus that the form states the policy the server enforces. **A test that has never been executed is
not a test**, and this one was written, committed to a file, and left unrun until the suite was
finally invoked.

_A one-line CSS rule whose effect nobody had measured._ The e2e layout suite failed on the
API-keys tab at phone width: **428px of document in a 360px viewport**. The offender turned out to
be `.sr-only` — the visually-hidden "Actions" column header. It is `position: absolute` with **no
positioned ancestor**, so it keeps its _static_ position relative to the **document** rather than
to the card that scrolls it. In a six-column table that sat at x≈427, and the whole page grew a
horizontal scrollbar. My new "Expires" column is what pushed it over the edge; the rule had been
wrong since it was written, and the rate-limits tab was quietly 8px over as well — never seen,
because the loop failed on the keys tab first and stopped.

Fixed with `left: 0` on `.sr-only`, which removes the static position from the equation for every
present and future use. **Not** by making `.table-wrap` a containing block: that would also clip
the info-hint popups, which are absolutely positioned inside table headers on purpose and are
_meant_ to escape. Nothing moves visually — `clip-path: inset(50%)` hides the element wherever it
sits. Every detail tab now measures exactly 360.

_And a test that was measuring more than it claimed._ `a live view refreshes without moving
anything` observed layout shifts with `buffered: true`, so it counted the **initial render** as
well as the refresh ticks it was written for. With 1919 findings in the database that render
reflows once — 0.0207 at t=101ms, a card arriving after the shell, which is ordinary for a
data-driven page and is not a control moving under the reader's cursor. Measured, then narrowed to
what the name promises: shifts _after_ the page has settled, still required to be exactly zero.

**Also fixed:** `test_management_api.py` scanned the use-case list as a plain array and failed after
the rebuild — server paging (`FRD-208`) had changed the body to `{count, …, results}` the same week,
and the integration layer is not part of `make ci`'s hermetic half, so nothing had told it. It now
**searches** rather than scanning, which it needed anyway: with hundreds of use cases in that
database, page one would not have contained a slug created a second earlier however the body were
shaped. The e2e round hit the identical trap with `ensureUseCase` on the same day.

## 2026-08-08 (later) — Stage A: OpenCode against the gateway, and a thinking mode that was not

`FRD-132` stage A, run rather than reasoned about. Ollama in the stack, `qwen3:4b` pulled because
it declares `tools`, a use case with a bounded API key, OpenCode 1.18.15 from npm pointed at the
**existing Gemini surface** via `@ai-sdk/google` with an overridden `baseURL`
(`tools/opencode/opencode.json`).

**The answer is B1: no new surface is needed.** Provider resolution, base URL, auth, model
selection, plain generation and SSE streaming all worked unmodified, and the client failed at
exactly one thing — `tools`, refused by name with the message `FRD-124` gave it. Reaching that
refusal is the successful outcome: everything up to the missing capability held, and the missing
capability is `FRD-131`, already specified. `FRD-106` stays withdrawn, and this run is the evidence
that was absent when it was withdrawn.

**One trivial instruction produced three gateway requests** — one served, one refused, one
`client_gone` — every one of them on the audit trail. §5's warning about assistants is now a number
instead of a caution: limits and budgets sized for a chatbot are wrong for this shape by a
multiple, and "requests" on the reporting screen counts a different thing here.

**The finding that had nothing to do with surfaces.** `reasoning_effort: "none"` does not mean "do
not think"; it means "do not emit a separate reasoning channel", and those are the same thing only
on some models. Measured on one Ollama, one prompt, within a minute of each other:

|               | `qwen3:0.6b`                  | `qwen3:4b`                                                  |
| ------------- | ----------------------------- | ----------------------------------------------------------- |
| field omitted | 115 tokens, content `"OK"`    | 132 tokens, content `"OK"`                                  |
| `"none"`      | **3 tokens**, content `"OK."` | **103 tokens, content = 480 chars of raw chain-of-thought** |
| `"minimal"`   | —                             | **400** `invalid reasoning value`                           |

The dialect maps `disabled` → `"none"` on a measurement recorded in the code — against the 0.6B
model, where it is correct. On the 4B model of the **same family, same server, same minute**, the
same mapping returns somebody's thinking _as the answer_, billed, with a 200. And the seed declared
`disabled` as the **default** for whatever model was configured, so this was the ordinary path, not
an edge case. A coding assistant against it would have received "Hmm, the user just asked me…" as
every answer.

Fixed as **data, not code**: both seeds now key the thinking declaration by model, from a
measurement, and a model nobody has measured gets no thinking declaration at all (`FRD-114` FR-7 —
absence of information is not permission). `qwen3:4b` no longer offers `disabled`, so `FRD-111`
refuses a request asking for it **by name**, which beats a 200 carrying reasoning. `minimal` is
gone from the `tools/` seed as well: the identical correction had been made in the _Management_
seed on 2026-08-06 and the second copy was never updated — one definition, two files, one of them
fixed.

The rule worth carrying: **a capability belongs to a model, not to a family, a vendor or a
runtime.** A declaration measured against one model is not evidence about its siblings, and the
seed that writes one declaration for "whatever model is configured" is the mechanism that turns a
measurement into an assumption.

## 2026-08-08 (later still) — `FRD-131` stages 1–4: a function call goes through the gateway

Tool calling, built in four stages, each run against the whole existing suite before the next —
the instruction was to add the capability without shooting anything down, and that is the part
worth describing.

**Stage 1, the canonical core.** `ToolCallPart` and `ToolResultPart` join `TextPart` and
`DataPart` in the ordered-parts union `FRD-110` created; `CanonicalRequest.tools` carries the
declarations; `CanonicalResponse.tool_calls` and `CanonicalChunk.tool_calls` carry the answer. The
whole suite passed unmodified, which was the bar `FRD-110` set. One existing rule needed changing
and it is the interesting one: `is_empty` refused a request with no text and no attachment, and
**the second turn of every agent exchange is exactly that** — nothing but "here is what `read_file`
returned". A tool result now counts as content, or the ordinary middle of an agent conversation
would have been refused as a no-op.

**Stage 2, the Gemini surface.** `functionCall` and `functionResponse` left the refused-parts list
and `tools` left the refused-fields list. Five existing tests failed, all of them asserting the
_old decision_ rather than a property — they moved with it, and the docstrings say why. Google
sends no call id and the other two dialects require one, so an id is generated where absent.

**Stage 3, the OpenAI dialect** — the path to Ollama. Declarations become `tools`, a tool result
becomes a message of its own with `role: "tool"`, arguments travel as a JSON _string_. The trap the
FRD named before anything was built is real and is now handled: **a streamed tool call arrives in
pieces**, name once and arguments as fragments across deltas, so `StreamedToolCalls` accumulates by
index and emits whole calls on the chunk that ends the message. Unparseable arguments keep the name
rather than failing the request — a model's mistake should not be hidden behind ours.

**Stage 4, governance.** `tools_enabled` per use case, **default off** (migration `0020`,
`server_default false`), read only when a request actually declares tools so an ordinary request
pays nothing. A `tools` capability in the catalog, checked **per hop**, so a fallback skips an
incapable candidate instead of answering in prose to a client that will parse it as a function
call. The mock upstream answers a tool request with a call, because otherwise the feature would
only ever be exercised against a model nobody has in CI — the state `FRD-110` refused to leave
attachments in.

**mypy caught what no test could.** Three adapters iterated `message.parts` and treated "not
`TextPart`" as "an attachment". Widening the union made that untrue, and a tool part reaching those
loops would have been an `AttributeError` at runtime, on the Gemini and Anthropic upstreams, in
production. Now each checks `isinstance(part, DataPart)` explicitly and raises `DialectUnsupported`
otherwise. That exception then had to **move out of the OpenAI dialect** into `upstreams/base.py`:
two other adapters needed it, and importing it from a sibling dialect is precisely the import the
architecture assertion caught once before with `to_json_schema`. A thing every dialect needs was
never one dialect's to own.

**And a measurement corrected a rule I had asserted.** `toolConfig` was refused outright, on the
argument that its modes "hold on one vendor and silently do not on another". Then OpenCode was
pointed at the gateway and sent `{"functionCallingConfig": {"mode": "AUTO"}}` on **every** request —
and `AUTO` _is_ the default: it asks for exactly what happens when nothing is sent. The blanket
refusal blocked the whole use case in the name of a fidelity problem that mode does not have. Now
`AUTO` is carried and `ANY`/`NONE` are refused **by name, because they are not built** — which is
an honest reason, unlike a claim about vendors nobody had measured. Same shape as the `tools`
refusal itself: a real capability question answered from the armchair.

**Proven against the running stack.** A real request with a real model:

```
POST /v1beta/models/qwen3:4b:generateContent  {"tools": [{"functionDeclarations": [read_file]}]}
→ {"functionCall": {"name": "read_file", "args": {"path": "hello.py"}}}, TOOL_USE, 487 tokens
```

and with OpenCode's own three-tool shape, `list{path: "."}`. Priced, budgeted and on the audit
trail like everything else.

**Not yet done, and stated rather than implied:** the full OpenCode loop still ends in a
`ReadTimeout` — a 4B model on CPU takes **86 seconds** for a three-tool request and OpenCode sends
ten plus a long system prompt. That is model speed, not a gateway defect: the same request answered
correctly when given the time. Tool calls on the **Vertex** dialects (Gemini upstream, Anthropic)
are not built either; both refuse a tool part by name, and the catalog capability keeps such a model
out of a tool request in the first place.

## 2026-08-08 (night) — `FRD-131` FR-7: the audit row learns what the model asked for

Stages 1–4 were called done, and a live OpenCode run said otherwise. The audit row of a real
assistant turn read `{"text": ""}` and nothing else.

**The cause is worth keeping.** The streaming path builds its stored response by accumulating
`text_delta`. A tool call has **no text delta** — the answer _is_ the call — so the row was
literally correct and completely useless: tokens and cost recorded, and no trace of what the model
asked to have run. The buffered path stored it in full, because the whole response object went into
the payload. One feature, two exits, one of them blind: `FRD-126`'s lesson arriving through a door
it had not been pointed at.

For a coding assistant this is not a detail. Every such client streams, so _every_ tool call in
real traffic was unrecorded — and "which functions did the model ask to run" is exactly the
question `ADR-0013` promises the gateway can answer. A platform whose point is auditable model
access had the least auditable part be the one that matters most.

**Closed as one fact on the trail, not two at the exits.** `AuditTrail` gains `tools_declared` and
`tool_calls`; `Accounting.served()` records the names, so both exits get it by calling the same
method rather than by each remembering; `tool_summary()` is an **allow-list** in the shape
`FRD-122` established — names and counts only. Arguments stay out: they are caller content and
belong under `store_payloads`, inside the retention clock and behind `FRD-406`'s redaction, not in
a metadata column no clock covers. Migration `0021`.

`declared` is recorded beside `called` because **"offered ten functions and asked for none" and
"offered none" are different events**, and only one of them is a model behaving oddly. A request
that declares nothing stores NULL — a column that is never NULL stops being evidence of anything.

**And the client was not receiving them either.** The chunk mapper carried only `text_delta`, so a
streamed tool call reached nobody: the audit was blind _and_ the answer was lost. Both fixed
together, which is the honest framing — recording something is not the same as delivering it.

The mock now emits a tool call on the final chunk, exactly as a real dialect does. Without that the
streamed path had no hermetic coverage at all, which is how the gap survived stages 1–4 and had to
be found against a running model.

Verified live, same command as before:

```
streamGenerateContent  545   NULL                                  ← the client's own title call
streamGenerateContent  2099  {"declared": 10, "called": ["read"]}
streamGenerateContent  2115  {"declared": 10, "called": []}
```

**Model selection, measured.** `qwen3:4b` calls tools correctly and spends 352 completion tokens
and 86 seconds doing it, almost all of it discarded reasoning. `qwen2.5-coder:7b` is fast and
**cannot call tools at all** — it returns the JSON as prose with `tool_calls: null`, while
`ollama show` lists `tools` as a capability. `qwen2.5:3b` calls correctly in **2 seconds and 21
tokens**, a factor of 43 against qwen3:4b with better arguments. `qwen2.5:0.5b` called correctly
once and was then used to argue that the family can do it at any size; asked again it answered in
prose, then called with invented arguments, once naming a parameter that is not in the schema.

Which produced the rule the seed now states: **a vendor's capability flag is a claim, not
evidence, and one successful call is not a capability.** `TOOLS_BY_MODEL` holds what was _seen_, is
appended to only after a run, and a model absent from it declares no tool calling — so the dispatch
chain refuses it by name rather than letting prose reach a client that will parse it as a call.
That entry was written for `qwen2.5:7b` while it was still downloading and taken out again before
the file was saved: the fourth instance in one evening of the same reflex.

## 2026-08-08 (night, later) — tool calling on all three dialects, and a matrix instead of anecdotes

**Gemini and Anthropic now carry tool calls too.** Both had raised `DialectUnsupported` since the
part union widened; the catalog capability kept a tool request away from them, so nothing was
broken — the feature simply only existed on one of three wire formats.

Two dialect facts worth keeping. **Google sends no call id** and matches a result to a call by
_name_, so an id is generated deterministically — otherwise a conversation begun there could not be
continued on the two dialects that require one. And **`functionResponse.response` is an object**,
not a string, so a result is parsed back and a non-JSON one wrapped; the canonical model keeps text
because two of three want it. Google also sends a function call **whole in one chunk**: no
accumulator was written for it, because a mechanism defending against a problem a wire format does
not have is a mechanism nobody will maintain correctly.

**Anthropic is where the collision the FRD predicted actually lives.** `input_json_delta` means two
different things on that dialect and only `content_block_start` says which: for a structured
request the fragments **are** the answer and stream as text (`FRD-112` depends on it), for one of
the caller's tools the identical fragments are arguments and must be accumulated — streaming them
as text would send `{"pa`, `th": "he` to the client as the model's reply. And `aira_structured_
output` is itself a `tool_use` block, filtered out of the reported calls: returning it would hand
the caller a function they never declared.

The **tools-plus-schema conflict is a dispatch decision, not a mapping error**: structured output
on that dialect _is_ a forced tool call, so one field would have to serve two purposes and one of
them would silently lose. `ToolsAndSchemaTogether` skips the candidate by name, exactly as
`SamplingExpressible` does for `top_k`, and each adapter declares `tools_with_schema` — absent
means "cannot".

### The matrix

Asked whether the edge cases were covered, the honest answer was **no, not systematically** —
individual cases existed, a matrix did not. `test_tool_calling_matrix.py` is organised by _where in
the path_ × _what is wrong with it_: the declaration, the replayed turn, the model's answer, the
stream, governance, and the audit row seen as evidence. Writing it found three things the code had
never decided:

- **a function name nothing can call** (empty, or with spaces and dots) was accepted and would have
  been rejected downstream with a message naming neither the tool nor the field;
- **the same name declared twice** was accepted — and a call to it cannot be matched to one
  function, so the caller would run whichever their code found first;
- an **empty `tools: []`** must stay identical to sending none, or a client that always includes
  the field is refused by the use-case gate for asking nothing.

The first two are now refused at the surface, where parsing belongs. One deliberate **non**-decision
is recorded too: a tool result answering no call in the history is carried, not policed —
`ADR-0013` says the gateway governs model access, not the caller's conversation.

The matrix also caught a sloppy assertion of my own: `"functionCall" not in response.text` passed
for the wrong reason, because `Part` serialises all four shapes and the string appears as a null
field. Asserted on the parsed answer now.

### And a caching setting made a test suite fail

`make ci` went red on **twelve frontend tests**, all timing out at five seconds. Nothing to do with
the code: `uptime` said load average **103** and 118 MB free. `OLLAMA_KEEP_ALIVE=30m`, which I set
an hour earlier so an agent loop would not reload its model between turns, had pinned **two models
(7.8 GB)** in a 15 GB box for half an hour, with `NUM_PARALLEL=2` making it two rather than one.

Lowered to Ollama's own default of 5 minutes, and the models unloaded: 9 GB free, 502 frontend
tests green. The lesson is the coupling itself — a _caching_ setting starved a _test suite_, and
nobody would look there. A knob that trades memory for latency needs a number that fits the machine
it runs on, not the workload that motivated it.

## 2026-08-08 (night, last) — the console side of it: who may ask what, and a config that runs

Three things the owner asked for before going to sleep: collect as much about AI usage as
possible, show each role only what it may see (with a _"only my own requests"_ toggle even for
those who see everything), let IT Security find a compromised client or system as fast as
possible, and put a button on API-key issuance that produces an OpenCode configuration. Tool calls
first, because that is where an assistant's behaviour actually shows.

### The reporting screen IT Security could not read

`visible_scope` — the one function `FRD-601` deliberately put the visibility rule in — asked
`principal.is_governance`. Governance and oversight differ by exactly one role: **IT Security**.
So the role whose job is investigating an incident got the "you are in no use case" branch: an
**empty** report and an **empty** trace list, on the screen built for it.

It survived because every test asserting "an oversight role sees everything" used a _global admin_,
which satisfies both predicates. It was found while writing a test for something else entirely,
which is the third time this month that a defect has come out of a test aimed elsewhere. `N40`
mutates the predicate back and both suites now catch it.

### What a trace has to carry before it is evidence

`tool_calls` joins the row (`FRD-131` FR-7), and the list learned the filters an investigation
actually opens with: **which system** (the API key's prefix), **whose identity**, **which
machine**, plus _only my own requests_ and _only the turns where the model asked for a function_.

Two rules decided the shape:

- **`source_ip` is a different kind of fact.** It identifies a machine, not a use case, so it is in
  `INCIDENT_FIELDS` and reaches only a role that may act on an incident. Asking to filter by it
  without one is **refused with a 403, not ignored** — a filter that silently does nothing lets
  somebody conclude an address made no requests, which is the opposite of what the screen told
  them.
- **A filter must not widen the scope.** Every one of them is applied _after_ `visible_scope`, so
  "only my own requests" narrows and can never reveal.

The console offers the address field on the same condition, and the predicate is now **one
definition** (`core/auth/roles.ts`) rather than a role list retyped in the screen that needs it —
`it-security`/`global-admin` had been written by hand in the security console, which is precisely
the shape of the 2026-08-07 finding where `it-steuerung` could stop traffic in one plane and not
the other.

### A configuration built at the only moment it can be

The OpenCode config is generated **at issuance**, because the plaintext key exists for exactly
that moment. Offered on any later screen it could only carry a placeholder — and a placeholder is
what somebody pastes and then debugs for twenty minutes. It names only models whose catalog entry
**declares** `tools`: `FRD-114`'s rule at the console, where undeclared means unsupported. An
assistant pointed at a model that answers in prose is the failure `FRD-131` exists to prevent.

### The defect the tests found

`loadMore` rebuilt its query by hand, so page two was fetched **without** the filters page one was
fetched under — the reader turned the page and silently got back the rows they had just excluded.
One list, two questions, no error anywhere. Both call sites now read a single `query()`; shown to
fail against the old code before the fix went in.

That is `FRD-126`, `FRD-206`, `FRD-602` and the KIRA membership bypass in one sentence again: **a
rule restated by hand at a second call site is a rule that will disagree with itself.** The fix is
never "remember both" — it is one function with two callers.

Frontend: 524 tests, branch coverage back above its gate by adding tests, not by moving it.

### The edge round, and an error shape nobody could read

31 live cases over the new surface, each asserting the three things `test_edge_cases.py` asserts:
never a 500, a status the caller can act on, and a message that names the problem. Two shapes were
worth walking twice — every combination of filters (contradictory, empty, absurd) must **narrow**,
and a tool call's _arguments_ must never reach the metadata column, because a file path or a
customer number is content and this list is readable by every oversight role.

One finding, from `limit=100000`: FastAPI answered **422** with its own `{"detail": [...]}` list.
Every other error this API produces is `{"error": {code, message, status}}`, so a Google client
handed the framework's shape reports "unknown error" and the caller never learns that `limit` has a
maximum of 200. That is the _same_ finding as the routing handler added on 2026-08-06 — a wrong
URL answered in the framework's shape — one layer in, and it had been there since the first typed
query parameter.

Now **400 `INVALID_ARGUMENT`, naming the parameter**. 400 rather than 422 because the caller's job
is to fix the request, which is what the rest of the surface says with that number. The KIRA
surface keeps its own `422` + `code`/`details` envelope — its routes parse their own bodies, and a
client migrating by changing a URL must keep receiving the errors it already handles. The handler
picks the envelope by path, like the routing one; the KIRA branch is **unreachable through the
published surface today**, which is stated in the test rather than left for somebody to discover,
because the next KIRA route with a typed parameter would otherwise answer in Gemini's envelope
silently.

### An id that identified two models

The same round put a **500** on the predecessor's surface: `MultipleResultsFound`, because two
catalog rows claimed numeric id `9001`. `tools/seed_local_catalog.py` writes a **fixed** id for
"the local chat model", and it had been run for a second one — so both kept it, and every KIRA
request naming that id failed. Silently created: the seed printed success, and the gateway's
read-model has no unique constraint (Management does, but this script writes past it).

Fixed at all three places it could have been stopped. The **resolver refuses** — this is
`ADR-0011`'s ambiguous routing table one level down, and picking a row would answer, bill and audit
under a model the caller never named, with nothing in the response looking wrong; `503`, because
the installation is misconfigured and an administrator can fix it, with the two model names in the
log rather than in the answer. The **seed releases the id** before taking it, since the number names
a _role_ and re-running for a different model must move it — fixed rather than derived, because a
caller's configuration holds that number and changing it would break them silently. And the live
catalog was cleaned.

Two integration rows were **retired rather than deleted**: `tools` sat on the "fields this gateway
does not serve" lists until `FRD-131` served it this morning. The requirement did not go away, it
moved — a use case without the toggle refuses a declaration by name — and a row deleted without a
comment reads as a requirement somebody dropped.

## 2026-08-09 — The requests view, read by somebody who did not build it

A walkthrough of yesterday's screen produced eight findings across two rounds. They were one
complaint in different shapes: **the view assumed the reader already knew the answer.**

The sharpest one was also the most embarrassing. The source address was added as a **filter** and
not as a **column** — so an investigator could search for an address the screen never showed them.
A filter narrows a lead; it cannot produce one, and I had built only the narrowing half.

### Three defects, none visible to a hermetic test

**A 200 rendered in red.** `outcome` arrived with `FRD-122`, so every row written before it is
NULL, and the badge fell through to its danger branch printing the status. A status column that
calls a success a problem is the one thing it must never do.

**The control that opens a request was off screen.** It was the last column, and the table scrolls
sideways now that it carries a use case and an address. Reported as _"the button was hidden behind
the scroll, I did not even know it was there"_ — which is the accurate description of an action
that does not exist. First column now, and the whole row opens.

**Three info hints that said nothing.** `InfoHint` takes its explanation as projected content; I
wrote `text="…"`, which is not an input, and Angular ignores an unknown attribute on a component
silently. The panels opened empty — _precisely_ the defect the component was built to prevent,
since `FRD-206` shipped info buttons as `title` attributes that displayed nothing and this
component was the fix.

### The guard against it was itself inert

Worth more than the fix. The first version was an Angular spec using `import.meta.glob` to read
every template, and it **did not work**: those specs run in a browser, the glob is unavailable at
runtime, and the file failed to _load_ — Vitest reported "0 tests" for it while the run's total
stayed green, and my grep for the pass count showed 535 and told me nothing.

Found by breaking a template on purpose and watching nothing happen. It lives in the Python suite
now, which has a filesystem, and it was shown to fire.

**A guard that cannot fail is the thing it guards against, one level up.** Third time in this
repository that a new test had to be broken before it could be believed — and the first time the
test in question _was_ the safety net.

### And the deferral that could never be discharged as written

`ADR-0009` deferred per-request browsing until `FRD-406` made it safe. `FRD-406` then shipped its
credential half and **declined its PII half on purpose**, because names and customer numbers are
what a payload is stored _for_ and a redactor that mangles them ends with storage switched off.

So the redactor was never going to discharge that deferral: the sensitive content and the useful
content are the same content. `ADR-0016` grants the view on a different condition — a named set of
roles, and **every read writes a record** naming who read what, when and on what authority, written
_before_ the content is returned. The boundary is still crossed. It is now crossed visibly.

IT Steuerung reads none of it: every figure about every use case, no content. Visibility and
content are different answers, which is the same split `FRD-206` had to make between seeing a use
case and administering one.

### Smaller, and still real

- The **summary panel** built for "I have to know the key first" was **removed the same day**: it
  answered the question and pushed the requests below the fold, and the first reader asked where
  their traces had gone. A discovery aid that hides the thing being discovered is a net loss.
- **"Show me the prompts that threw a warning"** is a filter now, backed by a `flagged` column
  derived in `record_request` from the argument all three call sites already pass. Not a query over
  the JSON decisions: containment is written differently on SQLite and Postgres, and the hermetic
  suite runs on one while production runs on the other.
- A migration id of 42 characters **applied its DDL and then failed writing its own version row**,
  because `alembic_version.version_num` is a `varchar(32)`. The same shape as the Keycloak client
  description that broke a realm import at `varchar(255)`: a length only a real database enforces.
- The **phone layout test** caught a ten-pixel overflow the day a checkbox gained a sentence-length
  label — `.checkline` was `white-space: nowrap`, which is right for "Refusals only" and wrong for a
  sentence.
- The e2e test for reading a prompt **issues its own key and sends its own request**, because the
  first version opened whichever row was on top and failed against rows another suite had left
  there, dated 2031 with no payload. A test that depends on ambient data is flaky by construction —
  and flaky in a way that looks like a product defect.
- `clearCookies()` does not end a Keycloak SSO session. Walked into twice in one day; written into
  the test rather than remembered.

## 2026-08-09 (later) — Four columns, and a search box that survives its own query

A second walkthrough of the same screen, and the more interesting of the two findings is the one
about a text field.

### Typing two characters threw the reader out of the field

> _"in Suchfeldern wenn ich 2 character reinschreibe, dann fängt er an zu suchen und ich fliege aus
> dem Feld raus und muss es nochmal anclicken"_

The use-case list had its search input inside the `@else` of `@if (loading())`. So the first
keystroke that reached the debounce started a query, the query set `loading`, Angular tore down the
`@else` — taking the input with it — and built a fresh one when the answer arrived. Focus gone,
mid-word.

**A control that starts a request must survive that request.** The box now sits outside the branch,
and "busy" is a word beside it rather than a screen that replaces it.

### The guard missed the case it was written for

The shape is the defect, not the occurrence, so the guard scans every template for a search input
inside a block its own query toggles. Its first version found nothing — because `@else` carries no
condition, so `} @else {` reads as innocent no matter what it is the alternative _to_. Teaching it
to inherit the `@if` it belongs to immediately turned up a second instance in the model catalog,
which does not misbehave today only because that search is client-side.

Second time in two days that a new guard had to be broken before it could be believed, and both
times it was silently wrong in the same direction: **passing**.

The e2e test was then shown to fail against the restored bug. Only a browser can see it — a
component test types into a field and asserts a request went out, with no notion of where the caret
is.

### Eleven columns down to four

When, from where, what, how it ended. That is what somebody scans a list _by_; model, tokens, cost,
latency, trace id, tools, credential and use case are details _about_ a request, and they now belong
to the request that was opened. The table had grown to eleven columns and scrolled sideways, which
is precisely how the control that opens a row ended up off screen yesterday — so the fix for that
and the fix for this are the same fix, and an assertion on the scroller's own width keeps it.

Dates are `dd.MM.yyyy`: this console is read in Europe, and `9/8/26` means two different days
depending on who is reading it. A request a pipeline step objected to is marked **red on the row**
rather than with a badge in a column nobody scans for.

Sixteen unit tests moved from asserting the list to asserting the opened row — the same statements,
one indirection further in. The harness gained a router, because the detail links to its use case
and a harness without one tests a different component.

## 2026-08-09 (evening) — The catalog, the rule editor, and an audit of the audit

### "How do I know a model is reachable if I have no key?"

The sharpest question of the round, and the honest answer was that nothing could tell you. A
catalog entry is a **declaration**: it needs no credential and proves nothing. Without a key no
adapter is registered, so the model sits in the catalog looking perfectly healthy while every
request for it comes back `model_not_found` — which a caller reads as a typo, not as a missing
credential.

`GET /v1beta/models/{model}:check` now answers **three separate facts**: declared, served,
reachable. `reachable: null` means nothing was contacted, which `FRD-117` already established is not
the same as failing. Never a generation — a self-deployed model can be scaled to zero, and a
"does this work" button must not be the thing that wakes it, bills for it and takes minutes to say
so. The upstream's error _text_ is never repeated back: a provider's message can carry the URL it
was called with, and that URL can carry the key.

Verified against the live registry, which is the only place it means anything: the local model
answers, and a Vertex model this stack has no credential for reports **"declared, but nothing
serves it"** instead of looking fine.

### Declaring a model was at the bottom of the page

Somebody who came to add a model had to scroll past the entire catalog to find out how. The thing a
screen is _for_ goes where a reader starts. A row now opens to **everything on file** — built as a
list in the component so it is exhaustive by construction, with a test that populates every field
and requires each on screen. A catalog entry is what the gateway _enforces_; a partial answer to
"what does this row actually say" is worse than none.

### An audit of the audit

Asked whether all the test combinations actually occur, so it was measured rather than asserted:
each branch of `payloads.py` was broken in turn and the parametrised rows that noticed were
recorded. Three findings.

**`is_oversight` was undefended.** Removing it makes an oversight role fall through to
`OUT_OF_SCOPE`, which is _also_ a 403 — so a matrix checking only the status passed with the role
boundary gone. The distinction is the entire point of the message: "you see figures, not content"
and "that use case is not yours" send the reader to two different people. The matrix asserts the
sentence now.

**Half an audit reports half a matrix as pointless.** Deleting a branch can only make code _more_
permissive, so a case guarding against over-restriction can never go red for a deletion. Running the
inverse mutations — refuse too much — showed that four rows exist precisely to defend against that,
and one of them is the only thing standing between a colleague's request being readable and not.

**One row was defended a layer up**: `outsider` is caught by the route's own scope guard, which no
module-level break could reach. A mutation for it now exists.

Nine of these became permanent (`N46`–`N54`); the harness stands at 316 properties.

### And the rule editor

Its buttons sat in the same wrapping `form-inline` as its fields, so "Create rule" flowed in beside
"smallest sample" and read as one more setting. Fields and actions are two things now, separated by
a rule, with room to aim — a dozen controls at 0.6rem is a wall, and clicking the wrong one of two
adjacent checkboxes is a governance mistake rather than a typo.

## 2026-08-09 (night) — The button nobody could reach, and a seed that lied by one

### A capability with no way in

`FRD-500` says a global rule is IT Security's to author, and the server has accepted one since the
day it was written. The console never offered the button — so every global rule that existed
anywhere had been written into the database by a seed, and the question came back exactly as one
would expect: _"wie mache ich es über die Oberfläche?"_

This is `FRD-206`'s defect **inverted**. That one was a control that refuses when used; this is a
capability nobody could reach. Both are a console disagreeing with its server, and only the first
one announces itself.

### Three lists that only grow

Rules, what is stopped now, and what was stopped before — the last of which is _kept_ on purpose,
because "blocked for two hours last Tuesday" is what a review asks. All three are paged and
searchable now, and one box covers both suspension lists: "has this caller ever been stopped?" is
answered by the live list _together with_ the record, and a search over only the first would answer
it wrongly while looking like it had answered it.

Paging then broke two e2e tests, and correctly: they created a rule and looked for it on screen,
and with a few hundred rules from earlier runs a fresh one is well off the first page. They search
for it now — which is what a person would do, and what the tests should have been doing all along.

### The seed lied by one

Four rules were seeded and three appeared. The fourth named a use case this seed does not create,
and the loop's `continue` dropped it silently: the count looked plausible and the one that went
missing was the only rule that _acts_ rather than alerts.

Found by running the seed rather than reading it. It raises now — the **third** instance in this
repository of "returns silently for something unknown", after `record_to_outbox` and the missing
Kafka topics.

### And the design question that was asked directly

> _"Warum machen wir check reachability nicht im Window, und wenn reachability false ist, dann kein
> Anlegen?"_

In the window: yes, done. Blocking: **no**, and it is worth writing down why. Declaring a model
before its credential exists is the ordinary order of work — you write the catalog, then configure
the platform — and an adapter is registered only once the credential is there. A hard gate would
make a fresh installation undeclarable, and would make it impossible to prepare a catalog for a
platform whose key arrives next week. `FRD-114` already settled the shape of this: deprecation
warns, revocation blocks. A reachability verdict is information, and it is shown as such.

## 2026-08-09 (late) — A button that was invisible when it mattered, and tabs that stop hiding

### "Test connection is still not in the window"

It was — inside `@if (name())`. Opening "Add model" starts with an empty name, so there was **no
button at all**, and the feature read as missing because from where the reader stood it _was_
missing. A control that appears only after you have done something else is a control nobody finds.

The follow-up was sharper: _"und ich kann ein Modell ohne Testen anlegen"_. Yesterday I argued
against blocking on a failed verdict and that argument still holds — declaring a model before its
credential arrives is the ordinary order of work, and an adapter exists only once the credential
does, so refusing on `served: false` would make a fresh installation undeclarable.

But that was an answer to a different question. **Refusing the verdict and refusing the ignorance
are not the same refusal.** Save now needs a check to have been _answered_ for the name in the
form — whatever it answered. It rules out the one outcome a single button can rule out: nobody adds
a model without having found out. An erroring check counts as looked-at, because a diagnostic that
cannot answer must not become a gate.

Five unit tests and one e2e test started failing immediately, all of them creating a model without
checking. That is the gate working, and updating them is the cheapest possible proof of it.

### Tabs that stop hiding themselves

Below 60rem the strip is a vertical list. Scrolling was the old answer and it is the wrong one for
_navigation_: a tab that has scrolled out of view is a section the reader does not know exists, and
the use-case detail has seven of them. The breakpoint is 60rem rather than a phone width because the
pain starts on a laptop half-window, not at 360px.

CSS only — no template changed, so nothing that clicks a tab by role or text had to move.

### And the mistake worth writing down

Proving the layout test could fail, I cut the media query out of the stylesheet by searching for an
end marker that appears _earlier_ in the file than the block. The slice silently **duplicated**
content instead of removing it, the rule stayed in effect, and the test stayed green — which I
briefly read as "the test does not work".

Checking what the file actually contained took ten seconds and turned a wrong conclusion into a
right one. The lesson is the ordinary one, and it keeps arriving in new clothes: **when an
experiment says something surprising, verify the experiment before believing the result.**

## 2026-08-09 (night) — Only catalogued models, and what that cost to find out

> _"Es dürfen nur die Modelle verwendet werden, die im Katalog stehen und explizit von einem
> globalen Admin angelegt wurden."_

The morning's version approved _declared_ models and left a model with **no catalog row** alone, on
`FRD-114` FR-7's reasoning that an undeclared model gets the baseline. That was the wrong side of
the line, and for a reason better than tidiness: the rule could be defeated by **deleting** a
declaration. Approval was removable by removing the thing that carried it.

So the baseline for a model nobody catalogued is now nothing, and `FRD-114` FR-7 says so — "absence
of information is not permission" now extends from _what a model may do_ to _whether it may be used
at all_.

Two refusals, deliberately not one: _"not in the model catalog"_ needs somebody to **add** the
model, _"has not been approved"_ needs somebody to **release** it. A single message would send the
reader to the wrong person.

### 58 tests, and the right way to read them

Turning the rule on failed 58 hermetic tests. Not one was a defect — every one used an invented
model with no catalog row, which is what a test does.

The tempting fix was to catalogue invented models in fifty test files. That would have taught the
suite to lie about the policy. The honest one was to notice what those objects **are**: test
doubles. `MockProvider` and the stub upstreams now say so, and `ModelApproved` does not govern a
double, because its answers are deterministic fiction and approving fiction is theatre.

### And the hole that fell out of it

The exemption needed a boundary stronger than a flag, and looking for one surfaced something worse
than the thing being fixed: **the mock was registered in every environment**. A fake model could
serve production traffic, billed as free — which is worse than an ungoverned real model, because at
least the real one answers.

It is now registered only in `local` or demo mode. `FRD-307` did not create that hole; it made it
impossible to keep not noticing.

Verified live, twice: removing `qwen3:0.6b` from the catalog produced _"is not in the model
catalog"_, and un-approving it produced _"has not been approved for use"_ — each naming the model
and the action.

## 2026-08-09 (night) — "Are they really leftover data?"

Asked to check an intermittent failure rather than accept my own guess about it, and the guess was
wrong in an instructive way. I had said "looks like leftover rows, but I have not proved it". It
**is** leftover data — and not the kind I meant.

### What actually happened

`test_f8` asserts that an oversight role can see a finding for its use case. It read the
**unfiltered** findings list and looked for its own row in it. The endpoint returns the newest 50,
ties broken by `id` — which is a random UUID.

Measured on the live database: **one evaluator tick wrote 57 findings in the same instant**, and
the newest-50 page consisted entirely of rows from that one timestamp. So whether this test's
finding landed on page one was decided by a random UUID. It passed alone, passed in its own file,
and failed in a full run — which is exactly what "decided by chance, weighted by load" looks like.

### Why one tick wrote 57 findings

Not because of many use cases. Because of **56 leftover global rules**, all named `e2e editable …`
or `e2e readable …`. Every e2e run created a global anomaly rule through the API and never removed
it, and **every tick evaluates every rule** — so each leftover produced a finding on every tick,
forever. 61 global rules on this installation, 5 of them real. 8054 of 8274 stored findings were
their output: **97 % of the security console's content was test residue.**

After deleting them, a tick writes **1** finding.

### Two fixes, and the second is the one that generalises

The test was also genuinely wrong: it means to assert _scope_, so it now asks about its own use
case instead of fishing in a global list. Deterministic, and it tests the thing it is named after.

And the e2e tests **delete the rules they create**. That is the rule worth stating: a test may leave
rows behind — they are noise. A test that leaves a **policy** behind has changed the system's
behaviour, permanently and invisibly, and this one had been doing it for weeks.

Deleting a rule asks for confirmation, and Playwright auto-_dismisses_ dialogs — so the first
version of the cleanup clicked and did nothing, which is how the leftovers would have kept
accumulating even after "adding cleanup".

### One more thing it exposed

The same round found two console tests looking for a freshly created row on page one of a paged
list — the catalog and the rules. Both search now. Paging turns "it is on the screen" into "it is
findable", and every test that was written before paging landed makes the old assumption.


## 2026-08-11 — Windows, a scope per head, and a currency nobody could name

A walkthrough of the settings screens by their owner, six reports. Four are the same complaint in
four places — **a control that does not say what it does** — and one is a scope the vocabulary
never had.

### The creation forms became windows

Budgets, rate limits and anomaly rules each unfolded a form under their list. `FRD-206` recorded
what that costs with the model editor: the page scrolls to a control far from the row it is about,
the list behind stays clickable, and nothing on screen says what is being edited. All three open a
window now, and the window is **one shared control** (`core/ui/modal.ts`) rather than the fifth
hand-rolled dialog — at which point the Escape handler, the focus move and the backdrop exist in
five places and differ in four. It owns three promises, each the one a hand-rolled dialog forgets:
Escape closes, the keyboard moves in, the backdrop closes. Tested where it lives, not through the
five screens that open one.

### "In welcher Währung ist das?"

The spend limit was a number with no unit. Every provider on this gateway prices in dollars and the
catalog's own figures are dollars per million tokens, so a budget in anything else would be a
conversion nobody performed — and one a reader would assume had been. Every monetary label now says
so: `Spend limit (USD)`, `Input $ / 1M`, `Spend ($)` on the consumption bars, the reporting cards
and the export. The figures did not change; what they are finally does.

### Burst, explained by somebody who had to look it up

Reported by the owner about a field he had configured himself: *"sogar mir ist nicht ganz klar was
damit gemeint ist"*. A control whose own author cannot say what it does is one that gets set by
imitation. Burst is the **size of the bucket**, not a second rate — it decides how spiky a minute
may be, not how much a caller may send in one. The hint says that, and says the part people get
wrong: raising it does not raise the sustained rate.

### A budget per **head**, which is the one people actually want

`use_case` is a shared pot — the first caller to arrive can spend all of it. `member` bounds one
named person, needs a row each, and goes stale as people join and leave. Neither is *a fair share
per person*, which is what an administrator asks for first. `each_member` is one configured row and
one counter per caller, applying to whoever turns up.

`aira_gateway/scopes.py` exists precisely so a third scope is one branch and both consumers follow
— and the interesting half is that **only one of them did**. The rate limiter needed no change:
`_applicable` resolves each row against the caller on every request. The budget service was keyed
off the row itself, long after the caller had gone — `_scope_key(budget)` passed `budget.subject`
as its own caller, which is exactly right for the two scopes that name somebody and resolves to
*nothing* for the one that does not. Threaded through now, with the subject carried on the
`Reservation` beside `period_keys` for the same reason: settle and release run long after the
request's identity was resolved. **The same rule, two implementations, one of them wrong** — which
is the shape this repository keeps finding, and the reason the shared module was written in the
first place. `S8`/`S9`, both shown to fail first.

One consequence had to be answered rather than rendered: **a per-person budget has no single
consumption figure**. `GET /v1beta/usage/{use_case}` reports the *reader's own* number, says whose
it is (`measured_for`), and answers `null` — never zero — to a reader the row does not bind, such
as an oversight role who is a member of nothing. Zero is also what an untouched allowance looks
like, so this is the one place `FRD-603`'s rule (unknown is never rendered as zero) is not merely
tidy. The console draws no bar there and says why.

The route is asserted separately from the service, which is the `FRD-124` lesson: the service can
resolve the caller perfectly and still be **asked for nobody**, and the answer to that looks
exactly like a fresh allowance. The stand-in in `test_budget_routes.py` caught it by being
*stricter* than the real service rather than more permissive — the trap `CLAUDE.md` names, for
once falling the useful way.

### Released models first

In **Models & prices** the approved models now sort to the top. With fifty imported drafts from one
key (`FRD-507`), the entries somebody actually released were scattered through a list ordered by
nothing in particular.

### And the staircase

In a use case's Members tab, "Grant access to" and "As" sat on two lines. `expectFormControlsAligned`
did not see it because it only looked inside `form.form-inline`, and this row is a `.filter-row`.
Widening the guard found the real defect and then a second one it had also been blind to; a 43 px
offset was injected to watch it fail first, because a guard that has never been broken is one that
passes for reasons nobody has checked — **the third time in a week** that a new guard had to be
proved sharp before it could be believed, and each time it was silently wrong in the same
direction: passing.

### Nine stale anchors, and two graves

The harness reported nine `STALE` after the week's changes — an anchor pointing at code that has
moved, which protects nothing while reading exactly like a property that is defended. Seven were
re-anchored (`B11`, `P5`, `M15`, `V5`, `Z4`, `Z15`, `J16`) and shown to be caught again. **Two were
removed**: `P3` guarded `allow_check`, which `FRD-308` replaced with the per-use-case release —
defended at every hop of the dispatch chain by `J1`–`J4` — and `R23` guarded a rule about the two
use-case roles `ADR-0017` abolished. A mutation defending deleted code is worse than none: it
reports green about nothing, and the count it inflates is the number people quote.

### A test written against a moving target

The full browser round then failed one case that had passed an hour earlier without its own code
changing: the prompt-caching hint hovered its "i" **immediately after the navigation resolved**.
The overview finishes assembling afterwards — the consumption card arrives from the gateway and
pushes the tiles down — so the pointer was left over whatever moved into that spot and the panel
never opened. The two hint tests beside it settle the page by interacting with it first, which is
why they never saw it. Fixed by waiting for the trigger, and worth naming as a class: **a hover is
aimed at a coordinate, so a test that hovers before layout has settled is asserting about the
pointer, not about the control.** Nothing in the product was wrong; the test had been passing on
timing since the day it was written.

### And one that counted instead of naming

The same round failed a model-release case that had been green for days: it derived *what one
keypress does* from *how many chips are on screen* (`before === 1 ? 0 : …`). That holds only when
the highlight lands on a model already chosen — and **clicking the search field opens the list**,
so the following ArrowDown *moves* rather than lands. It therefore passed for exactly as long as
the demo use case had released nothing, and started failing the day it had released one. It
searches for a model **by name** now, leaving one option, so the keypress has one possible meaning
whatever the use case already allows — and the case is genuinely state-independent rather than
state-independent-looking. Same family as the hover above: **an assertion aimed at a position is an
assertion about the arrangement, not about the behaviour.**


## 2026-08-11 (later) — a rule about a person, and the name it never matched

Asked whether the per-person scope works when access comes from a **Keycloak group and no users at
all**. It does, and it was measured rather than reasoned: a use case whose entire access is two
group grants, nobody named anywhere, one `each_member` budget — two identities called once each and
the counters came back as

    member:group-probe-2:1361bd47-…  1
    member:group-probe-2:2fc398cc-…  1

Two counters from one row that names nobody. Over groups it is the only one of the three scopes
that can work at all, since `member` needs a row per person and there is no list of people.

### And the question exposed something older

Those keys are **uuids**. The gateway takes an OIDC subject from `sub`, while an API key's subject
is its owner's *username* (`FRD-604`) — two credentials, two alphabets for the same question. A
`member`-scoped rule is written by an administrator **typing a name**, so it bound API-key traffic
and, for the same person over OIDC, bound nothing at all. Measured before touching anything:

    budget: scope=member, subject="service-account-…-member", limit_requests=1
    four calls by that account: [200, 200, 200, 200]

Configured, displayed, inert — `FRD-125`'s badge-wearing absent control, one identity system over,
and it had been that way since member scopes existed.

**The repair is option B of three**, chosen with the owner: a member row matches **either** the
caller's subject or the name they are known by (`preferred_username`). The alternatives were worse
in ways worth recording — making the subject *be* the username would rewrite every future audit row
and move a renamed person's history onto whoever inherits the name, and a directory picker in the
console fixes neither API callers nor a group-granted account that appears in no membership list.

Three properties hold it together:

- **The name is never an identity.** `subject` stays what every row records and every counter is
  keyed on. A username can be reassigned; a subject cannot.
- **The key is the row's own subject**, whichever name matched — so a person is one counter rather
  than two, and every figure already in `budget_usage` keeps being found. That shape is stored, and
  changing it would not lose the rows, it would stop finding them.
- **Matching a name is not matching anyone.** Somebody else's name is somebody else, and an empty
  subject binds nobody — the widening that would have been easy to write by accident.

Same measurement after: **[200, 429, 429, 429]**.

### Where the proof had to live

Neither existing layer could see this defect, and that is the interesting part. A hermetic test
mints no credentials, so it cannot express "two credentials disagree"; the browser suite
authenticates the gateway with an **API key**, which is exactly the half that always worked. Only a
real Keycloak token carries a `sub` that differs from the name somebody would type — so
`tests/integration/test_named_member_rules.py` is where it belongs, with the member account's
username spelled out and its `sub` appearing nowhere in the file.

The route is asserted separately from the rule (`FRD-124`'s lesson): the scope can resolve two
names perfectly and the **route** can still fail to hand the second one over. Proved by cutting
that one line and watching the case go red. `S10`, `S11`; `S1` re-anchored.

## 2026-08-26 — an audit of the tests against the code they defend

**What was asked.** Go through every test and the code it covers, ask whether the test actually
defends the behaviour it names, and look for defects on the way. The final measure was to be the
showcase: does it do what it says, and is the code secure, readable and extensible.

**Method, and what it found that reading alone would not.** Two passes. A *reading* pass over the
request path, the auth chain, budgets, rate limits, the pipeline, the consumer and both planes'
serializers; and a set of *sweeps* — questions about a file rather than about a field, which is
what `LESSONS.md` §1 records as the only kind that sees a rule stated in three places and not
inherited by the fourth. The sweeps found more than the reading did.

**Nineteen findings: seventeen fixed, two reported.** Every fix was observed red before and green
after — the property broken on purpose, the test watched to fail, the fix restored — and each
carries the mutation that reintroduces it. The two that are reported rather than changed are each a
decision about scope rather than a defect to fix quietly; they are at the end.

**The two that matter most, because both were silent.**

*A `pii_filter` rewrite never reached the stored payload when a later step blocked.*
`run_pipeline` assigned `trail.body = _rewritten_body(...)` **after** `engine.run` returned, so a
`PipelineRejected` from any step following the redactor skipped the line — and the refusal's audit
row was written from the caller's original body. The personal data the step exists to remove was
kept, in the one place a retention clock covers, on a request nobody was served. `_rewritten_body`
was correct and its call site was correct; the wire between them existed on one path of two, and
the file's three tests all called the function directly. The engine now takes a caller-owned
`rewrites` list — the shape `decisions` and `model_calls` already use for exactly this reason —
and the trail is rewritten in the `finally`.

*A plaintext Keycloak realm passed the deployment check unless it happened to be listed last.*
`plaintext_problems` took a `dict[str, str]`, and the gateway built it with one
`("AIRA_OIDC_ISSUER", issuer)` pair **per configured realm** (`FRD-118`) under a comment saying
every issuer is checked. A dict keeps the last value per key. Measured: `environment=production`,
two issuers, the plaintext one first — `unsafe_settings` returned an empty list, on the check this
module's own docstring calls *the one misconfiguration that defeats authentication outright*. The
parameter is a sequence of pairs now, so the collapse cannot be written at any call site.

**A grant on a group did not make an administrator.** `GroupGrantResolver.use_cases` answers
`{slug: role}` and has a test asserting the role is carried through; `_with_group_grants` used only
the keys. `payloads.grant_role_in` then re-derived the role from `use_case_members`, where a group
grant writes no row — so the route `FRD-209` FR-6 leads with produced an administrator the gateway
read as `user`, refused their colleagues' prompts in a restricted use case and narrowed their trace
list, while Management (which asks guardian) treated them correctly. Two planes, one question, two
answers. The resolved pairs travel on the `Principal` now. Six tests, and **one of them is a wire
test**: the other five construct a `Principal` themselves and all stayed green when the wire was
cut, which is the whole lesson repeating itself inside its own fix.

**Three defaults that stopped discriminating on a partial update.** `AnomalyRuleSerializer.validate`
read `attrs.get("kind", REFUSAL_RATE)` and `attrs.get("action", ALERT)`, so on a `PATCH` — which the
console's own client method is built on and documents — every check below answered about a rule
nobody has. Three measured consequences: *every* partial edit refused over a `min_sample` the caller
never sent; a partial edit that did carry it **cleared `action_minutes`** on a throttle rule,
leaving an incident control the gateway then refuses to enforce while the console still shows it as
throttling; and a `spend_spike` threshold below 100 accepted, which fires every window forever. The
catalogue serializer had the same shape one field over — its price pair was checked against the
edit, so correcting one price of a fully priced model was refused and *clearing* one was accepted.
Both read the instance now. `rule-form.ts` had been working around the first by always sending the
whole object, and says so in a comment: a workaround in one client is not a property of an endpoint.

**A model reachable only through the catalogue bypassed both dialect checks.** `SamplingExpressible`
and `SchemaExpressible` resolved their adapter with `provider_for(model)` — one argument, which
answers a *configured* model and `None` for one servable because it is catalogued (`FRD-507`
stage B) — and both read that `None` as "this dialect declares no restriction". Nothing else reads
`sampling_controls` or `schema_refusal`. Measured: `topK` on a catalogued model, served **200** by a
dialect that has no `top_k`. The third occurrence of one shape, so the lookup is a named helper
now. Beside it, the Anthropic mapping's backstop raised a bare `ValueError` where the OpenAI
mapping raises `DialectUnsupported` — only the second is in `REFUSALS`, so the one path that
backstop exists for produced `500 Internal error`.

**And the showcase, which is where the audit was supposed to end and did not.** `make showcase`
ran green and the demo had stopped demonstrating: the prompt-injection attempt and the embedding
batch both came back `429 budget_exceeded`, refused by an allowance before the pipeline ran. The
run before it had recorded `400 blocked_by_pipeline` and `200 served`. Both are in `request_logs`,
one above the other.

The cause is a number without a derivation. `_budgets()`'s docstring says the figures are
*"calibrated against what the demo traffic actually costs"*; the per-head daily cap was `0.000100`,
and one run of `demo_traffic.py` costs — measured across eight runs in the audit trail — between
50 600 and 129 400 nanos. The cap sat **inside** the spread of the demo's own traffic, so whether
`make showcase` worked depended on how verbose a 0.6B model felt that morning. It is twice the
observed maximum now, with the derivation written beside it.

The more useful half is the guard. `demo_traffic.py` counted a `429` as `refused` and reported
success; its own comment had named half the hazard — *"or the demo's most important refusal quietly
becomes a served request"* — and guarded only that direction. The mirror case is the one that
fired. `--assert-controls` now fails the run when the injection was not refused **by the filter** or
the batch was not **served**, and names the two explanations. `make showcase` passes it;
`make showcase-traffic` deliberately does not, because reaching a limit is that target's point.
Verified both ways: the fixed showcase records `400 blocked_by_pipeline` and `200 served`, and with
the cap forced to 1 the same script exits 1 where it used to exit 0.

**And the layer nobody had run in a while.** The live-stack suite came back with twenty-two
failures, every one of them on the KIRA surface, and the surface was working: one hand-made call
to the same endpoint with the same key answered `200`. The tests were addressing `model_id: 9001`
and the catalogue holds the chat model under `1004` — `tools/seed_local_catalog.py` moved it there
deliberately (*"every document and every example said `1004`, and the one runnable command said
something else"*), and `conftest.LOCAL_CHAT_MODEL_ID` was introduced in the same round with the
hazard written out: *"Six tests carried `9001` as a literal, and moving the demo … would have left
every one of them addressing a model that no longer answers — reported as a `404` about a number,
which reads as a broken surface rather than as a stale test."* Six were corrected by hand;
twenty-one were not, and the embedding id was typed nine more times beside them. The paragraph
predicted its own consequence and the search-and-replace that followed it stopped two files early.
Every integration test imports the constant now, and a new guard bans a typed id **in that layer
only** — a hermetic test writes its own catalogue row, so the number is local to it and typing it
is not a copy of anything. The first, wider version of the guard reported thirteen files that were
all correct, which is the wolf-crying check `LESSONS.md` §3 names appearing inside a guard written
against a real defect.

**One guard was rewritten rather than added to.** `test_a_callers_value_is_never_a_server_error.py`
promises that *"the next endpoint is covered by it on the day it is added"* and carried three
hand-written lists. Against the served OpenAPI document, seven things were never swept — the whole
`/v1beta/register` endpoint among them. The query and path sweeps derive from `/openapi.json` now;
`BODIES` stays hand-written, because what is *wrong* for a body depends on its vocabulary, and it
gained the comparison in both directions. The gateway itself was clean under the widened sweep,
which is the answer worth having: the code was right and the guard was not.

**Two guards that were missing rather than wrong.** "Which models does this pipeline name" is
implemented once per plane — Management refuses a pipeline that *saves* an unreleased model, the
gateway refuses one a *dry run* would call — and both docstrings end *"the pair is named in each so
neither is edited alone"*. Nothing compared them, which is `LESSONS.md` §1's *a paragraph
explaining why a copy is dangerous is evidence the copy needs a test*. They are compared now in
both of the ways they can drift: what they answer for a document naming a model everywhere one can
be named, and which keys they look at, read from the source — the half that can see a site only one
side has learned. And the **integration layer never asked `/readyz`** before blaming the code:
`test_diagnostics.py` stops the local model on purpose and restarts it in a `finally`, so a run
interrupted between the two leaves every later test failing `502 ConnectError` at a gateway that is
working. That discriminator was written down for the *browser* suite; the layer that actually calls
a model did not have it, and it cost an hour here. It is a `pytest_report_header` now — above the
first test rather than inferable from the fortieth.

**Also fixed:** an unreachable `burst` guard in the rate-limit serializer, under a comment
describing a rule the gateway's own tests contradict; `MAX_MODELS`, a bound the pipeline serializer
claimed and did not have; `EVENT_KINDS`, the third of three anomaly classifications and the only one
nothing read, now held by a partition test; a `Cache-Control` sentence that carved out health
probes the code never carved out; the console's post-login redirect guard, which refused
`//evil.example` and accepted `/\evil.example` — one character narrower than the rule it states;
`_require_oversight`, whose own first line says *"**Not** the oversight set"* and which guards the
kill switch at three call sites a reviewer reads by name; and `make showcase-doctor`, which failed
on a healthy stack every run for everybody, because its duplicate-account heuristic matched any
eight-character word and the shipped realm creates one ending in `security`.

**Left as a report, not a change.** Two things, for the same reason: each is a decision about
scope rather than a defect to fix quietly.

`features/models/model-catalog.ts` is 1456 lines and three tabs, which is the shape `CLAUDE.md` §3
names and `use-case-detail` was split at 1238 for.

And `disabled` thinking is asserted to a **fallback** that has none. `_validated` returns "send no
parameter" when `disabled` is asked of a model that declares no thinking, because `thinkingBudget:
0` is a 400 from Google for every model that cannot be switched off — and that correction applies
to the routed model only. Thinking is resolved once, the chain does not re-resolve per hop, and
`permitted_by` waves a `disabled` setting past any candidate. Measured: a primary declaring
`{disabled, auto}`, a fallback declaring no thinking, `permitted_by` → `None`, and the fallback is
sent `{'thinkingBudget': 0}`. The consequence is bounded — the upstream 400 maps to a visible
`400 FAILED_PRECONDITION`, not a silent wrong answer — and the honest repair is re-resolving
thinking per hop, which changes the dispatch path's contract (`Routing` deliberately carries
addressing and nothing else).

Counts after: 2 920 hermetic Python tests, 946 console tests, **622 mutation properties, all defended** (a full `make
mutants`, 33 minutes, nothing survived), `make
lint-py`, `make lint-frontend` and Prettier clean, `make showcase` green with its two control rows
back in the audit trail, and the live-stack layer at **840 integration tests, 819 passed, 21
skipped, none failed** — the run that started this last stretch by failing twenty-two of them.

**And a note on method, because it cost more than any single finding.** Twice I read a wall of
`502 ConnectError` as evidence about the code. The first time I had run an Angular build and a
mypy pass alongside a suite that was measuring a live model; the second time I had killed that
suite mid-test — `test_diagnostics.py` was between its deliberate `docker stop aira-ollama` and the
`finally` that starts it again, so the container simply stayed down. Both times the answer was one
`curl /readyz` away and I reached for the diff instead. **A measurement taken while you are doing
something else is not a measurement**, and a live-stack suite is not a thing to interrupt. The
header added above is the part of that which generalises; the rest is a note to whoever reads this
next.
