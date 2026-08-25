import { CatalogModel } from '../../core/api/models';
import { openCodeModel } from './opencode-config';

/**
 * The OpenCode model block (`FRD-132` §11).
 *
 * The tests worth having are all about **absence**, because that is where the defect was: OpenCode
 * fills a missing `limit` with `0`, a context gauge is `used / limit.context`, and `0` is not
 * "unknown" on screen — it is a gauge that never moves. So the question at every branch is whether
 * a figure the catalog does not have stays out of the file.
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
    expect(openCodeModel(model())).toEqual({
      name: 'qwen3:0.6b via AIRA',
      limit: { context: 40960, output: 4096 },
      cost: { input: 0.1, output: 0.4 },
    });
  });

  it('omits the limits entirely when the context window is unknown', () => {
    // Not `{context: 0, output: 4096}`. Half a limit is a gauge stuck at 0%, which is the exact
    // thing reported — and worse than no gauge, because it looks like a measurement.
    const entry = openCodeModel(model({ context_window: null }));

    expect(entry.limit).toBeUndefined();
    expect(entry.name).toBe('qwen3:0.6b via AIRA');
  });

  it('omits the limits when the output cap is unknown', () => {
    expect(openCodeModel(model({ max_output_tokens: null })).limit).toBeUndefined();
  });

  it('omits the prices when a model is unpriced', () => {
    // An invented price is worse than an absent one (`FRD-403`), and this is the same sentence one
    // file along: a coding assistant showing a running cost of zero for a model that bills is a
    // number somebody budgets against.
    expect(openCodeModel(model({ input_price_per_million: null })).cost).toBeUndefined();
    expect(openCodeModel(model({ output_price_per_million: undefined })).cost).toBeUndefined();
  });

  it('treats an empty price string as no price, not as free', () => {
    expect(openCodeModel(model({ input_price_per_million: '   ' })).cost).toBeUndefined();
  });

  it('refuses a price that is not a number rather than writing NaN into the file', () => {
    // `Number('cheap')` is `NaN`, and `JSON.stringify({cost: NaN})` writes `null` — a config file
    // that parses and means nothing.
    expect(openCodeModel(model({ output_price_per_million: 'cheap' })).cost).toBeUndefined();
  });

  it('keeps a price of zero, which is a price', () => {
    // A locally hosted model genuinely costs nothing per token. Dropping it would be the same
    // mistake as writing zero for unknown, in the other direction.
    expect(
      openCodeModel(model({ input_price_per_million: '0', output_price_per_million: '0' })).cost,
    ).toEqual({ input: 0, output: 0 });
  });
});
