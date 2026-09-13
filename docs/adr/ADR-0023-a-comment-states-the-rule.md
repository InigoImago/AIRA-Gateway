# ADR-0023 — A comment states the rule; the story lives in the DEVLOG

- **Status:** Accepted
- **Date:** 2026-09-12
- **Deciders:** project owner

## Context
The codebase grew one round at a time, and each round wrote its findings into the code it touched:
the date a defect was found, how it was measured, what the code used to do, which test caught it.
By September about half of every source file was comment or docstring (gateway 50 % code, the
shared library 44 %), constants sat between the functions that use them, and several modules had
grown past a thousand lines holding many concerns (`api/serving.py` 1757 lines, `api/kira/routes.py`
959, `pipeline/engine.py` 956). A reader looking for what a function does first had to read why it
was once wrong. The owner asked for the code to be made clearer, with the existing tests as the
ground truth.

## Options considered
- **Restructure only, keep every comment** — safest; the narrative stays the bulk of every file.
- **Condense comments to the rule and its reason**; the story stays where it already is — the
  DEVLOG entry, the FRD, `LESSONS.md`, the git history.
- **Strip comments to near nothing** — loses the *why* that often exists nowhere else.

## Decision
The second option (owner's choice). In code:

- A comment or docstring states **what the rule is and why**, with its FRD/ADR reference and any
  non-obvious constraint (an ordering, a security property, a trap for the next editor). It does not
  narrate: no dates, measurements, "found live", "used to", or retellings of how a defect was found.
- Module constants, tables and limits sit at the **top** of the module, after the imports.
- A file that holds several concerns becomes a **package** (or sibling modules), one module per
  concern, with an `__init__` that re-exports its public names so imports stay stable.
- Refactoring changes no behaviour: the tests are the ground truth, structural guards follow moved
  code, and every moved mutation anchor is re-pointed and its mutation re-run.

## Consequences
- Positive: a file reads top-down — definitions, then behaviour — and the reason for a rule sits
  next to it in a sentence or two.
- Negative / trade-offs: an incident's full story is one hop away (DEVLOG, FRD, `git log -S`)
  instead of inline. Accepted: the story was already recorded there, and the inline copy had become
  the larger part of the code.
- Follow-ups: new code follows this convention; `CLAUDE.md` §3 states it.
