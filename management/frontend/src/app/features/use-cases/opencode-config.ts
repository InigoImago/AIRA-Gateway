import { CatalogModel } from '../../core/api/models';

/**
 * The currency OpenCode's `cost` is quoted in by convention: models.dev uses US dollars per
 * million tokens and OpenCode prints the total with a `$`.
 *
 * **The prices are written whatever the installation's currency.** Without a `cost` OpenCode
 * shows `$0.00` for every session; no figure is not the safe answer here, it is the useless one.
 * So the figures go in and the **currency travels in the model's name**, which is the one field
 * this file controls and which OpenCode shows in its model list and beside the session.
 * Converting instead would need an exchange rate per booking (`FRD-403`), which is the one thing
 * that must not be invented.
 */
const OPENCODE_CURRENCY = 'USD';

/**
 * One model, as OpenCode's configuration file describes it (`FRD-132` §11).
 *
 * OpenCode reads token counts from the API, but its context gauge and cost come from this file:
 * without `limit` and `cost` here the gauge reads 0% forever. The API surface stays Google's shape.
 *
 * **Absent, never zero.** A key is omitted when the catalog has no figure: OpenCode shows `0` for a
 * missing one, but writing `0` ourselves would make the console the author of a wrong figure.
 */
export interface OpenCodeModel {
  name: string;
  limit?: { context: number; output: number };
  cost?: { input: number; output: number };
}

/**
 * The name OpenCode shows for the model, with the currency where a price is written and the
 * installation does not use the one OpenCode assumes.
 *
 * In the name because that is where a figure's label can still reach the reader: OpenCode prints
 * the cost with its own `$`, and nothing this file writes changes that.
 */
function labelled(model: string, currency: string, priced: boolean): string {
  const unit = currency.trim().toUpperCase();
  if (!priced || unit === OPENCODE_CURRENCY || unit === '') {
    return `${model} via AIRA`;
  }
  return `${model} via AIRA (${unit})`;
}

/** A decimal string from the API as a number, or `null` for anything that is not one. */
function priced(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value.trim() === '') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function openCodeModel(model: CatalogModel, currency: string): OpenCodeModel {
  // Both or neither: `limit` is a pair, and a missing `context` would be a gauge at 0% forever.
  const context = model.context_window ?? null;
  const output = model.max_output_tokens ?? null;

  // Per 1,000,000 tokens on both sides, so no arithmetic — and **never converted**: an exchange
  // rate needs a date per booking (`FRD-403`). Written in whatever currency this installation
  // accounts in, with that currency in the name (see `labelled`).
  const input = priced(model.input_price_per_million);
  const out = priced(model.output_price_per_million);
  const hasPrice = input !== null && out !== null;

  const entry: OpenCodeModel = { name: labelled(model.name, currency, hasPrice) };
  if (context !== null && output !== null) {
    entry.limit = { context, output };
  }
  if (hasPrice) {
    entry.cost = { input: input as number, output: out as number };
  }
  return entry;
}
