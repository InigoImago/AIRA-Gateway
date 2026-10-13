import { formatDate } from '@angular/common';
import { perMillion } from '../../core/api/me.service';
import { CatalogModel, GatewayProvider, ModelCheck, readRegions } from '../../core/api/models';

/** How a moment is written in this console's tables — the `date` pipe's pattern, in code. */
const WHEN = 'yyyy-MM-dd HH:mm';

/** One labelled fact in a model's detail panel. */
export interface DetailField {
  key: string;
  label: string;
  value: string;
}

/** What a reachability verdict means, in a sentence rather than three booleans (`FRD-506`). */
export function checkVerdict(verdict: ModelCheck): string {
  if (!verdict.served) return 'Declared, but nothing serves it';
  if (verdict.reachable === null) return 'Served — not contacted';
  return verdict.reachable ? 'Reachable' : 'Not reachable';
}

/**
 * A provider as a line somebody chooses from.
 *
 * The **label** says which vendor it is; the **name** follows in brackets because it is the string
 * written into the catalog and onto every audit row.
 */
export function providerLabel(provider: GatewayProvider): string {
  const where = provider.region ? ` · ${provider.region}` : '';
  return provider.label && provider.label !== provider.name
    ? `${provider.label} (${provider.name})${where}`
    : `${provider.name}${where}`;
}

/**
 * Every field of a declaration, in reading order, as label/value pairs.
 *
 * Built here rather than in the template so the list is **exhaustive by construction**: the catalog
 * is what the gateway enforces, and a partial answer to "what does this row say" is worse than
 * none. The spec holds it to every key of `CatalogModel`. Deliberately absent: `underlying_model`,
 * which nothing reads (`test_every_model_control_is_reachable.py`), and any `addressing` key besides
 * the regions — printed beside Provider and Platform they read as configuration, and no control
 * offers them. The regions are shown: they are where the model is addressed and its residency claim.
 */
export function detailOf(model: CatalogModel, currency = ''): DetailField[] {
  const dash = (value: unknown): string =>
    value === null || value === undefined || value === '' ? '—' : String(value);
  const json = (value: unknown): string =>
    value === null || value === undefined ? '—' : JSON.stringify(value);
  return [
    { key: 'display_name', label: 'Display name', value: dash(model.display_name) },
    { key: 'provider', label: 'Provider', value: dash(model.provider) },
    { key: 'publisher', label: 'Dialect (publisher)', value: dash(model.publisher) },
    { key: 'platform', label: 'Platform', value: dash(model.platform) },
    { key: 'hosting', label: 'Hosting', value: dash(model.hosting) },
    {
      key: 'regions',
      label: 'Regions (in order)',
      value: readRegions(model.addressing).join(', ') || '—',
    },
    {
      key: 'capabilities',
      label: 'Capabilities',
      value: model.is_declared ? (model.capabilities ?? []).join(', ') || '—' : 'undeclared',
    },
    { key: 'context_window', label: 'Context window', value: dash(model.context_window) },
    { key: 'max_output_tokens', label: 'Output cap', value: dash(model.max_output_tokens) },
    {
      key: 'default_max_output_tokens',
      label: 'Default output cap',
      value: dash(model.default_max_output_tokens),
    },
    { key: 'thinking', label: 'Thinking', value: json(model.thinking) },
    { key: 'embedding', label: 'Embedding', value: json(model.embedding) },
    { key: 'attachments', label: 'Attachments', value: json(model.attachments) },
    {
      key: 'input_price',
      label: `Input ${perMillion(currency)}`,
      value: model.is_priced ? dash(model.input_price_per_million) : 'no price',
    },
    {
      key: 'output_price',
      label: `Output ${perMillion(currency)}`,
      value: model.is_priced ? dash(model.output_price_per_million) : 'no price',
    },
    {
      key: 'cached_input_price',
      label: `Cached input ${perMillion(currency)}`,
      value: dash(model.cached_input_price_per_million),
    },
    {
      key: 'cache_write_price',
      label: `Cache write ${perMillion(currency)}`,
      value: dash(model.cache_write_price_per_million),
    },
    { key: 'numeric_id', label: 'KIRA id', value: dash(model.numeric_id) },
    {
      key: 'approved',
      label: 'Approved for use',
      value: model.approved ? 'yes' : 'no — a use case cannot call it',
    },
    { key: 'deprecated', label: 'Deprecated', value: model.deprecated ? 'yes' : 'no' },
    {
      key: 'updated_at',
      label: 'Last changed',
      value: model.updated_at ? formatDate(model.updated_at, WHEN, 'en-US') : '—',
    },
  ];
}
