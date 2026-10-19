# Contributing

## Where the reasoning lives

The README says what AIRA does. These say why it does it that way, and they are the first thing to
read before changing anything substantial.

| | |
|---|---|
| [**CLAUDE.md**](CLAUDE.md) | Conventions, locked-in decisions, and where to look for the current state |
| [**PRD**](docs/PRD.md) | What the product must do, as the owner defined it. §1.1 is the feature list planning is done from |
| [**Roadmap**](docs/ROADMAP.md) | The phases, and the order things are built in |
| [**ADRs**](docs/adr/) | Every significant architectural decision, with the alternatives that were rejected |
| [**FRDs**](docs/features/) | What each feature must do, written before it is built. Each one's `Status:` header is the only place its status lives |
| [**Devlog**](docs/DEVLOG.md) | A dated record of what changed and what it cost to find out |
| [**Lessons**](docs/LESSONS.md) | The rules this project has already paid for — read them before planning |

## The working agreement

1. **Write the FRD first.** Copy `docs/features/FRD-TEMPLATE.md`. If the implementation deviates
   from it, the FRD changes too — a document that describes something the code does not do is worse
   than no document. **When it ships, change its `Status:` header** and regenerate the index with
   `uv run python tools/features_index.py --write`: `docs/features/README.md` is generated from the
   headers, and `tools/tests/test_features_index.py` fails when the two disagree.
2. **Record decisions as ADRs.** Copy `docs/adr/ADR-TEMPLATE.md` and link it from
   `docs/adr/README.md`. The rule of thumb: if a future contributor would have to reverse-engineer
   why, write it down.
3. **Append to the devlog** with what changed, what was measured and what a round found. The story
   goes there — not into code comments (`ADR-0023`), and not into `CLAUDE.md`.
4. **Add to the lessons only when a round produced a new rule**, and merge it into the existing
   entry when it is the same shape in different clothes.
5. **A change to what is processed is a change to the privacy notice** (`ADR-0027`, `FRD-625`).
   Every column either plane stores is named in
   `management/backend/src/aira_management/apps/privacy/activities.py` or declared not personal, and
   `management/backend/tests/test_the_privacy_notice_names_every_column.py` fails otherwise. A
   changed register or text needs a new `EDITION` and `SOURCE_DIGEST` in `apps/privacy/edition.py`
   (`test_privacy_notice.py` fails and names the new digest) and is described in **every**
   `apps/privacy/texts/*.toml`. A new transmission, telemetry attribute or browser storage stores
   nothing that check can see: add it to the register and the texts by hand.
6. **Update the reader-facing documents** when a change alters what a reader would do or expect:
   architecture, request lifecycle, API reference, deployment, configuration, integrations, gap
   analysis, roles.
7. **Cutting a release is one of these, not a chore after them.** A release is an annotated tag and
   **its message is the release notes** — there is no changelog. `make release-tag VERSION=vX.Y.Z`
   opens your editor on `docs/RELEASE-TEMPLATE.txt`; five rules refuse an annotation that says
   nothing, and a rejected one is deleted again so the version number is not spent. Details and the
   two failures that produced the rules: [`docs/DEPLOYMENT.md` §1b](docs/DEPLOYMENT.md#1b-releasing-cutting-a-version-and-publishing-the-images).

## Testing

```bash
make ci                # the hermetic gates: lint, types, unit tests with coverage
make mutants           # break each guarded property; a test must notice
make test-integration  # against the live stack (tests/integration/README.md)
make test-e2e          # a real browser (e2e/README.md)
```

`make ci` runs what CI's first two jobs run, and `tools/tests/test_ci_runs_what_a_developer_runs.py`
keeps the two in step. CI then starts the full stack twice: once for the integration suite, once for
the browser suites (end to end, then Windows sign-on).

Three rules that are not negotiable:

- **Never weaken a coverage gate to make a test pass.** If coverage drops, the missing tests are
  the work.
- **Prove a new test can fail.** Break the property it guards, watch it go red, restore. A test
  that has never been red is a test nobody has checked — including the tests that are themselves
  safety nets, two of which turned out to be silently inert this way.
- **A fixed bug gets a mutation.** Add the edit that reintroduces it to `tools/mutation_check.py`,
  naming every test file you expect to fail, so `make mutants` notices if it comes back.
  `tools/tests/test_mutation_anchors.py` fails when an anchor no longer matches exactly one place.

## Generated files

Edit the source and regenerate; never edit the copy. Each guard fails when the copy is stale.

| Generated | From | Regenerate with | Guard |
|---|---|---|---|
| `docs/features/README.md` | each FRD's header | `uv run python tools/features_index.py --write` | `tools/tests/test_features_index.py` |
| `management/frontend/src/app/features/use-cases/opencode-plugin.generated.ts` | `tools/opencode/plugins/aira-budget/tui.tsx` | `uv run python tools/opencode_plugin_asset.py --write` | `tools/tests/test_the_console_hands_out_the_plugin_it_ships.py` |
| `management/frontend/public/favicon.ico` | `management/frontend/public/aira-mark.svg` | `uv run python tools/make_favicon.py --write` | `tools/tests/test_favicon_is_the_aira_mark.py` |

## Database migrations

- **Gateway (Alembic).** One file per revision in `gateway/migrations/versions/`, named
  `00NN_<what>.py`, whose `revision` is that stem and whose `down_revision` is the current head.
  Keep the id within 32 characters: that is all Alembic's `alembic_version` column holds. SQLite,
  which the hermetic suite runs on, enforces no length, so only Postgres refuses to record a longer
  one. `gateway/tests/test_the_migration_graph_is_a_line.py` fails on a second head, a second root
  or a missing parent; `tests/integration/test_the_gateway_migrations_match_its_models.py` compares
  the migrated schema with the models on Postgres. `make migrate-gateway` applies them.
- **Management (Django).** Run `uv run python management/backend/manage.py makemigrations` after a
  model change; `management/backend/tests/test_migrations_match_the_models.py` fails when a model
  has changed without its migration.
- **A column is dropped or renamed one release after the code stopped reading it**, so two versions
  can serve against one schema during an update (`FRD-127` §5.4). Nothing enforces this yet.

## Style

- English everywhere: code, comments, documentation, commit messages.
- Python: **ruff** for both lint and formatting (`ruff check`, `ruff format`) — there is no black —
  and type hints checked by mypy in strict mode.
- TypeScript: strict. **Prettier** formats it, and the production `ng build` stands in for a linter
  — there is no ESLint.
- `make lint` checks all of it as CI does; `make fmt` rewrites the formatting and applies ruff's
  fixes.
- **A comment states the rule and its reason** (`ADR-0023`). How the rule was found — dates,
  measurements, "used to" — belongs in the devlog and the FRD. Constants sit at the top of their
  module; a file that holds several concerns becomes a package, one module per concern.
- Angular is zoneless — all mutable component state is a `signal`, and `[(ngModel)]` is written as
  `[ngModel]="x()" (ngModelChange)="x.set($event)"`.
- **A page is a parent plus panels.** The parent loads and owns the tab bar; each panel is a child
  that owns its form state and mutations, and reports through the page's one `PageFeedback`.
- **Frontend tests assert the rendered DOM and real interactions**, not component methods.
