import { CatalogModel, GatewayProvider, ModelCheck } from '../../core/api/models';

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
 * none. `underlying_model` and the raw `addressing` block are deliberately absent — printed beside
 * Provider and Platform they read as configuration, and neither is a field the editor offers
 * (`test_every_model_control_is_reachable.py`).
 */
export function detailOf(model: CatalogModel): DetailField[] {
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
      label: 'Input $ / 1M',
      value: model.is_priced ? dash(model.input_price_per_million) : 'no price',
    },
    {
      key: 'output_price',
      label: 'Output $ / 1M',
      value: model.is_priced ? dash(model.output_price_per_million) : 'no price',
    },
    { key: 'numeric_id', label: 'KIRA id', value: dash(model.numeric_id) },
    {
      key: 'approved',
      label: 'Approved for use',
      value: model.approved ? 'yes' : 'no — a use case cannot call it',
    },
    { key: 'deprecated', label: 'Deprecated', value: model.deprecated ? 'yes' : 'no' },
    { key: 'updated_at', label: 'Last changed', value: dash(model.updated_at) },
  ];
}
