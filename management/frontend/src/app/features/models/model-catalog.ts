import {
  Component,
  ElementRef,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { errorMessage } from '../../core/api/error-message';
import {
  CAPABILITIES,
  MEDIA_TYPES,
  THINKING_MODES,
  AttachmentDeclaration,
  Capability,
  CatalogModel,
  EmbeddingDeclaration,
  GatewayProvider,
  Me,
  ModelCheck,
  OfferedModel,
  ServedModel,
  ThinkingDeclaration,
  ThinkingModeName,
} from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { ConfirmService } from '../../core/ui/confirm.service';
import { TablePager } from '../../core/ui/table-pager';
import { TableView } from '../../core/ui/table-view';

/** An amount as people type it: "0.075", "10", "10,50". Kept as text end to end. */
const AMOUNT = /^\d+([.,]\d{1,6})?$/;

@Component({
  selector: 'app-model-catalog',
  imports: [InfoHint, FormsModule, TablePager],
  templateUrl: './model-catalog.html',
  // Escape closes the editor. A window with no way out but the mouse is a window somebody gets
  // stuck in.
  host: { '(document:keydown.escape)': 'close()' },
})
export class ModelCatalog implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly confirmService = inject(ConfirmService);

  protected readonly models = signal<CatalogModel[]>([]);

  /**
   * The catalog, searched and paged.
   *
   * A model catalog is a list that only grows: four families across three platforms, plus every
   * locally served model, each as its own row. Somebody arriving to fix one price should be able
   * to type part of its name rather than scroll past ninety.
   *
   * The name **and** the provider are searchable, because "what do we serve on Vertex" is as
   * common a question here as "what does gemini-2.0-flash cost".
   */
  /**
   * The catalog with **the released models first** (2026-08-11).
   *
   * A catalog only grows, and a reader arriving here is almost always asking about something that
   * is in use — while an alphabetical list buries those among drafts, retired entries and the
   * long tail of a vendor's listing. `approved` is the one property that says "this is live"
   * (`FRD-307`), so it is the one that orders the table.
   *
   * The server keeps ordering by name; sorting here rather than there is deliberate, because two
   * of this screen's warnings count over the **whole** catalog and it is fetched whole for exactly
   * that reason (`FRD-208`).
   */
  private readonly ordered = computed(() =>
    [...this.models()].sort((a, b) => {
      const live = Number(b.approved !== false) - Number(a.approved !== false);
      return live || a.name.localeCompare(b.name);
    }),
  );

  protected readonly view = new TableView<CatalogModel>(
    this.ordered,
    (model) => `${model.name} ${model.display_name ?? ''} ${model.provider ?? ''}`,
  );
  protected readonly loading = signal(true);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly notice = signal<string | null>(null);
  protected readonly me = signal<Me | null>(null);
  protected readonly showAdd = signal(false);
  /** The model id being corrected, or '' when adding a new one. Also the window's title. */
  protected readonly editing = signal('');
  private readonly dialog = viewChild<ElementRef<HTMLElement>>('dialog');

  constructor() {
    // Move the keyboard into the window when it opens, or Escape and Tab still belong to the page
    // behind it.
    effect(() => {
      if (this.showAdd()) this.dialog()?.nativeElement.focus();
    });
  }

  protected readonly name = signal('');
  protected readonly displayName = signal('');
  protected readonly provider = signal('');
  protected readonly inputPrice = signal('');
  protected readonly outputPrice = signal('');
  /** The two cache rates (`FRD-133`). Optional on purpose: a model whose provider does not cache
   *  has none, and one whose provider does but whose rates nobody has entered is charged the
   *  ordinary input rate — over-stating, never under-stating. */
  protected readonly cachedPrice = signal('');
  protected readonly cacheWritePrice = signal('');

  // FRD-114. Zoneless: every piece of form state is a signal, or changing it from code renders
  // nothing.
  protected readonly allCapabilities = CAPABILITIES;
  protected readonly capabilities = signal<Capability[]>([]);
  protected readonly publisher = signal('');
  protected readonly platform = signal('');
  protected readonly hosting = signal<'' | 'managed' | 'self_deployed'>('');
  protected readonly maxOutput = signal<number | null>(null);
  protected readonly defaultOutput = signal<number | null>(null);
  protected readonly deprecated = signal(false);

  // The three declaration blocks. Held as flat signals rather than as the nested JSON the API
  // takes, because a form edits fields and a validator reads a document — assembling it once on
  // save is the only place the two shapes have to meet.
  protected readonly allThinkingModes = THINKING_MODES;
  protected readonly thinkingModes = signal<ThinkingModeName[]>([]);
  protected readonly thinkingMin = signal<number | null>(null);
  protected readonly thinkingMax = signal<number | null>(null);
  protected readonly thinkingDefault = signal<'' | ThinkingModeName>('');
  /** Mode → budget, so an abstract level reserves the right number of tokens (`FRD-111`). */
  protected readonly thinkingLevels = signal<Record<string, number>>({});
  /** Comma-separated, because a width list is short and typing it beats a repeater. */
  protected readonly dimensions = signal('');
  protected readonly defaultDimension = signal<'' | number>('');
  protected readonly taskTypes = signal('');
  protected readonly supportsBatch = signal(false);
  protected readonly allMediaTypes = MEDIA_TYPES;
  protected readonly mediaTypes = signal<string[]>([]);
  /** Per-type token estimates that were already on file. **Carried, never rebuilt**: the form has
   *  no input for them, and writing the block from the checkboxes alone would silently drop an
   *  estimate somebody measured (`FRD-110` §5.3). */
  private readonly mediaTypeSpecs = signal<Record<string, { tokens?: number } | null>>({});
  /** Released for use (`FRD-307`). New declarations start **off**: a model appearing on an
   *  upstream is not the same event as somebody deciding it may be used here. */
  protected readonly approved = signal(false);

  /** Models nobody has described. The gateway serves them at the baseline and refuses everything
   * beyond it (FRD-114 FR-7), so an undeclared model quietly does less than the list suggests. */
  protected readonly undeclared = computed(() => this.models().filter((m) => !m.is_declared));

  /**
   * What ticking each box actually commits the platform to.
   *
   * The vocabulary is closed and every entry means *whether*, never *how* (`ADR-0012`) — but a
   * checkbox reading `structured_output` tells a Global Administrator nothing about the
   * consequence, and the consequences differ sharply: most of these **exclude a model from a
   * fallback chain** when they are absent, while `prompt_caching` only changes the price. A
   * declaration made without knowing that is a guess, and `FRD-114`'s rule is that a capability
   * is a measurement.
   */
  private readonly capabilityHelp: Record<Capability, string> = {
    generate: `The model answers prompts. Almost every model has this; a model without it is an
      embedding model, and the gateway refuses generation requests for it by name.`,
    embed: `The model turns text into vectors. Separate from generation because most models do one
      or the other, and a request to the wrong kind is refused rather than approximated.`,
    structured_output: `The model can be asked to answer as a document matching a schema. Three
      vendors do this by three unrelated mechanisms and the catalog never learns which — this says
      only that it works. A model without it is skipped when a caller submits a schema, because
      prose where a document was expected is a wrong answer that looks like a right one.`,
    thinking: `The model can reason before answering. Declare the modes and budgets it really
      supports, measured — a mode filled in from the list rather than from a test produces requests
      the provider refuses by name. Leave it off if you have not checked: no thinking is the safe
      declaration.`,
    attachments: `The model reads documents and images, not only text. The one capability where
      being wrong is worst: a model that cannot read the attachment is skipped, never sent the
      prompt without it — a dropped attachment produces a confident wrong answer with a 200, and
      the caller blames the model.`,
    tools: `The model can be given function definitions and answer by asking for one. The gateway
      carries the call and never runs it. A model without this is skipped when a caller declares
      functions, because a model that answers in prose instead breaks an assistant silently.`,
    prompt_caching: `The provider will honour a cache marker on this model's stable prefix, so a
      repeated tool declaration and system prompt cost a fraction on the next request. The one
      capability here that changes the **price** and not the answer — so a model without it is
      served normally rather than skipped. Needs the two cache prices above to show up in
      reporting.`,
  };

  protected explain(capability: Capability): string {
    return this.capabilityHelp[capability];
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
    if (!on) {
      // Its budget goes with it, and so does a default naming it — the validator refuses a default
      // that is not among the declared modes, and leaving either behind turns an untick into a
      // save that fails for a reason the reader cannot see on screen.
      const levels = { ...this.thinkingLevels() };
      delete levels[mode];
      this.thinkingLevels.set(levels);
      if (this.thinkingDefault() === mode) this.thinkingDefault.set('');
    }
  }

  protected thinkingLevel(mode: ThinkingModeName): number | null {
    return this.thinkingLevels()[mode] ?? null;
  }

  protected setThinkingLevel(mode: ThinkingModeName, value: number | null): void {
    const levels = { ...this.thinkingLevels() };
    if (value === null || value === undefined || Number.isNaN(value)) delete levels[mode];
    else levels[mode] = value;
    this.thinkingLevels.set(levels);
  }

  /** The widths as numbers, for the default picker — so it can only offer a declared one. */
  protected readonly declaredDimensions = computed(() =>
    this.dimensions()
      .split(',')
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value) && value > 0),
  );

  protected hasMediaType(mediaType: string): boolean {
    return this.mediaTypes().includes(mediaType);
  }

  protected toggleMediaType(mediaType: string, on: boolean): void {
    const current = this.mediaTypes().filter((value) => value !== mediaType);
    this.mediaTypes.set(on ? [...current, mediaType] : current);
  }

  protected mediaTypeTokens(mediaType: string): number | null {
    return this.mediaTypeSpecs()[mediaType]?.tokens ?? null;
  }

  /** Only a Global Administrator maintains prices — they follow the provider contract. */
  protected readonly canEdit = computed(() => this.me()?.roles.includes('global-admin') ?? false);

  /** Models in the catalog that would make consumption unaccountable. */
  protected readonly unpriced = computed(() => this.models().filter((m) => !m.is_priced));

  ngOnInit(): void {
    this.meService.get().subscribe({ next: (me) => this.me.set(me), error: () => undefined });
    this.reload();
  }

  protected reload(): void {
    this.loading.set(true);
    this.service.models().subscribe({
      next: (models) => {
        this.models.set(models);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.error.set(errorMessage(response, 'Could not load the model catalog.'));
        this.loading.set(false);
      },
    });
  }

  protected formError(): string | null {
    if (!this.name().trim()) return 'A model id is required.';
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
    if ((cached || cacheWrite) && !input) {
      // A cache rate without a base rate prices part of a request and not the rest, which is the
      // "looks complete and is not" failure the pair rule below already exists for.
      return 'A cache price needs an input price to sit beside.';
    }
    if (!!input !== !!output) {
      // Half a price produces a cost figure that looks complete and is not.
      return 'Set both the input and the output price, or neither.';
    }
    const max = this.maxOutput();
    const fallback = this.defaultOutput();
    if (max != null && fallback != null && fallback > max) {
      return 'The default output cap cannot exceed the maximum.';
    }
    return null;
  }

  /**
   * Whether a reachability check has been **answered** for the name currently in the form.
   *
   * Answered, not *passed*. An error counts: if the gateway cannot be asked, a reader must not be
   * locked out of their own catalog by a diagnostic.
   */
  protected readonly checkedName = signal<string | null>(null);

  /** True while a **new** model has not been checked yet. */
  protected mustCheck(): boolean {
    return !this.editing() && this.checkedName() !== this.name().trim();
  }

  protected canSave(): boolean {
    // Creating a model requires having *looked*. Not having succeeded — `FRD-114`'s rule stands,
    // deprecation warns and revocation blocks, and a declaration made before its credential
    // arrives is the ordinary order of work. What is refused here is doing it **blind**: the
    // catalog is what the gateway enforces, and "I did not know it was unreachable" is the one
    // outcome a single button can rule out.
    //
    // Editing is exempt: correcting a price on a model that already exists is not the moment to
    // demand a network round trip.
    return !this.formError() && !this.busy() && !this.mustCheck();
  }

  protected save(): void {
    if (!this.canSave()) return;
    const amount = (value: string) => {
      const trimmed = value.trim().replace(',', '.');
      return trimmed ? trimmed : null;
    };
    this.busy.set(true);
    this.error.set(null);
    this.notice.set(null);
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
        platform: this.platform().trim(),
        hosting: this.hosting(),
        max_output_tokens: this.maxOutput(),
        default_max_output_tokens: this.defaultOutput(),
        deprecated: this.deprecated(),
        approved: this.approved(),
        ...this.declarations(),
      })
      .subscribe({
        next: (model) => {
          this.busy.set(false);
          this.notice.set(`${model.name} saved.`);
          this.reset();
          this.editing.set('');
          this.showAdd.set(false);
          this.reload();
        },
        error: (response: unknown) => {
          this.busy.set(false);
          this.error.set(errorMessage(response, 'Could not save the model.'));
        },
      });
  }

  private reset(): void {
    this.name.set('');
    this.displayName.set('');
    this.provider.set('');
    this.inputPrice.set('');
    this.outputPrice.set('');
    this.cachedPrice.set('');
    this.cacheWritePrice.set('');
    this.capabilities.set([]);
    this.publisher.set('');
    this.platform.set('');
    this.hosting.set('');
    this.maxOutput.set(null);
    this.defaultOutput.set(null);
    this.deprecated.set(false);
    this.approved.set(false);
    this.clearDeclarations();
    // The vendor's answers belong to the model that was open. Left standing, they describe the
    // previous one — a note saying "its output cap was filled in" beside a form where it was not.
    this.providerIsCustom.set(false);
    this.vendorFilled.set([]);
    this.vendorSaid.set([]);
  }

  /** Open the window empty, for a model the catalog does not have yet. */
  protected add(): void {
    this.reset();
    this.loadProviders();
    this.checkedName.set(null);
    // A verdict about the last model, left on a window that is now about a new one, is a wrong
    // answer wearing a right one's clothes.
    this.check.set(null);
    this.editing.set('');
    this.showAdd.set(true);
  }

  /** Close the window, discarding whatever was typed. Deliberately not a save. */
  protected close(): void {
    if (!this.showAdd()) return;
    this.showAdd.set(false);
    this.editing.set('');
    this.reset();
  }

  /** Load a row into the window so a declaration can be corrected. */
  // ---- everything on file about one model -------------------------------------------------

  /** The model whose full declaration is open, if any. One at a time. */
  protected readonly openModel = signal<string | null>(null);

  protected toggleDetail(model: CatalogModel): void {
    this.openModel.set(this.openModel() === model.name ? null : model.name);
    this.check.set(null);
  }

  // ---- is it actually reachable, or only written down? (`FRD-506`) -------------------------

  /** The verdict for the open model, if it has been asked for. Cleared when another row opens:
   *  a verdict left on screen under a different model is worse than none. */
  protected readonly check = signal<ModelCheck | null>(null);
  protected readonly checking = signal(false);

  protected runCheck(model: Pick<CatalogModel, 'name'>): void {
    this.checking.set(true);
    this.check.set(null);
    this.service.checkModel(model.name).subscribe({
      next: (verdict) => {
        this.check.set(verdict);
        this.checkedName.set(model.name);
        this.checking.set(false);
      },
      error: (response: unknown) => {
        this.checking.set(false);
        // Counted as looked-at. A diagnostic that cannot answer must not become a gate: the
        // gateway may be down, and the catalog is Management's.
        this.checkedName.set(model.name);
        this.error.set(errorMessage(response, 'Could not check this model.'));
      },
    });
  }

  /** What the verdict means, in a sentence rather than three booleans. */
  protected checkVerdict(verdict: ModelCheck): string {
    if (!verdict.served) return 'Declared, but nothing serves it';
    if (verdict.reachable === null) return 'Served — not contacted';
    return verdict.reachable ? 'Reachable' : 'Not reachable';
  }

  /**
   * Every field of a declaration, in reading order, as label/value pairs.
   *
   * Built here rather than in the template so the list is **exhaustive by construction**: a field
   * added to `CatalogModel` and forgotten here is a field the console silently does not show, and
   * "what does this row actually say" is the question somebody opens the panel with. The catalog is
   * what the gateway *enforces* — a partial answer is worse than none.
   */
  protected detailOf(model: CatalogModel): { key: string; label: string; value: string }[] {
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
      { key: 'underlying_model', label: 'Underlying model', value: dash(model.underlying_model) },
      { key: 'addressing', label: 'Addressing', value: json(model.addressing) },
      {
        key: 'capabilities',
        label: 'Capabilities',
        value: model.is_declared ? (model.capabilities ?? []).join(', ') || '—' : 'undeclared',
      },
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

  /**
   * What the gateway serves, and which of it the catalog does not know (`FRD-507`).
   *
   * `null` until asked. A list that loads itself would put 36 models next to an "Add" button on
   * every visit, which reads as a to-do list and invites exactly the bulk approval `FRD-307` is
   * there to prevent.
   */
  protected readonly served = signal<ServedModel[] | null>(null);
  protected readonly discovering = signal(false);

  //: Served by the gateway and **not in the catalog** — which is a different question from the
  //: existing `undeclared`, meaning catalogued with no capabilities declared. Two absences that
  //: read alike and are fixed differently: one needs a catalog entry, the other a measurement.
  protected readonly notCatalogued = computed(() =>
    (this.served() ?? []).filter((model) => model.airaDeclared === false),
  );
  protected readonly alreadyCatalogued = computed(
    () => (this.served() ?? []).length - this.notCatalogued().length,
  );

  protected discover(): void {
    this.discovering.set(true);
    this.service.servedModels().subscribe({
      next: (models) => {
        this.discovering.set(false);
        this.served.set(models);
      },
      error: (error: unknown) => {
        this.discovering.set(false);
        this.error.set(errorMessage(error, 'Could not ask the gateway which models it serves.'));
      },
    });
  }

  /**
   * Start a catalog entry from a served model.
   *
   * **Provenance only.** Name, provider, publisher and region are facts the adapter was configured
   * with and already put on every audit row. Price, capabilities and the release checkbox are left
   * exactly as the empty form has them, and that is the design rather than an omission: a vendor's
   * capability flag is a claim and not evidence (`FRD-131` found a model that lists `tools` and
   * answers in prose), and a price nobody set is not zero (`FRD-403`). The editor still asks.
   */
  protected importServed(model: ServedModel): void {
    this.reset();
    this.loadProviders();
    this.name.set(model.name);
    this.provider.set(model.airaProvider ?? '');
    this.publisher.set(model.airaPublisher ?? '');
    this.platform.set(model.airaProvider ?? '');
    this.editing.set('');
    this.showAdd.set(true);
  }

  // ---- the provider, and what it offers (`FRD-507` stage C) --------------------------------
  //
  // The field was a text box. A model is declared under whatever string somebody typed, and the
  // two refusals a typo produces — `not in the model catalog` and `has not been approved` — are
  // both correct and neither says which string was wrong. What the gateway is configured with is
  // a fact it already has; asking it removes the transcription and leaves the decision.

  /** The upstreams this gateway is configured with, or `null` until asked. */
  protected readonly providers = signal<GatewayProvider[] | null>(null);
  protected readonly loadingProviders = signal(false);
  /**
   * Why the list could not be fetched, if it could not.
   *
   * Not fatal, and that is deliberate. Declaring a model **before** its credential exists is the
   * ordinary order of work — you write the catalog, then configure the platform — so a gateway
   * that cannot be reached must degrade to the text box it replaced rather than lock somebody out
   * of their own catalog. `FRD-114`'s rule one level up: a diagnostic informs, it does not block.
   */
  protected readonly providersError = signal<string | null>(null);

  /** The sentinel the select uses for "not one of these". */
  protected readonly OTHER = '__other__';
  /** Whether the provider is being typed rather than chosen. */
  protected readonly providerIsCustom = signal(false);

  protected readonly selectedProvider = computed(
    () => (this.providers() ?? []).find((p) => p.name === this.provider()) ?? null,
  );

  // ---- browsing a vendor's own catalogue ---------------------------------------------------
  //
  // Its own window rather than a picker inside the editor, and the reason is size: one real key
  // answered with **50 models**. A dropdown of fifty inside a form that already has eighteen
  // fields is a control somebody scrolls past; a window can list them, mark the ones already
  // catalogued, be searched, and hand exactly one of them to the editor.
  //
  // Two windows, one at a time: browsing ends by opening the editor.

  protected readonly showBrowse = signal(false);
  /** The provider being browsed. Deliberately **not** the editor's `provider` signal: opening a
   *  window to look at something must not edit the form behind it. */
  protected readonly browseProvider = signal('');
  protected readonly browseSearch = signal('');

  /** What this provider offers, once asked. `null` means not asked or not askable. */
  protected readonly offerings = signal<OfferedModel[] | null>(null);
  protected readonly loadingOfferings = signal(false);
  protected readonly offeringsError = signal<string | null>(null);

  /** The providers that can actually be asked — the only ones this window is about. */
  protected readonly askable = computed(() =>
    (this.providers() ?? []).filter((provider) => provider.canEnumerate),
  );

  protected readonly browsedProvider = computed(
    () => (this.providers() ?? []).find((p) => p.name === this.browseProvider()) ?? null,
  );

  /** The offered models matching the search box, or all of them. */
  protected readonly browseMatches = computed(() => {
    const query = this.browseSearch().trim().toLowerCase();
    const list = this.offerings() ?? [];
    if (!query) return list;
    return list.filter((model) =>
      `${model.name} ${model.displayName} ${model.description}`.toLowerCase().includes(query),
    );
  });

  protected openBrowse(): void {
    this.showBrowse.set(true);
    this.browseSearch.set('');
    this.offerings.set(null);
    this.offeringsError.set(null);
    this.loadProviders(() => {
      // One provider that can be asked is not a choice, and a select with a single option is a
      // click that teaches nothing. Two or more, and the reader picks.
      const askable = this.askable();
      if (askable.length === 1 && !this.browseProvider())
        this.chooseBrowseProvider(askable[0].name);
    });
  }

  protected closeBrowse(): void {
    this.showBrowse.set(false);
    this.browseProvider.set('');
    this.offerings.set(null);
    this.offeringsError.set(null);
    this.browseSearch.set('');
  }

  protected chooseBrowseProvider(name: string): void {
    this.browseProvider.set(name);
    this.browseSearch.set('');
    this.offerings.set(null);
    this.offeringsError.set(null);
    if (name) this.loadOfferings(name);
  }

  /**
   * What was copied from the vendor's answer, named.
   *
   * The point of the sentence, not decoration: an import that silently fills six fields and
   * silently leaves five others is indistinguishable from one that was supposed to fill them all
   * and failed. A reader has to be able to see that the price and the capabilities are theirs to
   * enter, at the moment they are looking at a form that is suddenly half full.
   */
  protected readonly vendorFilled = signal<string[]>([]);
  /** What the vendor said that the console deliberately did **not** turn into a declaration. */
  protected readonly vendorSaid = signal<string[]>([]);

  private loadProviders(then?: () => void): void {
    if (this.providers() !== null) {
      then?.();
      return;
    }
    if (this.loadingProviders()) return;
    this.loadingProviders.set(true);
    this.service.providers().subscribe({
      next: (providers) => {
        this.loadingProviders.set(false);
        this.providers.set(providers);
        then?.();
        // Decided in **both** directions, and the first version only did one. Opening the editor
        // fires this call, so a form already carrying a provider is laid out before the answer
        // arrives — and a one-way rule left a perfectly configured provider stuck in the text box
        // it was supposed to replace, for every model opened faster than the gateway answered.
        //
        // A provider the gateway does not have is still not an error: it is a model declared
        // ahead of its credential, which is the ordinary order of work.
        if (this.provider()) {
          this.providerIsCustom.set(!providers.some((p) => p.name === this.provider()));
        }
      },
      error: (response: unknown) => {
        this.loadingProviders.set(false);
        this.providers.set([]);
        this.providerIsCustom.set(true);
        this.providersError.set(
          errorMessage(response, 'Could not ask the gateway which providers it has.'),
        );
      },
    });
  }

  /** The editor's provider select changed: either a configured provider, or "type it yourself". */
  protected chooseProvider(value: string): void {
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

  private loadOfferings(provider: string): void {
    this.loadingOfferings.set(true);
    this.service.providerOfferings(provider).subscribe({
      next: (models) => {
        this.loadingOfferings.set(false);
        this.offerings.set(models);
      },
      error: (response: unknown) => {
        this.loadingOfferings.set(false);
        // Its own message rather than the page banner: this failed *inside* the window, about the
        // provider on screen, and a red bar behind an open window is a report about nothing.
        this.offeringsError.set(
          errorMessage(response, 'Could not ask this provider what it offers.'),
        );
      },
    });
  }

  /** The names the catalog already holds, so the picker can mark them. */
  protected readonly catalogued = computed(() => new Set(this.models().map((m) => m.name)));

  /**
   * A provider as a line somebody chooses from.
   *
   * The **label** leads and the name follows in brackets, because they answer different questions:
   * "which vendor is this" and "what string will be written into the catalog". Showing only the
   * first would hide the value that ends up on every audit row; only the second is what a reader
   * reported as unreadable — `generative-language` beside `local` names neither vendor.
   */
  protected providerLabel(provider: GatewayProvider): string {
    const where = provider.region ? ` · ${provider.region}` : '';
    return provider.label && provider.label !== provider.name
      ? `${provider.label} (${provider.name})${where}`
      : `${provider.name}${where}`;
  }

  /** Whether this offered model is already declared — marked, never hidden: the reader would
   *  otherwise have to check each one against the table behind the window. */
  protected isCatalogued(model: OfferedModel): boolean {
    return this.catalogued().has(model.name);
  }

  /**
   * Take one of the vendor's entries into the editor.
   *
   * The window closes. Two open windows would leave the reader with a list and a form and no
   * indication which one their next click belongs to — and the list is a *choice*, made once.
   */
  protected catalogueOffered(model: OfferedModel): void {
    const provider = this.browsedProvider();
    const existing = this.models().find((m) => m.name === model.name);
    this.showBrowse.set(false);
    // A model the catalog already has is **corrected**, not added a second time: the editor opens
    // on the existing declaration, so a measured capability or a price is not silently replaced by
    // an empty form carrying the vendor's answer.
    if (existing) {
      this.edit(existing);
      return;
    }
    this.reset();
    this.editing.set('');
    this.showAdd.set(true);
    if (provider) {
      this.provider.set(provider.name);
      this.publisher.set(provider.publisher);
      this.platform.set(provider.name);
      this.providerIsCustom.set(false);
    }
    this.useOffered(model);
  }

  /**
   * Fill the form from one of the vendor's own entries.
   *
   * **What may be copied is the whole design.** Three things arrive from the endpoint as facts:
   * the name, the vendor's display name, and its output ceiling — the API refuses a larger
   * request, so that is measured rather than claimed. So are the verbs: Google returns an
   * exhaustive method list and answers 404 for a method missing from it.
   *
   * What is left blank is left blank on purpose. A price nobody set is not zero (`FRD-403`), and
   * what a model is *good* at is a measurement — `FRD-131` found one advertising `tools` in its
   * own metadata that returns the JSON as prose. `thinking` is the sharpest case: the vendor's
   * flag says a model reasons, and `FRD-114` needs the modes and the budgets, which no listing
   * publishes. It is shown and never ticked.
   *
   * Capabilities are **added, never removed**: a vendor saying nothing must not untick a box an
   * administrator has just ticked from their own knowledge.
   */
  protected useOffered(model: OfferedModel): void {
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

    // Picking from a listing the vendor answered a second ago **is** having looked, and that is
    // what `mustCheck` refuses to skip: the ignorance, not the verdict. It counts only where
    // cataloguing the model is enough to reach it — where it is not, the model still needs an
    // entry in the gateway's configuration, and the check is exactly what says so.
    const provider = this.browsedProvider() ?? this.selectedProvider();
    this.checkedName.set(provider?.cataloguedIsEnough ? model.name : null);
  }

  protected edit(model: CatalogModel): void {
    this.check.set(null);
    this.checkedName.set(null);
    this.loadProviders();
    // Editing is not importing: the picker stays shut, because a listing offered beside a model
    // that already exists invites replacing a declaration somebody measured with a vendor's claim.
    // The provider select still needs its current value, and a value the gateway does not have is
    // a model declared ahead of its credential — shown as typed, not silently blanked.
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
    this.platform.set(model.platform ?? '');
    this.hosting.set(model.hosting ?? '');
    this.maxOutput.set(model.max_output_tokens ?? null);
    this.defaultOutput.set(model.default_max_output_tokens ?? null);
    this.deprecated.set(model.deprecated ?? false);
    this.approved.set(model.approved ?? false);
    this.loadDeclarations(model);
    this.editing.set(model.name);
    this.showAdd.set(true);
  }

  private clearDeclarations(): void {
    this.thinkingModes.set([]);
    this.thinkingMin.set(null);
    this.thinkingMax.set(null);
    this.thinkingDefault.set('');
    this.thinkingLevels.set({});
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
    this.thinkingLevels.set({ ...(thinking?.levels ?? {}) });

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
   * The three blocks as the API takes them, or `null` where the capability is not declared.
   *
   * **`null` and an empty object are different answers.** Sending `{}` for a model with no
   * thinking would replace a declaration with an empty one; sending `null` removes it, which is
   * what unticking the capability means. And a block is only sent when its capability is ticked,
   * so a model that stops embedding does not keep a width nobody can see.
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
          levels: Object.keys(levels).length ? levels : null,
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
          // Only where it is one of the declared widths — the validator refuses anything else, and
          // a stale value can survive an edit that shortened the list.
          default: widths.includes(fallback) ? fallback : null,
          task_types: tasks.length ? tasks : null,
          supports_batch: this.supportsBatch(),
        }
      : null;

    const specs = this.mediaTypeSpecs();
    const attachments: AttachmentDeclaration | null = this.hasCapability('attachments')
      ? {
          media_types: Object.fromEntries(
            // The estimate on file is carried through, because the form has no input for it.
            this.mediaTypes().map((mediaType) => [mediaType, specs[mediaType] ?? null]),
          ),
        }
      : null;

    return { thinking, embedding, attachments };
  }

  protected remove(model: CatalogModel): void {
    const question = `Remove ${model.name} from the catalog? Requests for it will no longer be priced.`;
    if (!this.confirmService.ask(question)) return;
    this.busy.set(true);
    this.service.removeModel(model.name).subscribe({
      next: () => {
        this.busy.set(false);
        this.notice.set(`${model.name} removed.`);
        this.reload();
      },
      error: (response: unknown) => {
        this.busy.set(false);
        this.error.set(errorMessage(response, 'Could not remove the model.'));
      },
    });
  }
}
