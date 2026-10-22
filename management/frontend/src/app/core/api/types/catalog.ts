/**
 * The model catalogue and what the gateway says about the models it reaches.
 *
 * The three vocabularies below restate Python enums because a checkbox list has to come from
 * somewhere. `libs/tests/test_capability_vocabulary.py` compares each against its enum **in both
 * directions**: a value the console cannot express looks exactly like a design decision.
 */

/** What a model may be asked to do (`FRD-114`, `ADR-0012`). Flags say *whether*, never *how*. */
export type Capability =
  | 'generate'
  | 'embed'
  | 'structured_output'
  | 'thinking'
  | 'attachments'
  | 'tools'
  | 'prompt_caching'
  | 'speech'
  | 'rerank';

/** A model's sampling defaults (`FRD-146`): either, both, or none. */
export interface SamplingDefaults {
  top_p?: number;
  top_k?: number;
}

/** The closed capability vocabulary — `aira_common.models.Capability`. */
export const CAPABILITIES: readonly Capability[] = [
  'generate',
  'embed',
  'structured_output',
  'thinking',
  'attachments',
  'tools',
  'prompt_caching',
  'speech',
  'rerank',
];

/**
 * The three thinking settings the **gateway** owns (`FRD-111`, `ADR-0021`).
 *
 * Level words (`low`, `high`, …) are not here: they are the vendors' own words, declared per model
 * as free text and checked against that model, because a closed list cannot hold a vendor's next one.
 */
export type ThinkingModeName = 'disabled' | 'limited' | 'auto';

export const THINKING_MODES: readonly ThinkingModeName[] = ['disabled', 'limited', 'auto'];

/**
 * What a model may be declared to read (`FRD-110`).
 *
 * The gateway's `DEFAULT_MEDIA_TYPES` is the outer bound — a type outside it is refused before any
 * model is consulted — so offering more here would offer something that cannot work.
 */
export const MEDIA_TYPES: readonly string[] = [
  'application/pdf',
  'application/x-javascript',
  'text/javascript',
  'text/plain',
  'text/html',
  'text/md',
  'text/csv',
  'text/xml',
  'text/rtf',
  'image/png',
  'image/jpg',
  'image/jpeg',
  'image/webp',
  'image/heic',
  'image/heif',
];

/** A model's thinking declaration (`FRD-114` FR-3, `ADR-0021`). */
export interface ThinkingDeclaration {
  /** The gateway's own three, which every dialect spells differently. A closed set. */
  modes: ThinkingModeName[];
  /** Bounds for `limited` — the one mode where the **caller** names a number. */
  min_tokens?: number | null;
  max_tokens?: number | null;
  /** A mode or a level word: whatever this model does when the caller says nothing. */
  default?: { mode: string; tokens?: number | null } | null;
  /**
   * The **vendor's own** level words this model accepts, free text. No token budget per level: no
   * vendor publishes one, and a guessed number would cap the model's reasoning.
   */
  levels?: string[] | null;
}

/** An embedding model's shape (`FRD-113`). */
export interface EmbeddingDeclaration {
  task_types?: string[] | null;
  supports_batch?: boolean | null;
  dimensions?: number[] | null;
  default?: number | null;
}

/** Which media types a model reads, and what each costs to send (`FRD-110`). */
export interface AttachmentDeclaration {
  media_types?: Record<string, { tokens?: number } | null> | null;
}

/** A model in the catalog: what it costs, what it can do, how it is reached (FRD-403, FRD-114). */
export interface CatalogModel {
  name: string;
  display_name?: string;
  /** Released for use by a Global Administrator (`FRD-307`). Only an approved model may be
   *  called by a use case; an *undeclared* model is not gated by this. */
  approved?: boolean;
  provider?: string;
  input_price_per_million?: string | null;
  /** What a cache read and a cache write cost (FRD-133). Absent means "charge the ordinary input
   *  rate" — never "free", so an undeclared rate can only over-state a bill. */
  cached_input_price_per_million?: string | null;
  cache_write_price_per_million?: string | null;
  output_price_per_million?: string | null;
  is_priced?: boolean;
  updated_at?: string;

  capabilities?: Capability[];
  /** Which vendor's API shape it speaks — selects the upstream dialect (ADR-0011). */
  publisher?: string;
  /** Which transport reaches it (`vertex`, `foundry`, …). */
  platform?: string;
  /** Platform addressing — the model's regions (`FRD-609`). Read it through `readRegions`. */
  addressing?: Record<string, unknown> | null;
  /** What the price attaches to when the caller-facing name is not the vendor's. */
  underlying_model?: string;
  /**
   * What the model can hold at once, prompt and answer together.
   *
   * Not enforced by AIRA — the upstream refuses what does not fit. Published as the Gemini model
   * resource's `inputTokenLimit`, because a client has nowhere else to learn it.
   */
  context_window?: number | null;
  max_output_tokens?: number | null;
  /** Applied when the caller sets no cap — Anthropic requires one on every request. */
  default_max_output_tokens?: number | null;
  thinking?: ThinkingDeclaration | null;
  embedding?: EmbeddingDeclaration | null;
  attachments?: AttachmentDeclaration | null;
  hosting?: '' | 'managed' | 'self_deployed';
  /** The model samples at its own settings: a request setting temperature, top_p or top_k to
   *  anything but the neutral value is never sent to it. Absent or false is no restriction. */
  fixed_sampling?: boolean;
  /** Sent where a caller sets that control not at all; absent or null leaves the model's own. */
  default_sampling?: SamplingDefaults | null;
  /** Warns, never blocks — blocking is what revocation is for. */
  deprecated?: boolean;
  numeric_id?: number | null;
  /** Whether anyone has said what this model can do. Undeclared means the baseline only. */
  is_declared?: boolean;
}

/**
 * Whether a declared model can actually be reached (`FRD-506`).
 *
 * Three facts, never collapsed: `declared` proves nothing about reachability; `served` says an
 * adapter exists (what a missing credential fails); `reachable` is `null` when nothing was
 * contacted — "we did not look" and "it is fine" are different answers.
 */
export interface ModelCheck {
  model: string;
  declared: boolean;
  served: boolean;
  /** The **best** of the regions below: a model that answers in one of its places is reachable. */
  reachable: boolean | null;
  detail: string;
  /**
   * The cause the provider or the connection stated (`FRD-141`): a word from a closed list, and
   * what to do about it. `null` where it stated none — or where nothing failed.
   */
  reason?: string | null;
  explanation?: string | null;
  /** One verdict per declared region (`FRD-609`); a single empty-named entry where none is. */
  regions?: {
    region: string;
    reachable: boolean;
    detail: string;
    reason?: string | null;
    explanation?: string | null;
  }[];
}

/** One cause of a failed check, and the regions it was given for. */
export interface CheckCause {
  reason: string;
  explanation: string;
  regions: string[];
}

/** What the model itself said about each declared level word (`ADR-0021`). */
export interface ThinkingLevelCheck {
  model: string;
  /** One row per region **and** word: which places accept which is not knowable in advance. */
  results: { region: string; level: string; accepted: boolean; detail: string }[];
  /**
   * One row per **mode** asked about. No region and no request: whether a wire format has a field
   * for a mode is a fact about the dialect, not about the model or where it runs.
   */
  modes?: { mode: string; accepted: boolean; detail: string }[];
}

/**
 * An upstream this installation is configured with (`FRD-507` stage C).
 *
 * - `canEnumerate` — whether this platform can be **asked** what it offers. One that cannot is not
 *   broken, and must not be reported as a fault.
 * - `cataloguedIsEnough` — whether the model name alone reaches the model, or whether it needs more:
 *   Azure a deployment named in the gateway's configuration, Vertex a region on the catalogue
 *   entry. True exactly where the model name is the whole addressing.
 */
export interface GatewayProvider {
  /** An identifier — written into the catalog, onto every audit row and into routing. */
  name: string;
  /** What to call it on screen (`Google AI Studio`), supplied by the adapter so the console holds
   *  no second vocabulary. */
  label: string;
  publisher: string;
  region: string;
  canEnumerate: boolean;
  cataloguedIsEnough: boolean;
  servedModels: number;
  adapters: number;
}

/**
 * What the gateway answers about its own configuration (`FRD-507` stage C, `ADR-0012` §6).
 *
 * The regions travel with the providers because the editor needs both at once, and because
 * residency is the **gateway's** policy (`AIRA_ALLOWED_REGIONS`): a copy in Management would be a
 * second answer to a question that must have one.
 */
export interface GatewayConfiguration {
  providers: GatewayProvider[];
  /** Empty means *the gateway did not say* — an older one — never *nothing is allowed*. */
  allowedRegions: string[];
  /** Regions single models are permitted **by name**, beyond `allowedRegions`
   *  (`AIRA_ALLOWED_MODEL_REGIONS`): model name → its further regions. */
  modelRegions: Record<string, string[]>;
}

/**
 * A model the **gateway** serves, as its own listing reports it (`FRD-507`).
 *
 * Deliberately not shaped like a catalog entry: it carries where the model is reached and whether
 * the catalog knows it, never price or capability — those are a decision and a measurement.
 */
export interface ServedModel {
  name: string;
  airaDeclared?: boolean;
  airaProvider?: string | null;
  airaPublisher?: string | null;
  airaRegion?: string | null;
}

/**
 * A model a **vendor** says this installation's credential can reach.
 *
 * Every capability is `boolean | null`, and `null` is a third answer: the vendor did not say.
 * Collapsing it into `false` would pre-fill a form with a declaration nobody made (`FRD-114` FR-7).
 */
export interface OfferedModel {
  name: string;
  displayName: string;
  description: string;
  maxOutputTokens: number | null;
  canGenerate: boolean | null;
  canEmbed: boolean | null;
  canCachePrompts: boolean | null;
  thinking: boolean | null;
}

/**
 * A model as the **compatibility surface** addresses it (`FRD-107`): by integer, which is what a
 * migrating client's configuration holds.
 *
 * Read from the gateway rather than the catalog: the catalog is readable only with `catalog.write`,
 * and the use-case administrator setting a client up does not hold it.
 */
export interface KiraModel {
  id: number;
  name: string;
  capabilities: string[];
  deprecated?: boolean;
  max_output_tokens?: number | null;
}

/**
 * A model's regions, from either spelling (`FRD-609`): `{regions: [...]}`, or the older
 * `{region: "x"}` that rows written before a model could name several still carry.
 *
 * The gateway has the same reader (`ModelDeclaration.regions`), so the two planes cannot come to
 * read a spelling differently.
 */
export function readRegions(addressing: Record<string, unknown> | null | undefined): string[] {
  const block = addressing ?? {};
  const raw = block['regions'] ?? (typeof block['region'] === 'string' ? [block['region']] : []);
  const list = Array.isArray(raw) ? raw : [raw];
  const seen: string[] = [];
  for (const entry of list) {
    const region = typeof entry === 'string' ? entry.trim() : '';
    if (region && !seen.includes(region)) seen.push(region);
  }
  return seen;
}

/**
 * A draft of a catalog entry from a public model list (`FRD-508`): the editor's fields in this
 * catalog's vocabulary. A third party's claim, which is why it travels with its source and date.
 */
export interface ReferenceEntry {
  /** The provider name the gateway stamps on the model (`vertex`, `generative-language`). */
  platform: string;
  name: string;
  display_name: string;
  publisher: string;
  family: string;
  /** `deprecated`, `beta`, `alpha`, or empty for a current model. */
  status: string;
  release_date: string;
  knowledge: string;
  description: string;
  declaration: {
    capabilities: Capability[];
    context_window: number | null;
    max_output_tokens: number | null;
    thinking: ThinkingDeclaration | null;
    attachments: { media_types: Record<string, { tokens?: number } | null> } | null;
    /** Set where the list says the model takes no temperature at all. */
    fixed_sampling: boolean;
  };
  /** Per million tokens, in the reference's currency (US dollars), as decimal strings. */
  prices: {
    input: string | null;
    output: string | null;
    cached_input: string | null;
    cache_write: string | null;
  };
  notes: string[];
}

/** The reference as Management answers it: the drafts, and where and when they come from. */
export interface ModelReference {
  source: string;
  as_of: string;
  currency: string;
  /** The installation's rate for the list's prices (`FRD-509`), or `null` where none is set or
   *  the installation counts in the list's own currency. */
  conversion?: { from: string; to: string; rate: string } | null;
  /** Why a configured live list was not used; `null` when it was, or when none is configured. */
  live_error: string | null;
  entries: ReferenceEntry[];
}

/** The four price fields of a catalog entry, as both the reference and the billing lookup name them. */
export type PriceField = 'input' | 'output' | 'cached_input' | 'cache_write';

/** One price from the billing account, and the line of the bill it is (`FRD-509`). */
export interface BillingPrice {
  perMillion: string;
  currency: string;
  skuId: string;
  skuName: string;
  region: string;
  /** A contract price applied, rather than the list price. */
  contract: boolean;
}

/** What a model costs this installation, as the gateway read it from the billing account. */
export interface BillingPrices {
  model: string;
  region: string;
  billingAccount: string;
  currency: string;
  prices: Partial<Record<PriceField, BillingPrice>>;
  /** Why a field has no price: no SKU names it, or its SKU is not priced per token. */
  unpriced: Partial<Record<PriceField, string>>;
}
