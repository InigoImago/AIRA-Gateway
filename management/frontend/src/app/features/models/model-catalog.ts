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
  Capability,
  CatalogModel,
  Me,
  ModelCheck,
  ServedModel,
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
  protected readonly view = new TableView<CatalogModel>(
    this.models,
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
  }

  /** Open the window empty, for a model the catalog does not have yet. */
  protected add(): void {
    this.reset();
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
        label: 'Input / 1M',
        value: model.is_priced ? dash(model.input_price_per_million) : 'no price',
      },
      {
        key: 'output_price',
        label: 'Output / 1M',
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
    this.name.set(model.name);
    this.provider.set(model.airaProvider ?? '');
    this.publisher.set(model.airaPublisher ?? '');
    this.platform.set(model.airaProvider ?? '');
    this.editing.set('');
    this.showAdd.set(true);
  }

  protected edit(model: CatalogModel): void {
    this.check.set(null);
    this.checkedName.set(null);
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
    this.editing.set(model.name);
    this.showAdd.set(true);
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
