import { CatalogModel } from '../../core/api/models';

/**
 * One model, as OpenCode's configuration file describes it (`FRD-132` §11).
 *
 * ## Why this is in the file at all, rather than read from the API
 *
 * It was measured, because reasoning about it gave the wrong answer twice. Pointed at AIRA and
 * asked a question, OpenCode **does** receive the token counts: its own store recorded
 * `{input: 2050, output: 26, total: 2076}` for a request AIRA's audit row recorded identically.
 * The Gemini surface's `usageMetadata` arrives and is read.
 *
 * What was zero was everything OpenCode never asks the API for. Its resolved model read
 * `limit: {context: 0, output: 0}` and `cost: {input: 0, output: 0, …}`, because those come from
 * **this file** and the console generated it carrying only a display name. A context gauge is
 * `used / limit.context`, so it sat at 0% no matter how full the conversation was — reported as
 * *"I cannot see how many tokens were used and the limits are always at 0%"*, which was two thirds
 * a display and one third a genuinely missing figure.
 *
 * So none of this is a change to the API surface, and that is the point: the surface stays exactly
 * Google's shape. The one thing that *is* published through it — `inputTokenLimit` /
 * `outputTokenLimit` on the model resource, which AIRA had been sending under an invented name —
 * is a move **towards** the official shape, not away from it.
 *
 * ## Absent, never zero
 *
 * A key is omitted when the catalog has no figure. OpenCode fills a missing `limit` with `0`, and
 * `0` and "unknown" look the same on screen — but they are opposite instructions to whoever reads
 * the gauge. Writing `0` ourselves would make the console the author of the wrong one.
 */
export interface OpenCodeModel {
  name: string;
  limit?: { context: number; output: number };
  cost?: { input: number; output: number };
}

/** A decimal string from the API as a number, or `null` for anything that is not one. */
function priced(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value.trim() === '') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * The one currency OpenCode's `cost` means.
 *
 * models.dev — the catalogue every other provider in OpenCode is described from — quotes US dollars
 * per million tokens, and OpenCode renders the running total with a `$`. So the figures may be
 * written through **only** when this installation prices in the same unit.
 */
const OPENCODE_CURRENCY = 'USD';

export function openCodeModel(model: CatalogModel, currency: string): OpenCodeModel {
  const entry: OpenCodeModel = { name: `${model.name} via AIRA` };

  // **Both or neither.** OpenCode's `limit` is a pair, and half of it is `0` for the other half —
  // which for `context` is a gauge reading 0% forever, the exact defect this exists to fix.
  const context = model.context_window ?? null;
  const output = model.max_output_tokens ?? null;
  if (context !== null && output !== null) {
    entry.limit = { context, output };
  }

  // Per 1,000,000 tokens, which is both what the catalog stores and what OpenCode expects, so
  // there is no arithmetic to get wrong here. The **unit** is the whole question, and it is why
  // this takes a currency instead of assuming one: OpenCode prints the running total with a `$`,
  // so figures from an installation that prices in euros would be displayed as dollars — a number
  // somebody budgets against, wrong by whatever the exchange rate is that morning.
  //
  // No conversion, deliberately. `AIRA_CURRENCY`'s own comment refuses exchange rates for the
  // reason they are refused everywhere in this system: a rate needs a date per booking, and that
  // is a standing source of figures nobody can reconcile. Absent is the honest answer; OpenCode
  // then shows no cost, which is true.
  const input = priced(model.input_price_per_million);
  const out = priced(model.output_price_per_million);
  if (input !== null && out !== null && currency.toUpperCase() === OPENCODE_CURRENCY) {
    entry.cost = { input, output: out };
  }
  return entry;
}
