# FRD-308 — Which models a use case may call

> Phase: 3 · Status: **Built** · Owner: Vadim Scheibe

## 1. Problem

Asked directly: _"mir ist gerade aufgefallen, dass wir Use Cases nicht auf die Modelle
beschränken, es sollte doch so sein, dass entweder globale admin oder Use Case admin für einen Use
Case erlaubte Modelle Freigeben."_

Half of that already existed and did not work.

`FRD-307` answers **which models may be used in this installation at all** — a Global Administrator
catalogues a model and releases it. Nothing answered **which of those a particular use case may
call**. Any use case could name any approved model: the same one that summarises HR drafts could
call the most expensive reasoning model in the catalog, and nothing said no.

There was a mechanism, and on 2026-08-11 it was **measured** rather than read. The `allow_check`
pipeline step (`FRD-300`) takes a list of model names and refuses anything else:

| what the request did                          | result |
| --------------------------------------------- | ------ |
| named a model outside the list                 | **403** ✅ |
| was re-targeted by a `model_route` step        | **200, served** |
| was picked up by the `fallback_models` chain   | **200, served** |

The step runs **once, before routing**, against the model the *caller* named. `requirements.py`
had already written the rule down in its own docstring: _the check that runs before routing
protects nothing._ Attachments, tools, thinking, schemas and residency are all checked **per hop**
for exactly this reason; the model allow-list was the one control that was not.

Three smaller problems came with it: the step had to be added by hand, so a use case without one
was unrestricted and looked identical to one deliberately left open; it lived in the pipeline
builder rather than with the use case's settings; and its list was invisible to anybody who did not
open the graph editor.

## 2. Goals & Non-Goals

**Goals**

- A use case has an explicit set of models it may call, released by a Global Administrator **or**
  by an administrator of that use case.
- Enforced at every hop of routing and fallback, like every other dispatch condition.
- Visible where the use case is configured, including — especially — when it is empty.

**Non-Goals**

- Replacing `FRD-307`. Approval is the installation's decision and remains the outer boundary; this
  is the inner one. Nothing here can widen what a Global Administrator withheld.
- Per-model *limits* (a budget or a rate limit that applies to one model). Those are `FRD-400`/`405`
  questions, and answering them here would put four things in one screen.

## 3. Functional Requirements

- **FR-1** A use case carries a set of catalogued models. **Empty means none** — the owner's
  decision, 2026-08-11. A use case reaches the models somebody released for it; absence of a
  release is not a release.
- **FR-2** Releasing is `may_admin` on the use case: a Global Administrator, or a group granted
  administration of *that* use case (`ADR-0017`). Not `may_manage` — releasing a model changes what
  the use case **is**, not what happens inside it.
- **FR-3** Only an **approved** model may be released, refused by name. The two gates have two
  owners and the inner one cannot open what the outer one closed.
- **FR-4** Enforced as a dispatch requirement, so it is checked against the model **about to be
  dispatched to** — after routing, at every candidate in a fallback chain.
- **FR-5** A chain whose every candidate is withheld raises `NoCapableModel` → **400
  FAILED_PRECONDITION**, naming the model and the use case. Operator-fixable, not an outage.
- **FR-6** The refusal is recorded (`FRD-122`): a governance refusal that leaves no row is one
  nobody can review, and _"why can this team not use that model"_ is what a review opens with.
- **FR-7** `allow_check` is removed. Its lists are migrated into releases; the step disappears from
  the builder, the serializer, the gateway's vocabulary and the read-model.

## 4. Design

### 4.1 Three states, not two

`None`, `[]` and a list mean three different things, and collapsing any two of them breaks
something:

| value    | meaning                             | effect |
| -------- | ----------------------------------- | ------ |
| `None`   | **no event has said** — a read-model row from a Management that predates this feature, or a request with no use case at all | not ours to refuse |
| `[]`     | somebody released nothing            | nothing may be called |
| `[…]`    | exactly those                        | those and no others |

The first row is the one that would be easy to get wrong and expensive: reading "the event did not
mention it" as "released nothing" stops **every** use case on a partially upgraded stack. A
governance control arriving as an outage is how a control gets switched off permanently — the
lesson `FRD-500` recorded when it made `alert` the default action of an anomaly rule.

The same split `FRD-307` made for `approved`, and for the same upgrade.

### 4.2 Where it is enforced

`ModelReleasedForUseCase`, beside `ModelApproved` in `requirements.py`. It reads the release
**once** per request in `requirements_for` and is then asked per candidate, so a five-model chain is
one query. Test doubles are exempt exactly as they are for approval, bounded the same way — the
mock is registered in no environment but `local`.

### 4.3 A relation in Management, a list in the gateway

Two planes ask different questions of the same fact.

Management asks _"which use cases would break if I retire this model"_, which wants a relation:
`model.use_cases.all()`, and deleting a catalog entry cleans up the releases rather than leaving
names that resolve to nothing. The gateway asks _"may this use case call that model"_, which is one
row it already fetches — and a containment query over JSON is written differently on SQLite and
Postgres, which `FRD-505` paid for once already.

The event carries **names**, because a name is what a caller sends, what the audit row records and
what the gateway enforces against. An id would be a fourth identifier for one model, meaningful
only inside one database.

### 4.4 The migration carries a decision, and only a decision

A use case that had an `allow_check` step made a choice: somebody opened the builder, added the
step and typed model names. That is carried into the release.

A use case that never had one could call every approved model. Writing the whole approved catalog
into it would keep it running — and would show, in a console built to record who released what, **a
release nobody made**. `FRD-122`'s rule about the audit row is the same rule: an unverifiable claim
is not evidence. Those use cases start empty and stop serving.

That consequence was stated before the decision was taken and the owner chose it knowing it. What
follows from it is that every refusal has to be **actionable**: it names the model, the use case,
and who can release it; the console leads with the empty state rather than showing a blank list;
and the demo seed releases explicitly, because a showcase that refused all of its own traffic would
teach this rule backwards.

### 4.5 A picker, not a checkbox per row

Asked for directly: _"kannst du modell auswahl in use case als dropdown mit der suche und
multiselect machen? Die anzahl von Modellen wird wachsen."_ Correct — one real credential offered
**50 models**, and a table of checkboxes stops working long before that: the reader scrolls a list
they cannot search to find the four they want, with what is already chosen scattered through it.

`core/ui/multi-select.ts` answers both questions separately. **Chips** above the field say *what
did I pick* and are removable where they are read; the **search** below says *what else is there*.
Shared rather than written into this one screen: the catalog is not the only list here that only
grows, and the second copy is where keyboard handling drifts.

Three properties it has to have, each a way a picker of this shape goes quietly wrong:

- **What is chosen survives a search that hides it.** Filtering to `gemini` must not make three
  already-released models disappear from the screen — they are still in the saved set, and nothing
  would say so.
- **Enter keeps the list open.** The whole point is picking several; a list that closed each time
  would make four models four round trips.
- **Enter never submits the surrounding form.** A picker in a settings form would otherwise save
  the page on every choice, carrying a half-made selection.

Keyboard is most of what its tests assert, because unlike a checkbox there is no fallback
underneath: a picker that works only with a mouse cannot be used at all without one. The list is a
`combobox`/`listbox` pair with `aria-activedescendant`, so *open, how many, which one is under the
cursor, which are picked* are available without sight.

Two things fell out of building it. The first draft advanced the highlight on the **same keypress
that opened the list**, so ArrowDown into a closed picker landed on the *second* option and the
first was unreachable without a mouse. And `.picker` — the class the access panel's directory
results have used since `FRD-209` — turned out to have **no CSS at all**, so that list has rendered
unstyled for as long as the screen has existed. Styling it fixes both; making it `position:
absolute` broke the access panel across the whole page, so the shared class carries appearance and
the multi-select carries where it floats.

### 4.6 The builder chooses, and the dry run was a hole

The pipeline builder took **free text** for every model it names: the classifier a filter runs, the
classifier a router runs, each category's target, the default target and the fallback chain. Free
text offers exactly what the server refuses (`FRD-206`), and here it also invited naming a model the
use case has no right to. All five are now dropdowns over the release; the fallback chain is the
multi-select from §4.5, because it is several **in order**.

Enforced in both planes, because a dropdown is a convenience and not a control:

- **Management** refuses a pipeline naming a model the use case has not been released, by name.
  The gateway would refuse it at dispatch anyway — this is the refusal that arrives while somebody
  can still fix it, instead of surfacing later as refused traffic on a configuration that looks
  correct.
- **The gateway's dry run** does the same, and that one was a genuine escape hatch. It was
  measured before it was fixed: a caller posted a pipeline naming any model as its classifier and
  the gateway **called it** — no use case, no release check, no approval check, no budget, no rate
  limit, **and no audit row**. 1000 tokens spent, nothing recorded, by anybody with a login. The
  module's own docstring claimed its size bounds meant "a single call cannot be turned into a free
  LLM relay"; it was a comment claiming a rule the system did not have.

So a dry run now names a use case (**required**), is refused unless the caller may act on it
(`use_case_refusal` — the same one function both surfaces use), and may name only released models.
Two consequences worth stating:

- **A Global Administrator is a member of nothing** (`ADR-0007`), so they cannot dry-run a pipeline
  they can edit until they grant themselves the use case. That is the same rule a request gets, and
  the console says so rather than sending them to check a gateway setting that is working.
- The model the dry run **infers** when a pipeline names none — the commonest case, an injection
  filter on its own — now comes from the release. It used to be the first *registered* model, which
  after this rule meant a refusal about a model nobody chose: a guess that is guaranteed wrong is
  worse than the one it replaced.

### 4.7 Every model call belongs to somebody

The dry-run finding raised the general question, and the general answer is an inventory rather than
another fix. Six places outside the adapters can reach a provider:

| where | attribution | recorded |
| --- | --- | --- |
| Gemini `:embedContent` | `require_attribution` | `Accounting` |
| Gemini `:streamGenerateContent` | `require_attribution` | `Accounting`, settle shielded |
| KIRA `/embed` | its own resolver (`FRD-107` §5.3) | `Accounting` |
| the injection classifier | the caller's | `pipeline:<step>`, `requests=0` |
| the routing classifier | the caller's | `pipeline:<step>`, `requests=0` |
| the dispatch chain | `require_attribution` | `Accounting` |

`test_every_model_call_is_accounted.py` parses the source and requires each on that list with a
written justification. It is structural because the hole it guards was never a wrong answer — every
behaviour test passed while the dry run was open — it was a call site nobody had counted.

`ping` is deliberately outside the billable set, and a test asserts no adapter's probe reaches a
generating verb: a readiness check that bills somebody for the question "are you there" is the free
unattributed call this whole section exists to rule out (`FRD-117` §5.2).

**And the second hole the inventory found.** An authenticated caller belonging to no use case and
naming none was served — 200, 200 tokens, `use_case = NULL` — charged to no budget, bounded by no
use-case rate limit, and outside this FRD's release, because there was no use case to consult it
for. `AIRA_REQUIRE_USE_CASE` now defaults to true and cannot be turned off outside `local`/demo.
The unbound break-glass key (`ADR-0015`) keeps its exemption and is the only one; the decision lives
in `must_name_a_use_case`, one function both surfaces read.

## 5. Testing

- The two holes, as end-to-end requests: a routing rule that re-targets, and a fallback chain.
  Both were **200 before** and are shown to fail without the requirement.
- The three states, each asserted separately, including the absent one.
- Only approved models are releasable, refused **by name**.
- The event carries the release; the migration carries an `allow_check` list and **not** an absent
  one; running it twice changes nothing.
- The console: the empty state, the withdrawn-model case, read-only, and that filtering the list
  does not silently un-release what it hides.
- `J1`–`J7`.

## 6. Risks

- **Every existing use case stops.** Accepted, deliberately, and mitigated by the refusal wording,
  the console's empty state and the migration carrying real `allow_check` lists. An installation
  upgrading should release models before the gateway is restarted, or expect refusals in between.
- **A release outliving an approval.** A model released to a use case and later withdrawn from the
  catalog is refused by `ModelApproved` first. The console shows those separately rather than
  dropping them silently, because that is a state somebody has to act on.
- **Two lists to keep** — the catalog and each use case's release. That is the cost of delegation,
  and it is bounded: the inner list can only ever be a subset of the outer one, and the server
  enforces that rather than trusting the console.
