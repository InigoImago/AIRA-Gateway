# FRD-210 — A count reads the same in English and in German

> Phase: 2 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, reading token counts in the console from Germany: the commas between the
> thousands confuse — `1,234` is one thousand two hundred and thirty-four to an English reader and
> one and a bit to a German one. What kind of number it is should be clear.
> Related: `FRD-207` (the console explains itself), `FRD-403` (money), `FRD-409` (the budget mail).

## 1. Summary

Every count the installation shows a person — tokens, requests, bytes, milliseconds — groups its
thousands with a **narrow no-break space**: `12 840`, `1 234 567`. That is how the SI writes a
number, and DIN 5008 too. A comma groups thousands in English and marks the decimals in German, a
point the other way round; a space means neither, so the same figure reads the same to both, in a
console that stays English. One function per plane says it, and the texts that wrote a large
number out in words of digits say *million* instead.

## 2. Goals & Non-Goals

**Goals**
- A count no reader can take for a decimal, on every surface: the console, the mails, the
  server's messages.
- One rule in one place per plane, so a chart and the table beside it never disagree.

**Non-Goals**
- A locale-dependent console. The console is English; a figure written two ways on two screens
  reads as two figures.
- Money. An amount has two decimals, or more with a leading `0.` for a fraction of a cent, which no
  reader takes for a group of thousands; amounts are not grouped.
- The OpenCode plugin's sidebar, which shortens a count to `12k` or `1.2M` to fit 37 columns.
- Exports. A CSV is read by programs, and its numbers carry no grouping.

## 3. User Stories

- As a reader in Germany, I want `12 840 tokens` rather than `12,840 tokens`, so that I do not read
  twelve point eight.
- As any reader, I want a count grouped the same way in a chart, a table and a mail.

## 4. Functional Requirements

- **FR-1 The console.** `core/ui/money.ts` `displayCount` groups by U+202F — every count on every
  screen and chart goes through it, the technical record's milliseconds and bytes and the price
  steps' prompt lengths included.
- **FR-2 The server.** `aira_common.counts.display_count` does the same for a budget mail's counts,
  the model reference's notes and the refusal of an event too large for the bus.
- **FR-3 Words for round numbers in text.** "per 1,000,000 tokens" is "per million tokens"; a price
  typed with a decimal comma is refused with the sentence saying a point goes before its decimals.

## 5. Design

`en-US` formatting with its commas replaced: the decimal mark stays a point, the console's
convention, and only the grouping changes. No-break, so a line never splits a number, and narrow,
so a grouped count is barely wider than an ungrouped one.

## 10. Testing & Acceptance Criteria

- `money.spec.ts`, `libs/tests/test_counts.py` — the separator, no comma and no point, a negative
  count; every screen's spec asserts the figure as rendered.
- `test_budget_and_anomaly_mail.py`, `test_model_reference.py` — the mail and the reference's note.
- `e2e/tests/use-cases.spec.ts` — a budget's counts in a browser.
- Mutations `NUM-1`–`NUM-3`.

**Acceptance**

- *Given* a use case that spent 1 234 567 tokens of 2 000 000, *when* its budgets are shown, *then*
  they read `1 234 567 / 2 000 000`, with no comma.

## 12. Rollout / Demo

Images only; nothing is stored differently.
