import { CatalogModel, ThinkingDeclaration } from '../../core/api/models';

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
  cost?: OpenCodeCost;
  variants?: Record<string, OpenCodeVariant>;
}

/**
 * What OpenCode multiplies its token counts by, per 1,000,000 tokens — both generations read these
 * keys (`FRD-408`).
 *
 * **Every rate the gateway charges goes in.** OpenCode prices a rate it is not given at zero: it
 * subtracts the cached tokens from the input and multiplies them by `cache_read`, so a file with
 * only `input` and `output` showed every cache hit as free — in a long agentic session most of the
 * prompt. The rates are the gateway's own (`pricing.Rates`): a cache price the catalogue states,
 * else a read at the input price and a write at 1.25 times it.
 */
export interface OpenCodeCost extends OpenCodeRates {
  /** The rates above 200,000 prompt tokens — the one length OpenCode's file can state. */
  context_over_200k?: OpenCodeRates;
}

export interface OpenCodeRates {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

/** The one prompt length OpenCode's configuration can carry a second set of prices for. */
const OPENCODE_LONG_CONTEXT = 200_000;

/** A write at 1.25 times the input price where the catalogue states none — the gateway's rule
 *  (`pricing.CACHE_WRITE_NUMERATOR`), so OpenCode adds up what the gateway charges. */
const CACHE_WRITE_FACTOR = 1.25;

/**
 * The level words OpenCode can send, least thinking first. Its Google client refuses any other
 * `thinkingLevel` before a request leaves the workstation, so a model's `xhigh` or `max` gets no
 * variant: one OpenCode cannot send would fail every turn it is chosen for.
 */
const OPENCODE_LEVELS: readonly string[] = ['minimal', 'low', 'medium', 'high'];

/**
 * One thinking setting OpenCode offers for a model (`FRD-151`), as the Gemini field it sends —
 * which both OpenCode generations read from this shape.
 *
 * **No `includeThoughts`.** The gateway asks for the reasoning itself wherever the use case
 * returns it; written here, it would be refused by name on a use case that does not, and the file
 * would break the day somebody turned reasoning off.
 */
export interface OpenCodeVariant {
  thinkingConfig: { thinkingLevel?: string; thinkingBudget?: number };
}

/**
 * The thinking settings the catalogue declares for a model, as the variants OpenCode lets a
 * developer choose between (`ctrl+t`, `--variant`), or nothing where it declares none.
 *
 * **Least thinking first**: off, then the levels, then the model deciding. The order is not
 * cosmetic — OpenCode 1 sends the *first* variant with its own small requests, a session's title
 * among them, so the cheapest setting the model takes belongs there. `limited` gets no variant: a
 * budget needs a number, the only ones a vendor states are its bounds, and its ceiling does not fit
 * inside the output cap OpenCode sends with every request. A request with no variant chosen gets
 * the model's declared default, which is where such a model's thinking is set.
 */
export function openCodeVariants(
  thinking: ThinkingDeclaration | null | undefined,
): Record<string, OpenCodeVariant> | undefined {
  const modes = thinking?.modes ?? [];
  const levels = thinking?.levels ?? [];
  const variants: Record<string, OpenCodeVariant> = {};
  if (modes.includes('disabled')) {
    variants['none'] = { thinkingConfig: { thinkingBudget: 0 } };
  }
  for (const level of OPENCODE_LEVELS) {
    if (levels.includes(level)) {
      variants[level] = { thinkingConfig: { thinkingLevel: level } };
    }
  }
  if (modes.includes('auto')) {
    variants['auto'] = { thinkingConfig: { thinkingBudget: -1 } };
  }
  return Object.keys(variants).length > 0 ? variants : undefined;
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

/** One set of rates as OpenCode reads it, an absent cache price filled the way the gateway fills
 *  it — never left out, which OpenCode would read as free. */
function rates(
  input: number,
  output: number,
  cached: number | null,
  write: number | null,
): OpenCodeRates {
  return {
    input,
    output,
    cache_read: cached ?? input,
    cache_write: write ?? roundMicro(input * CACHE_WRITE_FACTOR),
  };
}

/** A price to the catalogue's six places, so `1.25 * 0.3` is written `0.375` and not
 *  `0.37500000000000006`. */
function roundMicro(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}

/**
 * The rates above 200,000 prompt tokens, where the catalogue states that step (`FRD-408`).
 * Another length — some Claude models step up at 100,000 — has no key in OpenCode's file, so the
 * model gets none and OpenCode under-states requests past that length; the gateway's own figures,
 * and the budget plugin that shows them, do not.
 */
function longContext(model: CatalogModel): OpenCodeRates | null {
  if (model.long_context_threshold !== OPENCODE_LONG_CONTEXT) return null;
  const input = priced(model.long_context_input_price_per_million);
  const output = priced(model.long_context_output_price_per_million);
  if (input === null || output === null) return null;
  return rates(
    input,
    output,
    priced(model.long_context_cached_input_price_per_million),
    priced(model.long_context_cache_write_price_per_million),
  );
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
    entry.cost = rates(
      input as number,
      out as number,
      priced(model.cached_input_price_per_million),
      priced(model.cache_write_price_per_million),
    );
    const longer = longContext(model);
    if (longer) {
      entry.cost.context_over_200k = longer;
    }
  }
  const variants = openCodeVariants(model.thinking);
  if (variants) {
    entry.variants = variants;
  }
  return entry;
}
