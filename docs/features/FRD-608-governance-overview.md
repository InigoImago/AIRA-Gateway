# FRD-608 — A governance overview, and residency that is measured rather than claimed

> Phase: 6 (governance) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner: *"there is still no overview for IT Steuerung where they can list
> use cases, the description in them, the models used in the use case, all the controls like how
> many days data is stored and so on, and generally how the data processing happens. I do not know
> yet whether read access to all use cases is enough, or whether a separate view or CSV export
> would do."*
> Related: `FRD-505` (requests view), `FRD-601`/`FRD-602` (reporting and CSV), `FRD-607` (retiring
> a use case), `FRD-404` (retention), `FRD-115` (residency), `ADR-0007` (governance is read-only).

## 1. The question, and what the code already answers

The owner's uncertainty is the right one to have, and the answer is unusual: **two of the three
things they were unsure about already exist, and the third is what is missing.**

Read against the code on 2026-08-19:

| | state |
| --- | --- |
| **Does IT Steuerung have access?** | **Yes.** `OVERSIGHT_ROLES` includes it, so it sees every use case; `GOVERNANCE_ROLES` includes it, so it sees every figure in reporting. Read-only by design (`ADR-0007`) — it may act inside none. |
| **Do the fields exist?** | **Yes, all of them.** A use case carries `description`, `processing_notes`, `store_payloads`, `retention_days`, `allowed_models`, `tools_enabled`, `prompt_caching_enabled`, `include_reasoning`, `prompt_cache_ttl`, `restrict_members_to_own_requests`. |
| **Is there a view?** | **No.** `use-case-list.html` renders **two columns** — name and technical id. Everything else is one click deeper, one use case at a time. |

So this is not a data problem or a permission problem. It is a **shape** problem, and the shape
matters because governance is a *comparison* activity. *"Which use cases store prompts?"*, *"which
keep them longer than 30 days?"*, *"which use a model outside the EU?"* are not answered by opening
forty detail pages; they are answered by one table that can be sorted and exported.

**So: a separate view, and CSV.** Not because access is missing, but because a list of two columns
is not a register.

## 2. What it should be

### 2.1 One row per use case, comparison-first

| column | source | why a governance reader needs it |
| --- | --- | --- |
| Use case (name · slug) | `UseCase` | the subject |
| Purpose | `description` | Art. 30(1)(b) — purposes of processing |
| Processing | `processing_notes` | how the processing happens, in the owner's own words |
| Models | `allowed_models` | Art. 30(1)(d) — recipients |
| Provider · region | catalogue join | **third-country transfer**, per model |
| Prompts stored | `store_payloads` | whether personal data is retained at all |
| Retention | `retention_days` | Art. 30(1)(f) — erasure deadlines |
| Own-requests only | `restrict_members_to_own_requests` | who inside the use case sees whose content |
| Tools · caching · reasoning | three flags | what leaves the request path, and where |
| Members · groups | counts, with a link | Art. 30(1)(a-ish) — who processes |
| Status | live / retired (`FRD-607`) | a retired use case is a record, not an omission |

Every one of these is a field that exists today. **The view is a reading, not a new datapath.**

### 2.2 The same rows as CSV

`FRD-602` already renders CSV as a *renderer on an existing endpoint* rather than a second export
path, and `csv_export.py` has the machinery. This should follow that shape exactly.

The CSV is the deliverable, not a convenience: printed, it is close to a **Verzeichnis von
Verarbeitungstätigkeiten (Art. 30 DSGVO)** — purpose, categories, recipients, third-country
transfer, erasure deadlines — assembled from configuration the system already enforces rather than
from a spreadsheet somebody maintains beside it.

### 2.3 Residency: measured, not claimed

This is the half I would argue hardest for, because it is the difference between a policy document
and evidence, and today's measurement makes the case better than an argument does.

Every audit row carries the region the request **actually** went to (`FRD-115` FR-10). Asked of the
running installation on 2026-08-19:

| region | provider | requests |
| --- | --- | --- |
| `europe-west1` | vertex | 236 |
| **`global`** | **generative-language** | **2** |
| (none) | mock / local | 1 078 |

Two requests were processed outside any EU guarantee. Not a risk assessment — a fact in the
installation's own log, which nothing currently surfaces. The rows with no region are provably
benign because the provider column names them: `mock` and `local` run in the container.

The overview should therefore carry, per use case and for the installation as a whole, **where
processing actually happened over the reporting period** — beside what the configuration claims.
When those two disagree, that is exactly the finding a governance role exists to make.

Two supporting facts, both already true and neither visible anywhere:

- ~~`AIRA_ALLOWED_REGIONS` in this deployment permits `global`~~ — **answered and built**
  (`FRD-611`, 2026-08-20). `global` is out of this deployment, and the audit trail had already
  shown two requests processed there.
- ~~Nothing in the console warns when a model is catalogued in such a region.~~ — **answered and
  built** (`FRD-611`): an impermissible region is refused where it is typed, and what is allowed is
  named in the field's own error.

What that does **not** answer is the paragraph above it: those two were about the *configuration*,
and this section is about **where processing actually happened**. A model catalogued in a permitted
region and served from another is still a finding nothing surfaces, and it is the one a governance
role exists to make.

### 2.4 Erasure as evidence, not as a setting

`RetentionService` reports `payloads_cleared` and `rows_deleted` on every pass, and nothing reads
it. *"Prompts are deleted after N days"* is a claim; *"the last pass ran at 03:00 and cleared 1 412
payloads"* is evidence, and it is the second one an auditor asks for. Surfacing the last run and
its counts costs a table and a query.

## 3. What I would not build, and why

- **A second permission model.** IT Steuerung already sees everything; adding a governance-only
  scope would be a second answer to a question `OVERSIGHT_ROLES` already answers, and two answers
  drift.
- **Editing anything from this screen.** `ADR-0007` makes governance read-only deliberately. A
  register that can change what it registers is not a register.
- **A PDF.** CSV goes into whatever the compliance function already uses; a PDF is a layout
  decision made on their behalf.

## 4. One thing this pass found that belongs here

While counting the catalogue, `mock-1` was in the **gateway's** read model and not in Management's.
A model the gateway could serve, that no console screen showed and no role could remove. It came
from a test run and was harmless, and the shape is not: **nothing compares the two planes' lists.**
An overview whose whole purpose is *"is what we think is configured what is actually running"*
should compare them — models, use cases, and the counts — and say when they disagree.

## 4a. What was built

`/v1beta/register` on the **gateway**, JSON and CSV by `Accept`, and a `Register` screen in the
console offered to an oversight role. One module assembles it (`reporting/register.py`), one renders
it (`reporting/register_csv.py`), and the route sits behind `api/reporting.py`'s heading — because
it is scoped by the very same `visible_scope` the report and the trace list use, and *two endpoints
that are safe the same way should share a file* (the same argument that moved the suspension
endpoints **out**, read the other way).

**Served by the gateway although Management authors every configuration field in it.** The gateway
is where the two halves meet: `UseCaseRead` already carried every field §2.1 asks for, and the audit
trail is the measurement. Assembling this in Management would have meant shipping the audit trail
across the planes to reach a half that was already on this side.

A consequence worth stating rather than discovering: the register therefore reflects the
**read-model**, which learns over Kafka. A use case authored a second ago is in Management and not
yet here. For a register that is the more honest of the two readings — it describes what is *in
force* rather than what was last typed — and the browser test says so out loud by polling for it.

Section by section:

| §  | built | note |
| --- | --- | --- |
| 2.1 one row per use case | ✅ | every column, including `members`/`groups` counts and live/retired |
| 2.2 the same rows as CSV | ✅ | a renderer over the same result, never a second query (`FRD-602`'s shape); every cell through `aira_common.spreadsheet` |
| 2.3 residency measured | ✅ | `processed_in` per use case and installation-wide, and `unexpected_regions` — regions traffic reached that no released model's catalogue entry names |
| 2.4 erasure as evidence | ✅ | `retention_runs` (migration `0041`); the sweep records each pass inside the same transaction as the deletions it describes, and the register prints the last one — or says there is none |
| 4 the two planes compared | ✅ (models) | the register reports the gateway's catalogue; the console holds Management's and shows the disagreement. **Use cases are not compared** — see below |

Three decisions taken while building, each of which could have gone the other way:

- **Unknown is not a violation.** A request whose audit row carries no region is reported under its
  provider and never counted as a transfer. Most dialects address a model by name, and the mock and
  local providers run in the container; counting those would make the finding column always red,
  which is the reliable way to have a finding ignored.
- **A use case whose models name no region has no finding.** There is nothing to disagree with, and
  the alternative reports every region as unexpected for every OpenAI-dialect use case.
- **No erasure deadline where nothing is stored.** `retention_days` keeps its value in the database
  when storage is switched off — turning it back on should not lose the period somebody chose — and
  printed beside *not stored* that number reads as a promise about data that was never written.

## 5. Open questions for the owner

1. **Scope of the register**: this document assumes one row per use case for the *installation*.
   Do you also need it per organisational unit, or is that a filter? — **Still open, and not
   buildable as asked**: there is no organisational unit in the data model. The register has a
   search box over name, id, purpose and processing, which is a filter over whatever convention a
   deployment already puts in those fields. A real unit would be a field on `UseCase` first.
2. **Retention of the register itself**: a CSV exported monthly is a record. Should the system keep
   the exports, or is producing them on demand enough? — **Built as on-demand**, which is the
   smaller and reversible choice and matches `FRD-602`. Keeping exports would make the system the
   custodian of its own compliance record, which is a decision with a retention question of its own;
   on demand, the document is reproducible for any past period from data that is already kept.
3. ~~**`global`**~~ — **answered by the owner and built as `FRD-611`** (2026-08-20): *"global out of
   allowed regions, and refuse the ones that are not permitted in the console."* Both halves shipped.
   The stated cost: `gemini-3.5-flash` is reachable with this credential **only** at `global`, so
   under an EU requirement it is not usable here — recorded as a policy outcome rather than worked
   around.
4. ~~**Retired use cases**~~ — **answered from this document's own argument**: in the register by
   default, marked `retired`. They are still processing records for as long as their payloads exist,
   and a register that omitted them would quietly stop describing the data it is about.

5. **Use cases across the two planes** — **new, and the one piece of §4 not built.** Models are
   compared; use cases are not. The register can report the gateway's own list, and comparing it to
   Management's needs both lists in the browser at once — which at the 917 rows one installation
   already has is a paging question rather than a rendering one. The models were the concrete
   instance §4 found and are a list of tens; this is the same idea at a size that needs a decision
   about where the comparison runs.
