# FRD-206 — The console offers only what the server would allow

> Phase: 2 (correction) · Status: **In progress (2026-08-07)** · Owner: AIRA · Last updated: 2026-08-07
> Related: `FRD-201` (RBAC), `FRD-202` (use-case CRUD), `FRD-203` (Angular shell), `FRD-205`
> (key issuance), `FRD-402`, `FRD-405`, `FRD-601`, `docs/adr/ADR-0007.md`

## 1. Summary

A walkthrough of the running console, role by role, produced fourteen findings. Most are cosmetic.
Three are not, and they share one shape: **the console was answering questions only the server can
answer, and answering them generously.**

- A use-case *user* was shown "Add member" and "Remove" on every row. Using either produced a `403`
  — from the screen that had just invited the click.
- IT Security signed in and saw an empty console (fixed the same day; see §4.2).
- The pipeline builder let anyone who could open it rearrange a graph they could never save.

An action nobody can carry out is worse than an absent one. An absent action reads as a boundary; a
present one that fails reads as a broken system, and the reader's next move is to distrust the
figures on the same page — in a console whose entire purpose is evidence, that is the expensive
failure.

The cause is structural, not a slip: object-level permission lives in `django-guardian` rows, so it
is **not in the token**, so `/api/v1/me` cannot carry it. The console had no way to know and filled
the gap with an assumption.

## 2. Goals & Non-Goals

**Goals**
- Every action the console renders is one the server would accept from this caller, on this object.
- The answer comes from the same predicates that enforce it — never a second statement of the rule
  in TypeScript.
- Where an action is withheld, the screen says who does it instead. A silent absence is a second
  puzzle.
- Read-only is a *usable* state, not a stripped one: a reader still sees members, budgets, limits,
  the pipeline, and can still run a dry run, because none of that changes anything.
- Defaulting to "no" while the answer is in flight.

**Non-Goals**
- Client-side permissions are **not** a security control. Every one of these rules is enforced
  server-side and stays enforced; this is about what the console *offers*.
- No new roles, no change to who may do what. The rules are the ones `FRD-201`/`ADR-0007` already
  set.

## 3. Functional requirements

**FR-1** — `GET /api/v1/use-cases/{slug}/` returns a `permissions` object for the calling user:
`can_admin` (may rename/delete it), `can_manage` (may change members, keys, pipeline, budgets,
limits) and `is_member` (belongs to it — which is what issuing a key needs).

**FR-2** — Those three answers are produced by `apps/usecases/access.py`, the same functions the
viewset calls to enforce them. A restatement is a rule that drifts.

**FR-3** — `is_member` is a separate answer from `can_manage` and from visibility. An oversight role
sees every use case and belongs to none of them (`ADR-0007`); a member belongs to one without
administering it.

**FR-4** — The use-case detail renders member add/remove, key revocation, retention settings,
budgets and rate-limit controls only when `can_manage`; the key-issuing control only when
`is_member`.

**FR-5** — Withheld actions are replaced by one sentence naming who performs them, not by nothing.

**FR-6** — The pipeline builder is *read-only* rather than absent for a caller without
`can_manage`: the graph is wrapped in a native `disabled` fieldset (so every control inside it is
genuinely inert, not merely un-saveable), Save is not rendered, and the test panel stays live.

**FR-7** — With no answer yet, the console assumes **no**. Showing an action and taking it away is
worse than showing it a moment later.

**FR-8** — Creating a use case is a window reached from a button, and saving it navigates to the new
use case's settings. A use case with no members, no budget and no limits is not finished, and
returning to the list is what makes it look finished.

**FR-9** — "Slug" is not a word to put in front of a reader. The field is a **technical id**,
filled in from the name, editable, and described by what makes it matter: it is permanent, appears
in the gateway URL and in every API key, while the name is not.

**FR-10** — Editing a model in the catalog happens in a window that names the model being edited.

**FR-12** — A `401` on a first-party call sends the reader to the login. A `403` does not. One
login is started however many requests fail together, and the path is restored afterwards if it is
a same-origin path.

**FR-11** — A figure in the reporting screen carries a short heading and an info button that shows
the sentence saying what it counts. **Hovering shows it** — that is what anybody reaching for an
"i" expects; focus shows it too, for a keyboard; and a click **pins** it, for a touch screen where
there is no hover at all. Not a `title` attribute: a native tooltip needs a long hover, never
appears on a touch screen and is invisible to a keyboard. One explanation at a time, and the panel
is positioned rather than in flow, so the card does not grow under the pointer.

## 4. Behaviour and decisions

### 4.1 Why the server answers, and not the token

`sync_user_roles` puts realm roles into Django groups, and `/me` returns them — that is enough for
"may this person create *a* use case", and the use-case list uses exactly that (FR-8's button). It
is not enough for "may this person manage *this* use case", which is a guardian row. The choice was
between shipping the permission on the object or issuing a second endpoint; the object already had
to be fetched, and a permission that travels with the thing it is about cannot be read for the
wrong thing.

### 4.2 IT Security is a restricted view, not an absent one

`scope_queryset` used `GOVERNANCE_ROLES` for both "sees every use case" and "sees every figure".
PRD §154 gives IT Security the first and not the second. Folded together, the role saw nothing at
all. Split into `OVERSIGHT_ROLES` ⊃ `GOVERNANCE_ROLES`: oversight decides *visibility*, governance
decides *spend*. Two tests hold both halves.

### 4.3 Read-only through a disabled fieldset

Hiding Save while leaving the graph editable would let somebody rearrange a pipeline for nothing —
the same defect one step later. A native `<fieldset disabled>` makes every nested control inert,
including the add/remove buttons in the graph, in one attribute. The test panel sits **outside** it
on purpose: a dry run changes nothing, and it is the most useful thing a reader can do here.

### 4.4 The renewal fix that broke login

`offline_access` was added to the requested scopes to obtain a refresh token. The realm does not
permit offline tokens, so the code-to-token exchange failed with `not_allowed`, Keycloak answered
without CORS headers, and the browser reported a CORS error — naming neither the scope nor the
setting. The console rendered nothing after a successful sign-in.

It was also the wrong instrument: the authorization-code flow already returns a refresh token, and
`offline_access` asks for one that survives the end of the SSO session. `timeoutFactor`, the
automatic silent refresh and the iframe fallback deliver the requirement; the scope was never part
of it.

The defect reached the running stack because three test layers ran and the fourth did not — and the
change lives only in the fourth. No unit test performs an OIDC redirect. A change to the login flow
is an e2e change.

### 4.5 A session that has ended is a login, not an error

Reported from the running console: a token going invalid — left open too long, or Keycloak
restarted — produced "invalid credentials" on every panel at once. That is a true statement and the
wrong one to make. It reads as *the backend rejecting this person*, and the next thing doubted is
the figures on the same page.

A `401` on a first-party call (`/api`, `/gw`) means exactly one thing, and there is exactly one
action available, so the console takes it: drop the dead token and start the login. Three details
are load-bearing:

- **`403` is left alone.** That is a real answer about a real permission and the caller is signed
  in perfectly well; logging them out would hide the boundary behind a login screen — the same
  mistake as §4.1 in the other direction.
- **One login, however many requests fail.** A screen makes several calls at once; five 401s
  starting five logins would leave four stale `state` entries racing over which returns.
- **The place is kept.** `initCodeFlow` carries the current path, and it is restored on the way
  back — but only if it is a same-origin path, because `state` survives a round trip through the
  browser and treating it as a destination would be an open redirect with extra steps.

Renewal failure is handled at the source too: when a silent refresh fails *and* no valid token
remains, the login starts immediately rather than waiting for the next request to fail on a screen
the reader is already looking at.

Two endings, both verified in a browser: with the Keycloak session still alive the round trip is
invisible and the reader never learns anything happened; with the session gone — the case actually
reported — they land on the login form.

### 4.6 The info button that did nothing

The first version put the explanation in a `title` attribute. It was reported from the running
console as "the info elements show no information", and that was exactly right: the control was
there, it responded to nothing, and the reader is left assuming the screen is broken.

The second version opened it on click. That worked and was still the wrong answer — an "i" is a
thing you point at, and the reader had said so. It now shows on **hover**, on **focus** for a
keyboard, and stays **pinned** on a click for a touch screen, which has no hover to offer. The
panel is absolutely positioned: in flow it would grow the card under the pointer and shove the rest
of the row, which is the jumpiness a hover is least forgiving of.

Both a unit test and an e2e case assert that using it reveals text, and the e2e one uses a **real
hover** — only a browser can tell "renders a tooltip attribute" from "shows the reader anything",
which is how the first version passed review in the first place.

### 4.7 Two different membership lists

The budgets tab reported "the gateway does not count you as a member of this use case" to a reader
looking at their own name in the Members tab. Both statements were true: the gateway takes
membership from the Keycloak group `/use-cases/<slug>`, Management from its own table. The message
now says that, in those words, because the remedy is a group and not a table.

## 5. Testing

Hermetic (frontend, Vitest — assertions on rendered DOM):
- a reader sees the members table and no Remove button, no Add member, and the sentence saying who
  does it;
- the same reader *can* still issue a key, because membership is what that needs;
- with `permissions` absent, nothing is offered;
- budgets and rate limits: figures present, controls absent;
- the pipeline: `fieldset.bare` is `disabled`, Save is not rendered, `#sample-system` is outside it;
- the model editor names the model it is editing, and Cancel discards the edit;
- the use-case window derives the id from the name, stops once the id is typed by hand, and
  navigates to the new use case's settings.

Hermetic (backend, pytest):
- the three answers for an admin, a member and an oversight role;
- **an agreement test**: for each of the three, the corresponding request is attempted and its
  status must match what the object reported. This is what stops the serializer and the viewset
  from drifting apart.

Mutation (`make mutants`): `Z23` (the reported `can_manage` is hardcoded true) and `Z24` (the
reported `is_member` is hardcoded true) — both caught by the agreement test. `G1` and `G3` were
**re-anchored** onto their new homes after this change moved them; a mutation whose anchor has
moved protects nothing.

End to end (Playwright, real browser): an info button reveals its sentence and closes again; and
`auth.spec.ts` signs in as four demo accounts. This is the
only layer that can see a broken login, and it is the layer that caught `offline_access`. Its
role-navigation case was rewritten rather than deleted when the disabled tabs went: the property is
that the console follows the roles in the token, and it now reads the header's role chips.

Proven able to fail: flipping the console's default from `?? false` to `?? true` turns FR-7's test
red, and only that test — which is what makes it a test of the default rather than of the feature.

## 6. Open

- The remaining cosmetic items from the walkthrough are tracked in the DEVLOG entry rather than
  here.
- The console still fetches the use case to learn its permissions on the pipeline screen. If more
  screens need them, a single cached fetch is the next step — not a second endpoint.
