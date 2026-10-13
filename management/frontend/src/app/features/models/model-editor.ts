import { Component, computed, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import {
  CAPABILITIES,
  MEDIA_TYPES,
  THINKING_MODES,
  readRegions,
  AttachmentDeclaration,
  Capability,
  CatalogModel,
  EmbeddingDeclaration,
  GatewayProvider,
  ModelCheck,
  OfferedModel,
  ServedModel,
  ThinkingDeclaration,
  ThinkingModeName,
} from '../../core/api/models';
import { MeService, perMillion } from '../../core/api/me.service';
import { Provenance, UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { checkVerdict, providerLabel } from './catalog-display';
import { CAPABILITY_HELP, thinkingModeMeans } from './declaration-help';
import { GatewayProviders } from './gateway-providers';

/** An amount as people type it: "0.075", "10", "10,50". Kept as text end to end. */
const AMOUNT = /^\d+([.,]\d+)?$/;

/** Digits a price may carry on either side of the point: the catalog stores `DecimalField(12, 6)`,
 *  so a seventh decimal or a seventh whole digit is refused by the server — said here, by field. */
const PRICE_DIGITS = 6;

/** A width as the embedding field takes it: a whole number, nothing else. */
const WIDTH = /^\d+$/;

/** What the model or the gateway answered about one word, mode or region. */
type Verdict = { ok: boolean; detail: string };

/** Where a model lives, as an entrance knows it — the four facts an import may copy (`FRD-507`). */
interface Whereabouts {
  provider: string;
  publisher: string;
  region: string;
  platform: string;
}

/** Why a typed price cannot be stored, naming the field and the limit — or `null` if it can. */
function priceProblem(label: string, value: string): string | null {
  if (!value) return null;
  if (!AMOUNT.test(value)) {
    return `${label}: '${value}' is not an amount. Prices are amounts per 1,000,000 tokens, e.g. 0.075.`;
  }
  const [whole, fraction = ''] = value.split(/[.,]/);
  if (whole.replace(/^0+(?=\d)/, '').length > PRICE_DIGITS) {
    return `${label}: at most ${PRICE_DIGITS} digits before the decimal point — the catalog stores up to 999999.999999.`;
  }
  if (fraction.length > PRICE_DIGITS) {
    return `${label}: at most ${PRICE_DIGITS} decimal places — '${value}' has ${fraction.length}.`;
  }
  return null;
}

/** A configured provider as the four facts it states about where its models live. */
function provenanceOf(upstream: GatewayProvider): Whereabouts {
  return {
    provider: upstream.name,
    publisher: upstream.publisher ?? '',
    region: upstream.region ?? '',
    platform: upstream.name,
  };
}

/**
 * The window a model is declared or corrected in (`FRD-114`, `FRD-403`, `FRD-507`).
 *
 * Owns the form — three tabs split by the question each field answers, with approval and
 * deprecation beside Save — its reachability and thinking checks, and the save. The catalog page
 * opens it through `add`, `edit`, `importServed`, `startOn` and `fromOffering`; outcomes go through
 * the page's `PageFeedback`.
 */
@Component({
  selector: 'app-model-editor',
  imports: [InfoHint, FormsModule, Modal],
  templateUrl: './model-editor.html',
  styleUrl: './model-editor.scss',
  host: { style: 'display: contents' },
})
export class ModelEditor {
  private readonly service = inject(UseCaseService);
  private readonly gateway = inject(GatewayProviders);
  private readonly currency = inject(MeService).currency;
  protected readonly perMillion = computed(() => perMillion(this.currency()));
  protected readonly feedback = inject(PageFeedback);

  /** Raised after a successful save, so the page reloads the catalog. */
  readonly saved = output<void>();

  /**
   * What the catalog already holds. A save is an upsert by name, so an "add" under a catalogued
   * name would replace that model's whole declaration — release, price, capabilities, KIRA id —
   * with the empty form's. Adding refuses such a name and offers the declaration on file instead.
   */
  readonly catalogued = input<readonly CatalogModel[]>([]);

  /**
   * Whether the reader may ask the gateway about a model (`operations.diagnose`). The reachability
   * check and "Ask the model" answer 403 without it, so they are offered only with it — and adding
   * a model no longer waits on a check its author cannot run. Off until said otherwise: a button
   * that can only fail is worse than one that is absent.
   */
  readonly canDiagnose = input(false);

  // ---- the window ---------------------------------------------------------------------------

  readonly showAdd = signal(false);
  /** The model id being corrected, or '' when adding a new one. Also the window's title. */
  readonly editing = signal('');
  /** Which third of the declaration is on screen. The values live in signals, so switching tabs
   *  keeps everything typed; only the DOM comes and goes. */
  protected readonly editorTab = signal<'identity' | 'capabilities' | 'price'>('identity');
  /** The window's heading: which model it is about, or that it adds one. */
  protected readonly title = computed(() =>
    this.editing() ? `Edit ${this.editing()}` : 'Add a model',
  );

  // ---- what it is ---------------------------------------------------------------------------

  protected readonly name = signal('');
  protected readonly displayName = signal('');
  protected readonly provider = signal('');
  protected readonly publisher = signal('');
  protected readonly platform = signal('');
  protected readonly hosting = signal<'' | 'managed' | 'self_deployed'>('');
  /**
   * The integer a KIRA client addresses this model by (`FRD-107`). Blank does not mean "none": the
   * server assigns the next free number. Worth setting when migrating clients already send an id.
   */
  protected readonly kiraId = signal<number | null>(null);
  /** The id the model holds on file. The server assigns one only when a model is created, so an
   *  edit that clears it stores nothing and the model stops answering on the KIRA surface. */
  private readonly storedKiraId = signal<number | null>(null);
  /**
   * Where this model lives, **in the order it should be tried** (`FRD-609`): the first is where an
   * ordinary request goes, the rest are what the gateway falls back to. Chips, so each entry is
   * separately removable and separately answerable when the model is checked.
   */
  protected readonly regions = signal<string[]>([]);
  /** What is in the box but not yet a chip. Enter, comma or blur commits it. */
  protected readonly regionDraft = signal('');
  /** Region → what the gateway said when asked. Empty until the check has run. */
  protected readonly regionVerdicts = signal<Record<string, Verdict>>({});
  /** The addressing block as stored. The form edits its regions only; every other key — a
   *  deployment, an endpoint id — written through the API or a seed is carried through an edit. */
  private readonly storedAddressing = signal<Record<string, unknown> | null>(null);

  /**
   * Whether the provenance fields are open, rather than summarised in a sentence.
   *
   * Provider, publisher, platform, hosting and KIRA id are answered when the provider is chosen,
   * and an empty box beside a fact the software already knows reads as "yours to decide". So they
   * are stated, opened on request — and open by themselves when nothing is known.
   */
  private readonly provenanceOpened = signal(false);
  /** Whether the software already knows where this model lives. One fact answers it: a provider. */
  protected readonly provenanceKnown = computed(() => !!this.provider().trim());
  protected readonly showProvenance = computed(
    () => this.provenanceOpened() || !this.provenanceKnown(),
  );

  /** The sentinel the provider select uses for "not one of these". */
  protected readonly OTHER = '__other__';
  /** Whether the provider is being typed rather than chosen. */
  protected readonly providerIsCustom = signal(false);
  protected readonly providers = this.gateway.list;
  protected readonly loadingProviders = this.gateway.loading;
  protected readonly providersError = this.gateway.error;
  protected readonly allowedRegions = this.gateway.allowedRegions;
  /**
   * Where **this** model may be processed: every model's regions, and those the gateway permits
   * it by name (`AIRA_ALLOWED_MODEL_REGIONS`) — a model served only at `global` is permitted there
   * alone, so the editor must not refuse what the gateway allows. Empty where the gateway did not
   * say.
   */
  protected readonly permittedRegions = computed<string[]>(() => {
    const allowed = this.allowedRegions();
    if (!allowed.length) return [];
    const byName = this.gateway.modelRegions()[this.name().trim()] ?? [];
    return [...allowed, ...byName.filter((region) => !allowed.includes(region))];
  });
  protected readonly selectedProvider = computed(() => this.gateway.find(this.provider()));

  /**
   * Whether the form was filled from a provider or a vendor's listing. The provenance sentence says
   * what is now known; this shows the note saying what the import deliberately left alone — the
   * price and the measured capabilities — so the two halves are each said once.
   */
  protected readonly imported = signal(false);
  /** What the vendor said that the console deliberately did **not** turn into a declaration. */
  protected readonly vendorSaid = signal<string[]>([]);

  // ---- what it can do -----------------------------------------------------------------------

  protected readonly contextWindow = signal<number | null>(null);
  protected readonly maxOutput = signal<number | null>(null);
  protected readonly defaultOutput = signal<number | null>(null);
  protected readonly allCapabilities = CAPABILITIES;
  protected readonly capabilities = signal<Capability[]>([]);

  // The three declaration blocks, held as flat signals: a form edits fields and the validator reads
  // a document, and `declarations()` is the one place the two shapes meet.
  protected readonly allThinkingModes = THINKING_MODES;
  protected readonly thinkingModes = signal<ThinkingModeName[]>([]);
  protected readonly thinkingMin = signal<number | null>(null);
  protected readonly thinkingMax = signal<number | null>(null);
  /** A mode or a level word — whichever the model does when the caller says nothing. */
  protected readonly thinkingDefault = signal<string>('');
  /**
   * The **vendor's own** level words this model accepts (`ADR-0021`), typed freely and checked
   * against the model. No token budget per level: no vendor publishes one, and a guess would go
   * upstream as a ceiling on the model's reasoning.
   */
  protected readonly thinkingLevels = signal<string[]>([]);
  /** What is in the box but not yet a chip. Enter, comma or blur commits it. */
  protected readonly levelDraft = signal('');
  /** Word → what the model said. Empty until asked: an unasked question is not a verdict
   *  (`FRD-506`). */
  protected readonly levelVerdicts = signal<Record<string, Verdict>>({});
  /** Mode → what the model's dialect said. Empty until asked. */
  protected readonly modeVerdicts = signal<Record<string, Verdict>>({});
  protected readonly checkingLevels = signal(false);
  /** Comma-separated, because a width list is short and typing it beats a repeater. */
  protected readonly dimensions = signal('');
  protected readonly defaultDimension = signal<'' | number>('');
  protected readonly taskTypes = signal('');
  protected readonly supportsBatch = signal(false);
  protected readonly allMediaTypes = MEDIA_TYPES;
  protected readonly mediaTypes = signal<string[]>([]);
  /** Per-type input-token estimates, carried through an edit rather than rebuilt from the
   *  checkboxes, so an estimate somebody measured is never silently dropped (`FRD-110` §5.3). */
  private readonly mediaTypeSpecs = signal<Record<string, { tokens?: number } | null>>({});

  // ---- what it costs ------------------------------------------------------------------------

  protected readonly inputPrice = signal('');
  protected readonly outputPrice = signal('');
  /** The two cache rates (`FRD-133`). Optional: without them cached tokens are charged the ordinary
   *  input rate — over-stating a bill, never under-stating it. */
  protected readonly cachedPrice = signal('');
  protected readonly cacheWritePrice = signal('');

  // ---- standing, beside Save ----------------------------------------------------------------

  /** Released for use (`FRD-307`). New declarations start **off**: a model appearing on an
   *  upstream is not the same event as somebody deciding it may be used here. */
  protected readonly approved = signal(false);
  protected readonly deprecated = signal(false);

  // ---- is it reachable? (`FRD-506`) ---------------------------------------------------------

  /** The verdict for the model in the form. Cleared whenever the form turns to another model. */
  protected readonly check = signal<ModelCheck | null>(null);
  protected readonly checking = signal(false);
  /** The name a reachability check has been **answered** for — answered, not passed. An error
   *  counts: a diagnostic that cannot answer must not lock anybody out of their own catalog. */
  protected readonly checkedName = signal<string | null>(null);
  /**
   * The two questions in flight, kept so they can be dropped. An answer belongs to the model and
   * provenance it was asked about; one landing after the window closed, or after the provider
   * changed, would otherwise mark another model's form with this one's verdict.
   */
  private checkRequest: Subscription | null = null;
  private levelsRequest: Subscription | null = null;

  protected readonly checkVerdict = checkVerdict;
  protected readonly providerLabel = providerLabel;
  protected readonly thinkingModeMeans = thinkingModeMeans;

  /** Everything a caller could ask this model for: the ticked modes, then the declared words. */
  protected readonly declarableDefaults = computed<string[]>(() => [
    ...this.thinkingModes(),
    ...this.thinkingLevels(),
  ]);

  /** The widths as numbers, for the default picker — so it can only offer a declared one. */
  protected readonly declaredDimensions = computed(() =>
    this.dimensions()
      .split(',')
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value) && value > 0),
  );

  /** What was typed into the widths and is not one — refused by name rather than dropped, or
   *  `768, 1O24` would be saved as `[768]` without a word. */
  protected readonly unreadableWidths = computed(() =>
    this.dimensions()
      .split(',')
      .map((value) => value.trim())
      .filter((value) => value && (!WIDTH.test(value) || Number(value) === 0)),
  );

  /** The catalog's model under the name typed into an **empty** form, if it has one. */
  protected readonly alreadyCatalogued = computed<CatalogModel | null>(() => {
    if (this.editing()) return null;
    const name = this.name().trim();
    return this.catalogued().find((model) => model.name === name) ?? null;
  });

  /** "Ask the model" has something to ask: a word, a draft about to become one, or a ticked mode —
   *  a model with modes and no levels is asked about its modes. */
  protected readonly canAskModel = computed(
    () =>
      !!this.name().trim() &&
      (this.thinkingLevels().length > 0 ||
        this.thinkingModes().length > 0 ||
        !!this.levelDraft().trim()),
  );

  /** The regions in the form that this installation does not permit. */
  protected readonly forbiddenRegions = computed<string[]>(() => {
    const permitted = this.permittedRegions();
    // Empty means the gateway did not say: reading it as an empty allow-list would refuse every
    // region anybody typed, enforcing a policy the console has never heard.
    if (!permitted.length) return [];
    return this.regions().filter((region) => !permitted.includes(region));
  });

  /**
   * The rest of the provenance sentence, assembled here rather than from template blocks: the
   * formatter puts each `@if` on its own line, and the text after it starts with a space.
   */
  protected readonly provenanceDetail = computed(() => {
    const parts: string[] = [];
    // The publisher, not a dialect: it selects one, and Vertex's open models from `zai-org` or
    // `deepseek-ai` all speak the OpenAI one (`FRD-115` FR-2a).
    if (this.publisher()) parts.push(`published by ${this.publisher()}`);
    if (this.platform()) parts.push(`on the ${this.platform()} platform`);
    if (this.hosting() === 'self_deployed') parts.push('self-deployed on our own capacity');
    return parts.length ? `, ${parts.join(', ')}.` : '.';
  });

  /** The provider as the sentence names it: its label where the gateway has one. Not
   *  `providerLabel()`, whose region is the provider's, not necessarily this model's. */
  protected readonly providerSummary = computed(() => {
    const upstream = this.selectedProvider();
    const name = this.provider().trim();
    return upstream?.label && upstream.label !== name ? `${upstream.label} (${name})` : name;
  });

  constructor() {
    // Settle the provider field when the gateway answers — in both directions, because a form
    // opened on a provider is laid out before the answer arrives. A provider the gateway does not
    // have stays typed: a model declared ahead of its credential, not an error.
    this.gateway.settled.pipe(takeUntilDestroyed()).subscribe((providers) => {
      if (providers === null) this.providerIsCustom.set(true);
      else if (this.provider()) {
        this.providerIsCustom.set(!providers.some((p) => p.name === this.provider()));
      }
    });
  }

  // ---- opening the window -------------------------------------------------------------------

  /**
   * Every way into the window starts here: an empty form, no question in flight, and nothing said
   * about a previous window — a verdict or a refusal about the last model must not appear on this
   * one, and the window is where this page's outcome is shown while it is open.
   */
  private begin(editing: string): void {
    this.reset();
    this.feedback.clear();
    this.gateway.load();
    this.editing.set(editing);
    this.showAdd.set(true);
  }

  /** Open the window empty, for a model the catalog does not have yet. */
  add(): void {
    this.begin('');
  }

  /** Load a catalog row into the window so its declaration can be corrected. */
  edit(model: CatalogModel): void {
    this.begin(model.name);
    // A provider the gateway does not have is shown as typed, not silently blanked. The vendor
    // listing stays shut: beside a declaration somebody measured it invites replacing it.
    this.providerIsCustom.set(
      !!model.provider && !(this.providers() ?? []).some((p) => p.name === model.provider),
    );
    this.name.set(model.name);
    this.displayName.set(model.display_name ?? '');
    this.provider.set(model.provider ?? '');
    this.inputPrice.set(model.input_price_per_million ?? '');
    this.outputPrice.set(model.output_price_per_million ?? '');
    this.cachedPrice.set(model.cached_input_price_per_million ?? '');
    this.cacheWritePrice.set(model.cache_write_price_per_million ?? '');
    this.capabilities.set([...(model.capabilities ?? [])]);
    this.publisher.set(model.publisher ?? '');
    this.regions.set(readRegions(model.addressing));
    this.storedAddressing.set(model.addressing ?? null);
    this.platform.set(model.platform ?? '');
    this.hosting.set(model.hosting ?? '');
    this.contextWindow.set(model.context_window ?? null);
    this.maxOutput.set(model.max_output_tokens ?? null);
    this.kiraId.set(model.numeric_id ?? null);
    this.storedKiraId.set(model.numeric_id ?? null);
    this.defaultOutput.set(model.default_max_output_tokens ?? null);
    this.deprecated.set(model.deprecated ?? false);
    this.approved.set(model.approved ?? false);
    this.loadDeclarations(model);
  }

  /**
   * Start an entry from a model the gateway serves (`FRD-507`) — **provenance only**.
   *
   * Name, provider, publisher, region and platform are facts the adapter was configured with
   * (`FRD-507` FR-3). Price, capabilities and release stay as the empty form has them: a vendor's
   * flag is a claim (`FRD-131`), and a price nobody set is not zero (`FRD-403`).
   */
  importServed(model: ServedModel): void {
    this.begin('');
    this.name.set(model.name);
    this.applyProvenance({
      provider: model.airaProvider ?? '',
      publisher: model.airaPublisher ?? '',
      region: model.airaRegion ?? '',
      platform: model.airaProvider ?? '',
    });
  }

  /**
   * Open the empty form on a configured provider, with what it states about itself filled in — the
   * way in for a platform that publishes no list, or a model its list does not carry.
   */
  startOn(upstream: GatewayProvider | null): void {
    this.begin('');
    if (upstream) {
      this.applyProvenance(provenanceOf(upstream));
      this.imported.set(true);
    }
  }

  /** Open a new entry from one of the vendor's listed models, with the provenance of the provider
   *  that listed it (`FRD-507` stage C). */
  fromOffering(model: OfferedModel, provider: GatewayProvider | null): void {
    this.begin('');
    if (provider) {
      this.applyProvenance(provenanceOf(provider));
      this.providerIsCustom.set(false);
    }
    this.useOffered(model, provider);
  }

  /**
   * Where a model lives, written the same way by every entrance — the served list, a provider and a
   * vendor's listing — so one provider yields one form whichever way in was taken.
   */
  private applyProvenance(where: Whereabouts): void {
    this.provider.set(where.provider);
    this.publisher.set(where.publisher);
    this.platform.set(where.platform);
    this.regions.set(where.region ? [where.region] : []);
  }

  /**
   * Fill the form from one of the vendor's own entries.
   *
   * Copied as facts: the name, the display name, the output ceiling (the API refuses more) and the
   * verbs (Google's method list is exhaustive). Left for the reader: the price (`FRD-403`) and what
   * the model is good at (`FRD-131`) — `thinking` is shown, never ticked, because `FRD-114` needs
   * modes no listing publishes. Capabilities are **added, never removed**: a vendor's silence must
   * not untick a box an administrator ticked.
   */
  protected useOffered(model: OfferedModel, from: GatewayProvider | null = null): void {
    this.name.set(model.name);
    if (model.displayName) this.displayName.set(model.displayName);
    if (model.maxOutputTokens) this.maxOutput.set(model.maxOutputTokens);

    const stated: Capability[] = [];
    if (model.canGenerate) stated.push('generate');
    if (model.canEmbed) stated.push('embed');
    if (model.canCachePrompts) stated.push('prompt_caching');
    this.capabilities.set([...new Set([...this.capabilities(), ...stated])]);
    this.imported.set(true);

    const said: string[] = [];
    if (model.thinking)
      said.push('it reasons — declare the modes and budgets you have measured, not the flag');
    if (model.description) said.push(model.description);
    this.vendorSaid.set(said);

    // A listing the vendor answered a second ago **is** having looked — but only where cataloguing
    // is enough to reach the model. Elsewhere the check is what says it needs configuring too.
    const provider = from ?? this.selectedProvider();
    this.checkedName.set(provider?.cataloguedIsEnough ? model.name : null);
  }

  /** Close the window, discarding whatever was typed. Deliberately not a save — and what went wrong
   *  in it goes with it: a refusal about a form nobody is looking at is a report about nothing. */
  protected close(): void {
    if (!this.showAdd()) return;
    this.showAdd.set(false);
    this.editing.set('');
    this.reset();
    this.feedback.clear();
  }

  // ---- provenance and regions ---------------------------------------------------------------

  /** Show the fields behind the sentence. One way only: there is nothing to close again. */
  protected openProvenance(): void {
    this.latchProvenance();
  }

  /**
   * Keep the provenance fields open once a reader is using them: otherwise choosing a provider
   * makes one "known" and collapses the block, taking the select out from under the pointer.
   */
  private latchProvenance(): void {
    this.provenanceOpened.set(true);
  }

  /** The provider typed by hand, for a platform this gateway has no adapter for yet. */
  protected typeProvider(value: string): void {
    this.latchProvenance();
    this.provider.set(value);
    this.forgetAnswers();
  }

  /** The provider select changed: a configured provider, or "type it yourself". */
  protected chooseProvider(value: string): void {
    this.latchProvenance();
    this.forgetAnswers();
    if (value === this.OTHER) {
      this.providerIsCustom.set(true);
      this.provider.set('');
      return;
    }
    this.providerIsCustom.set(false);
    this.provider.set(value);
    const provider = this.providers()?.find((p) => p.name === value);
    if (provider) {
      this.publisher.set(provider.publisher || this.publisher());
      this.platform.set(provider.name);
    }
  }

  /** The dialect typed by hand. Every answer so far was given in the previous one. */
  protected setPublisher(value: string): void {
    this.publisher.set(value);
    this.forgetAnswers();
  }

  /** Commit the box as a region. Order is kept, because order is the meaning; a repeat is dropped,
   *  because a list naming a place twice would retry the failure it just had. */
  protected addRegion(): void {
    const region = this.regionDraft().trim();
    this.regionDraft.set('');
    if (!region || this.regions().includes(region)) return;
    this.regions.set([...this.regions(), region]);
    this.forgetCheck();
  }

  protected removeRegion(region: string): void {
    this.regions.set(this.regions().filter((value) => value !== region));
    const verdicts = { ...this.regionVerdicts() };
    delete verdicts[region];
    this.regionVerdicts.set(verdicts);
    this.forgetCheck();
  }

  /**
   * The provenance a check was asked with changed, so its overall verdict — and the look that
   * adding a model requires — no longer describe this form. Each region's own mark stays: it is
   * still a true statement about that region.
   */
  private forgetCheck(): void {
    this.checkRequest?.unsubscribe();
    this.checkRequest = null;
    this.checking.set(false);
    this.check.set(null);
    this.checkedName.set(null);
  }

  /** The provider or dialect changed: every answer so far was about another one. */
  private forgetAnswers(): void {
    this.forgetCheck();
    this.levelsRequest?.unsubscribe();
    this.levelsRequest = null;
    this.checkingLevels.set(false);
    this.regionVerdicts.set({});
    this.levelVerdicts.set({});
    this.modeVerdicts.set({});
  }

  /** A region's verdict from the last check, or `null` if it has not been asked about. */
  protected regionVerdict(region: string): Verdict | null {
    return this.regionVerdicts()[region] ?? null;
  }

  /** Whether this installation permits a region — `null` where the gateway did not say. */
  protected regionAllowed(region: string): boolean | null {
    const permitted = this.permittedRegions();
    return permitted.length ? permitted.includes(region) : null;
  }

  // ---- capabilities and declarations --------------------------------------------------------

  protected explain(capability: Capability): string {
    return CAPABILITY_HELP[capability];
  }

  protected toggleCapability(capability: Capability, on: boolean): void {
    const current = this.capabilities().filter((value) => value !== capability);
    this.capabilities.set(on ? [...current, capability] : current);
  }

  protected hasCapability(capability: Capability): boolean {
    return this.capabilities().includes(capability);
  }

  protected hasThinkingMode(mode: ThinkingModeName): boolean {
    return this.thinkingModes().includes(mode);
  }

  protected toggleThinkingMode(mode: ThinkingModeName, on: boolean): void {
    const current = this.thinkingModes().filter((value) => value !== mode);
    this.thinkingModes.set(on ? [...current, mode] : current);
    // A default naming it goes with it: the validator refuses an undeclared default, which would
    // fail the save for a reason the reader cannot see on screen.
    if (!on && this.thinkingDefault() === mode) this.thinkingDefault.set('');
  }

  /**
   * Commit the box as a level word, lower-cased and de-duplicated (`Low` and `low` are the same
   * instruction to every vendor). One of the three *modes* is refused here: those are settings the
   * gateway translates per dialect, and sent verbatim as a level they would mean something else.
   */
  protected addLevel(): void {
    const word = this.levelDraft().trim().toLowerCase();
    this.levelDraft.set('');
    if (!word) return;
    if ((THINKING_MODES as readonly string[]).includes(word)) {
      // The page's one outcome, shown in this window while it is open: `formError` is about the
      // form's state, not about a keystroke.
      this.feedback.error.set(
        `'${word}' is a thinking mode rather than a vendor's level word — tick it above instead.`,
      );
      return;
    }
    if (this.thinkingLevels().includes(word)) return;
    this.thinkingLevels.set([...this.thinkingLevels(), word]);
  }

  protected removeLevel(word: string): void {
    this.thinkingLevels.set(this.thinkingLevels().filter((value) => value !== word));
    const verdicts = { ...this.levelVerdicts() };
    delete verdicts[word];
    this.levelVerdicts.set(verdicts);
    if (this.thinkingDefault() === word) this.thinkingDefault.set('');
  }

  /** A word's verdict from the last check, or `null` if it has not been asked about. */
  protected levelVerdict(word: string): Verdict | null {
    return this.levelVerdicts()[word] ?? null;
  }

  /**
   * Ask the model whether it takes the declared words, and its dialect whether it can express the
   * ticked modes (`ADR-0021`).
   *
   * **Informs, never blocks** (`FRD-506`): only the model can say whether a free-text word works,
   * and it may be unreachable while somebody fills in a form. The draft is committed first, or the
   * word the reader is looking at would be the one not checked.
   */
  protected checkLevels(): void {
    // A new question replaces the last one's outcome, before the draft is committed — so a mode
    // typed as a word is still reported.
    this.feedback.clear();
    this.addLevel();
    const words = this.thinkingLevels();
    const modes = this.thinkingModes();
    const model = this.name().trim();
    if ((!words.length && !modes.length) || !model) return;
    this.checkingLevels.set(true);
    this.levelsRequest?.unsubscribe();
    this.levelsRequest = this.service
      .checkThinkingLevels(model, words, this.formProvenance(), modes)
      .subscribe({
        next: (answer) => {
          const verdicts: Record<string, Verdict> = {};
          for (const result of answer.results) {
            verdicts[result.level] = { ok: result.accepted, detail: result.detail };
          }
          this.levelVerdicts.set(verdicts);
          const modeVerdicts: Record<string, Verdict> = {};
          for (const result of answer.modes ?? []) {
            modeVerdicts[result.mode] = { ok: result.accepted, detail: result.detail };
          }
          this.modeVerdicts.set(modeVerdicts);
          this.checkingLevels.set(false);
        },
        error: (response: unknown) => {
          this.checkingLevels.set(false);
          // Nothing is marked: a failed *question* must not read as a refused *word*.
          this.levelVerdicts.set({});
          this.modeVerdicts.set({});
          this.feedback.fail(response, 'Could not ask this model about its levels.');
        },
      });
  }

  protected hasMediaType(mediaType: string): boolean {
    return this.mediaTypes().includes(mediaType);
  }

  protected toggleMediaType(mediaType: string, on: boolean): void {
    const current = this.mediaTypes().filter((value) => value !== mediaType);
    this.mediaTypes.set(on ? [...current, mediaType] : current);
    // Untick clears the estimate too: a number no longer on screen must not be sent again when the
    // type is re-ticked.
    if (!on) this.setMediaTypeTokens(mediaType, null);
  }

  protected mediaTypeTokens(mediaType: string): number | null {
    return this.mediaTypeSpecs()[mediaType]?.tokens ?? null;
  }

  /**
   * What one attachment of this type is expected to cost in **input** tokens (`FRD-405`).
   *
   * The gateway reads a missing estimate as zero, so a document would be reserved for as if it
   * were a sentence. Wrong high is intended: `settle` corrects it once real usage arrives.
   */
  protected setMediaTypeTokens(mediaType: string, tokens: number | null): void {
    const specs = { ...this.mediaTypeSpecs() };
    specs[mediaType] = tokens === null || Number.isNaN(tokens) ? null : { tokens };
    this.mediaTypeSpecs.set(specs);
  }

  // ---- checking and saving ------------------------------------------------------------------

  protected formError(): string | null {
    const name = this.name().trim();
    if (!name) return 'A model id is required.';
    if (this.alreadyCatalogued()) {
      return (
        `'${name}' is already in the catalog. Open it to correct it — a new declaration under ` +
        'its name would replace the one on file, its release and its price with it.'
      );
    }
    // The server assigns an id only when a model is created; an edit sending none stores none.
    const storedId = this.storedKiraId();
    if (this.editing() && storedId !== null && this.kiraId() === null) {
      return (
        `Clearing the KIRA id takes ${name} off the KIRA surface — its clients address it as ` +
        `${storedId}. Type another id to change it.`
      );
    }
    // Residency refused where it is typed, judged against the gateway's own published list — the
    // same policy said earlier, not a second one (`ADR-0012` §6).
    const forbidden = this.forbiddenRegions();
    if (forbidden.length) {
      return (
        `${forbidden.length === 1 ? 'Region' : 'Regions'} ${forbidden.map((r) => `'${r}'`).join(', ')} ` +
        `${forbidden.length === 1 ? 'is' : 'are'} not permitted by this installation. ` +
        `Allowed: ${this.permittedRegions().join(', ')}. ` +
        'Residency is set on the gateway (AIRA_ALLOWED_REGIONS, or AIRA_ALLOWED_MODEL_REGIONS for ' +
        'one model by name); widen it there if that is intended.'
      );
    }
    const input = this.inputPrice().trim();
    const output = this.outputPrice().trim();
    const cached = this.cachedPrice().trim();
    const cacheWrite = this.cacheWritePrice().trim();
    const priced = [
      priceProblem('Input price', input),
      priceProblem('Output price', output),
      priceProblem('Cached input price', cached),
      priceProblem('Cache write price', cacheWrite),
    ].find(Boolean);
    if (priced) return priced;
    // Half a price — or a cache rate without its base — produces a cost figure that looks
    // complete and is not.
    if ((cached || cacheWrite) && !input) {
      return 'A cache price needs an input price to sit beside.';
    }
    if (!!input !== !!output) {
      return 'Set both the input and the output price, or neither.';
    }
    return this.capError() ?? this.declarationError();
  }

  /** The server's rules about the caps, said before the save rather than behind the window. */
  private capError(): string | null {
    const max = this.maxOutput();
    const fallback = this.defaultOutput();
    const window = this.contextWindow();
    if (max != null && fallback != null && fallback > max) {
      return 'The default output cap cannot exceed the maximum.';
    }
    if (max != null && window != null && max > window) {
      return 'The output cap cannot exceed the context window — the answer is part of what it holds.';
    }
    return null;
  }

  /** The server's rules about the declaration blocks that are ticked, said where they are typed. */
  private declarationError(): string | null {
    if (this.hasCapability('thinking')) {
      const min = this.thinkingMin();
      const max = this.thinkingMax();
      const cap = this.maxOutput();
      if (min != null && max != null && min > max) {
        return 'The smallest thinking budget cannot exceed the largest.';
      }
      if (max != null && cap != null && max >= cap) {
        return 'The largest thinking budget must stay below the output cap — it is drawn from it.';
      }
      if (this.hasThinkingMode('limited') && max == null) {
        return "Offering 'limited' needs a largest thinking budget: it bounds what a caller may name.";
      }
    }
    if (this.hasCapability('embed')) {
      const unreadable = this.unreadableWidths();
      if (unreadable.length) {
        return (
          `Dimensions: ${unreadable.map((value) => `'${value}'`).join(', ')} ` +
          `${unreadable.length === 1 ? 'is not a width' : 'are not widths'} — list whole numbers, e.g. 384, 768.`
        );
      }
    }
    return null;
  }

  /** True while a **new** model has not been checked yet — by somebody who may check it. Without
   *  `operations.diagnose` the check can only answer 403, and demanding it would lock a
   *  catalogue-only role out of adding anything; the footer says why it was not asked. */
  protected mustCheck(): boolean {
    return this.canDiagnose() && !this.editing() && this.checkedName() !== this.name().trim();
  }

  /**
   * Creating a model requires having *looked* — not having succeeded (`FRD-114`: deprecation warns,
   * revocation blocks, and a model is often declared before its credential exists). Editing is
   * exempt: correcting a price is not the moment to demand a round trip.
   */
  protected canSave(): boolean {
    return !this.formError() && !this.feedback.busy() && !this.mustCheck();
  }

  /** Where the **form** says this model lives, so a check answers about the declaration being
   *  written rather than the one being replaced. Regions travel comma-separated. */
  private formProvenance(): Provenance {
    return {
      provider: this.provider(),
      publisher: this.publisher(),
      region: this.regions().join(','),
    };
  }

  protected runCheck(model: Pick<CatalogModel, 'name'>): void {
    // A new question replaces the last one's outcome: a failed check left beside a verdict that
    // has since arrived would contradict it.
    this.feedback.clear();
    this.checking.set(true);
    this.check.set(null);
    this.checkRequest?.unsubscribe();
    this.checkRequest = this.service.checkModel(model.name, this.formProvenance()).subscribe({
      next: (verdict) => {
        this.check.set(verdict);
        // One verdict per region, so a model in three places says which of them answered.
        const verdicts: Record<string, Verdict> = {};
        for (const entry of verdict.regions ?? []) {
          if (entry.region) verdicts[entry.region] = { ok: entry.reachable, detail: entry.detail };
        }
        this.regionVerdicts.set(verdicts);
        this.checkedName.set(model.name);
        this.checking.set(false);
      },
      error: (response: unknown) => {
        this.checking.set(false);
        // Counted as looked-at: the gateway may be down, and the catalog is Management's.
        this.checkedName.set(model.name);
        this.feedback.fail(response, 'Could not check this model.');
      },
    });
  }

  protected save(): void {
    if (!this.canSave()) return;
    const amount = (value: string) => {
      const trimmed = value.trim().replace(',', '.');
      return trimmed ? trimmed : null;
    };
    this.feedback.busy.set(true);
    this.feedback.clear();
    this.service
      .saveModel({
        name: this.name().trim(),
        display_name: this.displayName().trim(),
        provider: this.provider().trim(),
        input_price_per_million: amount(this.inputPrice()),
        output_price_per_million: amount(this.outputPrice()),
        cached_input_price_per_million: amount(this.cachedPrice()),
        cache_write_price_per_million: amount(this.cacheWritePrice()),
        capabilities: this.capabilities(),
        publisher: this.publisher().trim(),
        addressing: this.addressing(),
        platform: this.platform().trim(),
        hosting: this.hosting(),
        context_window: this.contextWindow(),
        max_output_tokens: this.maxOutput(),
        numeric_id: this.kiraId(),
        default_max_output_tokens: this.defaultOutput(),
        deprecated: this.deprecated(),
        approved: this.approved(),
        ...this.declarations(),
      })
      .subscribe({
        next: (model) => {
          this.feedback.busy.set(false);
          this.feedback.succeed(`${model.name} saved.`);
          this.reset();
          this.editing.set('');
          this.showAdd.set(false);
          this.saved.emit();
        },
        error: (response: unknown) => {
          this.feedback.busy.set(false);
          this.feedback.fail(response, 'Could not save the model.');
        },
      });
  }

  /**
   * The addressing block to send: the stored one with the form's regions in it, so a key the form
   * does not offer survives the edit. The single-region spelling `region` is read into the list and
   * written back as `regions`, the one shape both planes read. `null` rather than `{}` when
   * nothing is left: an empty object claims the addressing was considered and is nothing.
   */
  private addressing(): Record<string, unknown> | null {
    const rest = { ...(this.storedAddressing() ?? {}) };
    delete rest['regions'];
    delete rest['region'];
    const block = this.regions().length ? { ...rest, regions: this.regions() } : rest;
    return Object.keys(block).length ? block : null;
  }

  /** Back to the empty form. The vendor's answers and the verdicts belong to the model that was
   *  open, and so do the questions still in flight; left standing they would describe the
   *  previous one. */
  private reset(): void {
    this.forgetAnswers();
    this.editorTab.set('identity');
    this.name.set('');
    this.displayName.set('');
    this.provider.set('');
    this.inputPrice.set('');
    this.outputPrice.set('');
    this.cachedPrice.set('');
    this.cacheWritePrice.set('');
    this.capabilities.set([]);
    this.publisher.set('');
    this.regions.set([]);
    this.regionDraft.set('');
    this.storedAddressing.set(null);
    this.platform.set('');
    this.hosting.set('');
    this.contextWindow.set(null);
    this.maxOutput.set(null);
    this.kiraId.set(null);
    this.storedKiraId.set(null);
    this.defaultOutput.set(null);
    this.deprecated.set(false);
    this.approved.set(false);
    this.clearDeclarations();
    this.providerIsCustom.set(false);
    this.provenanceOpened.set(false);
    this.imported.set(false);
    this.vendorSaid.set([]);
  }

  private clearDeclarations(): void {
    this.thinkingModes.set([]);
    this.thinkingMin.set(null);
    this.thinkingMax.set(null);
    this.thinkingDefault.set('');
    this.thinkingLevels.set([]);
    this.levelDraft.set('');
    this.levelVerdicts.set({});
    this.modeVerdicts.set({});
    this.dimensions.set('');
    this.defaultDimension.set('');
    this.taskTypes.set('');
    this.supportsBatch.set(false);
    this.mediaTypes.set([]);
    this.mediaTypeSpecs.set({});
  }

  private loadDeclarations(model: CatalogModel): void {
    const thinking = model.thinking ?? null;
    this.thinkingModes.set([...(thinking?.modes ?? [])]);
    this.thinkingMin.set(thinking?.min_tokens ?? null);
    this.thinkingMax.set(thinking?.max_tokens ?? null);
    this.thinkingDefault.set(thinking?.default?.mode ?? '');
    this.thinkingLevels.set([...(thinking?.levels ?? [])]);
    this.levelDraft.set('');
    this.levelVerdicts.set({});
    this.modeVerdicts.set({});

    const embedding = model.embedding ?? null;
    this.dimensions.set((embedding?.dimensions ?? []).join(', '));
    this.defaultDimension.set(embedding?.default ?? '');
    this.taskTypes.set((embedding?.task_types ?? []).join(', '));
    this.supportsBatch.set(embedding?.supports_batch ?? false);

    const specs = model.attachments?.media_types ?? {};
    this.mediaTypes.set(Object.keys(specs));
    this.mediaTypeSpecs.set({ ...specs });
  }

  /**
   * The three blocks as the API takes them, or `null` where the capability is not ticked.
   *
   * **`null` and `{}` are different answers**: `{}` replaces a declaration with an empty one,
   * `null` removes it — which is what unticking means — and no hidden width outlives its capability.
   */
  private declarations(): Pick<CatalogModel, 'thinking' | 'embedding' | 'attachments'> {
    const modes = this.thinkingModes();
    const chosen = this.thinkingDefault();
    const levels = this.thinkingLevels();
    const thinking: ThinkingDeclaration | null = this.hasCapability('thinking')
      ? {
          modes,
          min_tokens: this.thinkingMin(),
          max_tokens: this.thinkingMax(),
          default: chosen ? { mode: chosen } : null,
          levels: levels.length ? levels : null,
        }
      : null;

    const widths = this.declaredDimensions();
    const tasks = this.taskTypes()
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean);
    const fallback = Number(this.defaultDimension());
    const embedding: EmbeddingDeclaration | null = this.hasCapability('embed')
      ? {
          dimensions: widths.length ? widths : null,
          // Only a declared width: the validator refuses anything else, and a stale default
          // survives an edit that shortened the list.
          default: widths.includes(fallback) ? fallback : null,
          task_types: tasks.length ? tasks : null,
          supports_batch: this.supportsBatch(),
        }
      : null;

    const specs = this.mediaTypeSpecs();
    const attachments: AttachmentDeclaration | null = this.hasCapability('attachments')
      ? {
          media_types: Object.fromEntries(
            // `null` where nobody gave an estimate, which reserves nothing at dispatch.
            this.mediaTypes().map((mediaType) => [mediaType, specs[mediaType] ?? null]),
          ),
        }
      : null;

    return { thinking, embedding, attachments };
  }
}
