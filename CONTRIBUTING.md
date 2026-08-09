# Contributing

## Where the reasoning lives

The README says what AIRA does. These say why it does it that way, and they are the first thing to
read before changing anything substantial.

| | |
|---|---|
| [**CLAUDE.md**](CLAUDE.md) | Conventions, locked-in decisions, and the current state in detail |
| [**PRD**](docs/PRD.md) | What the product must do, as the owner defined it. §1.1 is the feature list planning is done from |
| [**Roadmap**](docs/ROADMAP.md) | The phases, and the order things are built in |
| [**ADRs**](docs/adr/) | Every significant architectural decision, with the alternatives that were rejected |
| [**FRDs**](docs/features/) | What each feature must do, written before it is built |
| [**Devlog**](docs/DEVLOG.md) | A dated record of what changed and what it cost to find out |

## The working agreement

1. **Write the FRD first.** Copy `docs/features/FRD-TEMPLATE.md`. If the implementation deviates
   from it, the FRD changes too — a document that describes something the code does not do is worse
   than no document.
2. **Record decisions as ADRs.** Copy `docs/adr/ADR-TEMPLATE.md` and link it from
   `docs/adr/README.md`. The rule of thumb: if a future contributor would have to reverse-engineer
   why, write it down.
3. **Append to the devlog** with what changed and why.
4. **Update the reader-facing documents** when a change alters what a reader would do or expect:
   architecture, request lifecycle, deployment, configuration, integrations, gap analysis, roles.

## Testing

```bash
make ci                # exactly what CI checks
make mutants           # break each guarded property; a test must notice
make test-integration  # against the live stack
make test-e2e          # a real browser
```

Two rules that are not negotiable:

- **Never weaken a coverage gate to make a test pass.** If coverage drops, the missing tests are
  the work.
- **Prove a new test can fail.** Break the property it guards, watch it go red, restore. A test
  that has never been red is a test nobody has checked — including the tests that are themselves
  safety nets, two of which turned out to be silently inert this way.

## Style

- English everywhere: code, comments, documentation, commit messages.
- Python: ruff and black, type hints checked by mypy. TypeScript: strict mode, eslint, prettier.
- Angular is zoneless — all mutable component state is a `signal`.
- Comments explain *why*, and are worth their space only if a reader would otherwise have to guess.
