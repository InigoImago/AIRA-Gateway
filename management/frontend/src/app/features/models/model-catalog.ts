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
import { CAPABILITIES, Capability, CatalogModel, Me, ModelCheck } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { TablePager } from '../../core/ui/table-pager';
import { TableView } from '../../core/ui/table-view';

/** An amount as people type it: "0.075", "10", "10,50". Kept as text end to end. */
const AMOUNT = /^\d+([.,]\d{1,6})?$/;

@Component({
  selector: 'app-model-catalog',
  imports: [FormsModule, TablePager],
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

  /** Models nobody has described. The gateway serves them at the baseline and refuses everything
   * beyond it (FRD-114 FR-7), so an undeclared model quietly does less than the list suggests. */
  protected readonly undeclared = computed(() => this.models().filter((m) => !m.is_declared));

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
    if ((input && !AMOUNT.test(input)) || (output && !AMOUNT.test(output))) {
      return 'Prices are amounts per 1,000,000 tokens, e.g. 0.075.';
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

  protected canSave(): boolean {
    return !this.formError() && !this.busy();
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
        capabilities: this.capabilities(),
        publisher: this.publisher().trim(),
        platform: this.platform().trim(),
        hosting: this.hosting(),
        max_output_tokens: this.maxOutput(),
        default_max_output_tokens: this.defaultOutput(),
        deprecated: this.deprecated(),
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
    this.capabilities.set([]);
    this.publisher.set('');
    this.platform.set('');
    this.hosting.set('');
    this.maxOutput.set(null);
    this.defaultOutput.set(null);
    this.deprecated.set(false);
  }

  /** Open the window empty, for a model the catalog does not have yet. */
  protected add(): void {
    this.reset();
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
        this.checking.set(false);
      },
      error: (response: unknown) => {
        this.checking.set(false);
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
      { key: 'deprecated', label: 'Deprecated', value: model.deprecated ? 'yes' : 'no' },
      { key: 'updated_at', label: 'Last changed', value: dash(model.updated_at) },
    ];
  }

  protected edit(model: CatalogModel): void {
    this.check.set(null);
    this.name.set(model.name);
    this.displayName.set(model.display_name ?? '');
    this.provider.set(model.provider ?? '');
    this.inputPrice.set(model.input_price_per_million ?? '');
    this.outputPrice.set(model.output_price_per_million ?? '');
    this.capabilities.set([...(model.capabilities ?? [])]);
    this.publisher.set(model.publisher ?? '');
    this.platform.set(model.platform ?? '');
    this.hosting.set(model.hosting ?? '');
    this.maxOutput.set(model.max_output_tokens ?? null);
    this.defaultOutput.set(model.default_max_output_tokens ?? null);
    this.deprecated.set(model.deprecated ?? false);
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
