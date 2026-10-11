import { CatalogModel } from '../../core/api/models';
import { openCodeModel } from './opencode-config';

/**
 * The OpenCode model block (`FRD-132` §11).
 *
 * The tests worth having are all about **absence**, because that is where the defect was: OpenCode
 * fills a missing `limit` with `0`, a context gauge is `used / limit.context`, and `0` is not
 * "unknown" on screen — it is a gauge that never moves. So the question at every branch is whether
 * a figure the catalog does not have stays out of the file.
 *
 * **The price is a different question from the limit** (§11.7): a price the catalog *has* goes in
 * whatever the currency, because with no `cost` block OpenCode prints `$0.00` for every session —
 * a wrong figure this file would be the author of. Proven by putting the old rule back (write
 * `cost` only for `USD`) and watching two of these go red.
 */

function model(over: Partial<CatalogModel> = {}): CatalogModel {
  return {
    name: 'qwen3:0.6b',
    context_window: 40960,
    max_output_tokens: 4096,
    input_price_per_million: '0.10',
    output_price_per_million: '0.40',
    ...over,
  };
}

describe('openCodeModel', () => {
  it('carries the limits and the prices a client cannot ask the API for', () => {
    expect(openCodeModel(model(), 'USD')).toEqual({
      name: 'qwen3:0.6b via AIRA',
      limit: { context: 40960, output: 4096 },
      cost: { input: 0.1, output: 0.4 },
    });
  });

  it('omits the limits entirely when the context window is unknown', () => {
    // Not `{context: 0, output: 4096}`: half a limit is a gauge stuck at 0% that looks measured.
    const entry = openCodeModel(model({ context_window: null }), 'USD');

    expect(entry.limit).toBeUndefined();
    expect(entry.name).toBe('qwen3:0.6b via AIRA');
  });

  it('omits the limits when the output cap is unknown', () => {
    expect(openCodeModel(model({ max_output_tokens: null }), 'USD').limit).toBeUndefined();
  });

  it('omits the prices when a model is unpriced', () => {
    // An invented price is worse than an absent one (`FRD-403`), and this is the same sentence one
    // file along: a coding assistant showing a running cost of zero for a model that bills is a
    // number somebody budgets against.
    expect(openCodeModel(model({ input_price_per_million: null }), 'USD').cost).toBeUndefined();
    expect(
      openCodeModel(model({ output_price_per_million: undefined }), 'USD').cost,
    ).toBeUndefined();
  });

  it('treats an empty price string as no price, not as free', () => {
    expect(openCodeModel(model({ input_price_per_million: '   ' }), 'USD').cost).toBeUndefined();
  });

  it('refuses a price that is not a number rather than writing NaN into the file', () => {
    // `Number('cheap')` is `NaN`, and `JSON.stringify({cost: NaN})` writes `null` — a config file
    // that parses and means nothing.
    expect(openCodeModel(model({ output_price_per_million: 'cheap' }), 'USD').cost).toBeUndefined();
  });

  it('writes the price in another currency and says which, in the name', () => {
    // **This test asserted the opposite until 2026-10-11**, and its reasoning was half right:
    // OpenCode prints the total with a `$`, and converting needs a rate per booking. What it got
    // wrong is what withholding costs — an installation in euros got no `cost` at all, so
    // OpenCode showed `$0.00` for every session, which is precisely the figure a developer was
    // asking for. Measured against a live stack: `opencode stats` reported `Total Cost $0.00`
    // after a real run that AIRA had priced at 0.0002.
    //
    // So the figures go in, unconverted, and the currency travels in the one field this file
    // controls and OpenCode displays.
    const entry = openCodeModel(model(), 'EUR');

    expect(entry.cost).toEqual({ input: 0.1, output: 0.4 });
    expect(entry.name).toBe('qwen3:0.6b via AIRA (EUR)');
    // The limits are not money and are unaffected: a context window is a count of tokens.
    expect(entry.limit).toEqual({ context: 40960, output: 4096 });
  });

  it('adds no currency to the name where OpenCode already assumes it', () => {
    // A label that repeats the obvious is noise in a model picker; `$` and USD agree.
    expect(openCodeModel(model(), 'USD').name).toBe('qwen3:0.6b via AIRA');
  });

  it('adds no currency to the name of a model with no price', () => {
    // Nothing to label: `(EUR)` beside a model whose cost OpenCode cannot show says nothing.
    const entry = openCodeModel(model({ input_price_per_million: null }), 'EUR');

    expect(entry.cost).toBeUndefined();
    expect(entry.name).toBe('qwen3:0.6b via AIRA');
  });

  it('does not care how the currency is spelled', () => {
    expect(openCodeModel(model(), 'usd').cost).toEqual({ input: 0.1, output: 0.4 });
  });

  it('writes the price before the console has been told the currency, and labels nothing', () => {
    // The currency arrives with the first `/v1/me`, and the file may be generated before it does.
    // The price is a fact of the catalogue either way; what is not known is what to call it, so
    // nothing is called anything — rather than guessing "probably dollars" in the name.
    const entry = openCodeModel(model(), '');

    expect(entry.cost).toEqual({ input: 0.1, output: 0.4 });
    expect(entry.name).toBe('qwen3:0.6b via AIRA');
  });

  it('keeps a price of zero, which is a price', () => {
    // A locally hosted model genuinely costs nothing per token. Dropping it would be the same
    // mistake as writing zero for unknown, in the other direction.
    expect(
      openCodeModel(model({ input_price_per_million: '0', output_price_per_million: '0' }), 'USD')
        .cost,
    ).toEqual({ input: 0, output: 0 });
  });
});
