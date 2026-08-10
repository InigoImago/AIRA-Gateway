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
