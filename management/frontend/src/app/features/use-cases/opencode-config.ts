import { CatalogModel } from '../../core/api/models';

/**
 * The one currency OpenCode's `cost` means: models.dev quotes US dollars per million tokens and
 * OpenCode prints the total with a `$`, so prices are written only when this installation uses it.
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

/** A decimal string from the API as a number, or `null` for anything that is not one. */
function priced(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value.trim() === '') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function openCodeModel(model: CatalogModel, currency: string): OpenCodeModel {
  const entry: OpenCodeModel = { name: `${model.name} via AIRA` };

  // Both or neither: `limit` is a pair, and a missing `context` would be a gauge at 0% forever.
  const context = model.context_window ?? null;
  const output = model.max_output_tokens ?? null;
  if (context !== null && output !== null) {
    entry.limit = { context, output };
  }

  // Per 1,000,000 tokens on both sides, so no arithmetic. Only in OpenCode's currency, and never
  // converted: an exchange rate needs a date per booking, and absent (no cost shown) is honest.
  const input = priced(model.input_price_per_million);
  const out = priced(model.output_price_per_million);
  if (input !== null && out !== null && currency.toUpperCase() === OPENCODE_CURRENCY) {
    entry.cost = { input, output: out };
  }
  return entry;
}
