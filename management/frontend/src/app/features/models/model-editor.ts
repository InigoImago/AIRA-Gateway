import {
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
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
import { PageFeedback } from '../../core/ui/page-feedback';
import { checkVerdict, providerLabel } from './catalog-display';
import { CAPABILITY_HELP, thinkingModeMeans } from './declaration-help';
import { GatewayProviders } from './gateway-providers';

/** An amount as people type it: "0.075", "10", "10,50". Kept as text end to end. */
const AMOUNT = /^\d+([.,]\d{1,6})?$/;

/** What the model or the gateway answered about one word, mode or region. */
type Verdict = { ok: boolean; detail: string };

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
  imports: [InfoHint, FormsModule],
  templateUrl: './model-editor.html',
  // Escape closes the editor: a window whose only exit is the mouse is one somebody gets stuck in.
  host: { '(document:keydown.escape)': 'close()', style: 'display: contents' },
})
export class ModelEditor {
  private readonly service = inject(UseCaseService);
  private readonly gateway = inject(GatewayProviders);
  private readonly currency = inject(MeService).currency;
  protected readonly perMillion = computed(() => perMillion(this.currency()));
  protected readonly feedback = inject(PageFeedback);

  /** Raised after a successful save, so the page reloads the catalog. */
  readonly saved = output<void>();

  // ---- the window ---------------------------------------------------------------------------

  readonly showAdd = signal(false);
  /** The model id being corrected, or '' when adding a new one. Also the window's title. */
  readonly editing = signal('');
  /** Which third of the declaration is on screen. The values live in signals, so switching tabs
   *  keeps everything typed; only the DOM comes and goes. */
  protected readonly editorTab = signal<'identity' | 'capabilities' | 'price'>('identity');
  private readonly dialog = viewChild<ElementRef<HTMLElement>>('dialog');

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
  protected readonly selectedProvider = computed(() => this.gateway.find(this.provider()));

  /**
   * What an import copied, named: an import that silently fills some fields and leaves others is
   * indistinguishable from one that failed at the rest.
   */
  protected readonly vendorFilled = signal<string[]>([]);
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

  /** The regions in the form that this installation does not permit. */
  protected readonly forbiddenRegions = computed<string[]>(() => {
    const allowed = this.allowedRegions();
    // Empty means the gateway did not say: reading it as an empty allow-list would refuse every
    // region anybody typed, enforcing a policy the console has never heard.
    if (!allowed.length) return [];
    return this.regions().filter((region) => !allowed.includes(region));
  });

  /**
   * The rest of the provenance sentence, assembled here rather than from template blocks: the
   * formatter puts each `@if` on its own line, and the text after it starts with a space.
   */
  protected readonly provenanceDetail = computed(() => {
    const parts: string[] = [];
    if (this.publisher()) parts.push(`speaking the ${this.publisher()} dialect`);
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
    // Move the keyboard into the window when it opens, or Escape and Tab still belong to the page.
    effect(() => {
      if (this.showAdd()) this.dialog()?.nativeElement.focus();
    });
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

  /** Open the window empty, for a model the catalog does not have yet. */
  add(): void {
    this.reset();
    this.gateway.load();
    this.checkedName.set(null);
    this.editing.set('');
    this.showAdd.set(true);
  }

  /** Load a catalog row into the window so its declaration can be corrected. */
  edit(model: CatalogModel): void {
    this.check.set(null);
    this.checkedName.set(null);
    this.gateway.load();
    // A provider the gateway does not have is shown as typed, not silently blanked. The vendor
    // listing stays shut: beside a declaration somebody measured it invites replacing it.
    this.providerIsCustom.set(
      !!model.provider && !(this.providers() ?? []).some((p) => p.name === model.provider),
    );
    this.vendorFilled.set([]);
    this.vendorSaid.set([]);
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
    this.platform.set(model.platform ?? '');
    this.hosting.set(model.hosting ?? '');
    this.contextWindow.set(model.context_window ?? null);
    this.maxOutput.set(model.max_output_tokens ?? null);
    this.kiraId.set(model.numeric_id ?? null);
    this.defaultOutput.set(model.default_max_output_tokens ?? null);
    this.deprecated.set(model.deprecated ?? false);
    this.approved.set(model.approved ?? false);
    this.loadDeclarations(model);
    this.editing.set(model.name);
    this.showAdd.set(true);
  }

  /**
   * Start an entry from a model the gateway serves (`FRD-507`) — **provenance only**.
   *
   * Name, provider, publisher and platform are facts the adapter was configured with. Price,
   * capabilities and release stay as the empty form has them: a vendor's flag is a claim
   * (`FRD-131`), and a price nobody set is not zero (`FRD-403`).
   */
  importServed(model: ServedModel): void {
    this.reset();
    this.gateway.load();
    this.name.set(model.name);
    this.provider.set(model.airaProvider ?? '');
    this.publisher.set(model.airaPublisher ?? '');
    this.platform.set(model.airaProvider ?? '');
    this.editing.set('');
    this.showAdd.set(true);
  }

  /**
   * Open the empty form on a configured provider, with what it states about itself filled in — the
   * way in for a platform that publishes no list, or a model its list does not carry.
   */
  startOn(upstream: GatewayProvider | null): void {
    // Through `add()`, so this entrance also clears the id being corrected and the last verdict.
    this.add();
    if (upstream) {
      this.provider.set(upstream.name);
      this.publisher.set(upstream.publisher ?? '');
      this.regions.set(upstream.region ? [upstream.region] : []);
      this.vendorFilled.set(
        [upstream.name && 'provider', upstream.publisher && 'dialect'].filter(Boolean) as string[],
      );
    }
  }

  /** Open a new entry from one of the vendor's listed models, with the provenance of the provider
   *  that listed it (`FRD-507` stage C). */
  fromOffering(model: OfferedModel, provider: GatewayProvider | null): void {
    this.reset();
    this.editing.set('');
    this.showAdd.set(true);
    if (provider) {
      this.provider.set(provider.name);
      this.publisher.set(provider.publisher);
      this.platform.set(provider.name);
      this.providerIsCustom.set(false);
    }
    this.useOffered(model, provider);
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

    const filled = ['the model id', 'where it is reached'];
    if (model.displayName) filled.push('its display name');
    if (model.maxOutputTokens) filled.push('its output cap');
    if (stated.length) filled.push(`what it may be asked to do (${stated.join(', ')})`);
    this.vendorFilled.set(filled);

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

  /** Close the window, discarding whatever was typed. Deliberately not a save. */
  protected close(): void {
    if (!this.showAdd()) return;
    this.showAdd.set(false);
    this.editing.set('');
    this.reset();
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
  }

  /** The provider select changed: a configured provider, or "type it yourself". */
  protected chooseProvider(value: string): void {
    this.latchProvenance();
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

  /** Commit the box as a region. Order is kept, because order is the meaning; a repeat is dropped,
   *  because a list naming a place twice would retry the failure it just had. */
  protected addRegion(): void {
    const region = this.regionDraft().trim();
    this.regionDraft.set('');
    if (!region || this.regions().includes(region)) return;
    this.regions.set([...this.regions(), region]);
  }

  protected removeRegion(region: string): void {
    this.regions.set(this.regions().filter((value) => value !== region));
    const verdicts = { ...this.regionVerdicts() };
    delete verdicts[region];
    this.regionVerdicts.set(verdicts);
  }

  /** A region's verdict from the last check, or `null` if it has not been asked about. */
  protected regionVerdict(region: string): Verdict | null {
    return this.regionVerdicts()[region] ?? null;
  }

  /** Whether this installation permits a region — `null` where the gateway did not say. */
  protected regionAllowed(region: string): boolean | null {
    const allowed = this.allowedRegions();
    return allowed.length ? allowed.includes(region) : null;
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
      // The page's banner, because `formError` is about the form's state, not about a keystroke.
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
    this.addLevel();
    const words = this.thinkingLevels();
    const modes = this.thinkingModes();
    const model = this.name().trim();
    if ((!words.length && !modes.length) || !model) return;
    this.checkingLevels.set(true);
    this.service.checkThinkingLevels(model, words, this.formProvenance(), modes).subscribe({
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
    if (!this.name().trim()) return 'A model id is required.';
    // Residency refused where it is typed, judged against the gateway's own published list — the
    // same policy said earlier, not a second one (`ADR-0012` §6).
    const forbidden = this.forbiddenRegions();
    if (forbidden.length) {
      return (
        `${forbidden.length === 1 ? 'Region' : 'Regions'} ${forbidden.map((r) => `'${r}'`).join(', ')} ` +
        `${forbidden.length === 1 ? 'is' : 'are'} not permitted by this installation. ` +
        `Allowed: ${this.allowedRegions().join(', ')}. ` +
        'Residency is set on the gateway (AIRA_ALLOWED_REGIONS); widen it there if that is intended.'
      );
    }
    const input = this.inputPrice().trim();
    const output = this.outputPrice().trim();
    const cached = this.cachedPrice().trim();
    const cacheWrite = this.cacheWritePrice().trim();
    if (
      (input && !AMOUNT.test(input)) ||
      (output && !AMOUNT.test(output)) ||
      (cached && !AMOUNT.test(cached)) ||
      (cacheWrite && !AMOUNT.test(cacheWrite))
    ) {
      return 'Prices are amounts per 1,000,000 tokens, e.g. 0.075.';
    }
    // Half a price — or a cache rate without its base — produces a cost figure that looks
    // complete and is not.
    if ((cached || cacheWrite) && !input) {
      return 'A cache price needs an input price to sit beside.';
    }
    if (!!input !== !!output) {
      return 'Set both the input and the output price, or neither.';
    }
    const max = this.maxOutput();
    const fallback = this.defaultOutput();
    if (max != null && fallback != null && fallback > max) {
      return 'The default output cap cannot exceed the maximum.';
    }
    return null;
  }

  /** True while a **new** model has not been checked yet. */
  protected mustCheck(): boolean {
    return !this.editing() && this.checkedName() !== this.name().trim();
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
    this.checking.set(true);
    this.check.set(null);
    this.service.checkModel(model.name, this.formProvenance()).subscribe({
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
        // `null` rather than `{}` when empty: an empty object claims the addressing was considered
        // and is nothing.
        addressing: this.regions().length ? { regions: this.regions() } : null,
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

  /** Back to the empty form. The vendor's answers and the verdicts belong to the model that was
   *  open; left standing they would describe the previous one. */
  private reset(): void {
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
    this.regionVerdicts.set({});
    this.platform.set('');
    this.hosting.set('');
    this.contextWindow.set(null);
    this.maxOutput.set(null);
    this.kiraId.set(null);
    this.defaultOutput.set(null);
    this.deprecated.set(false);
    this.approved.set(false);
    this.clearDeclarations();
    this.providerIsCustom.set(false);
    this.provenanceOpened.set(false);
    this.vendorFilled.set([]);
    this.vendorSaid.set([]);
    this.check.set(null);
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
