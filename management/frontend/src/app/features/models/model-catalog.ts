import { Component, OnInit, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { errorMessage } from '../../core/api/error-message';
import { CatalogModel, Me, ModelCheck, OfferedModel, ServedModel } from '../../core/api/models';
import { MeService, perMillion } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { can } from '../../core/auth/roles';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { TablePager } from '../../core/ui/table-pager';
import { TableView } from '../../core/ui/table-view';
import { checkVerdict, detailOf, providerLabel } from './catalog-display';
import { GatewayProviders } from './gateway-providers';
import { ModelEditor } from './model-editor';
import { ServedModels } from './served-models';

/**
 * Models & prices: the catalog, what is on file for each model, and the ways into the editor
 * (`FRD-114`, `FRD-403`, `FRD-507`).
 *
 * The page loads the catalog and owns the table, a row's detail and reachability check, and the
 * window listing what a provider offers. The editor (`ModelEditor`) and discovery (`ServedModels`)
 * are panels owning their own state; all of them report through this page's `PageFeedback`.
 */
@Component({
  selector: 'app-model-catalog',
  imports: [FormsModule, TablePager, ModelEditor, ServedModels],
  templateUrl: './model-catalog.html',
  providers: [PageFeedback, GatewayProviders],
})
export class ModelCatalog implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly confirmService = inject(ConfirmService);
  private readonly gateway = inject(GatewayProviders);
  protected readonly feedback = inject(PageFeedback);
  private readonly editor = viewChild.required(ModelEditor);

  /** The unit this installation's money figures are in, from the one place that decides it. */
  protected readonly currency = this.meService.currency;
  protected readonly perMillion = computed(() => perMillion(this.currency()));
  /** `, in EUR` — joined to the sentence in code, because a template block puts a space before the comma. */
  protected readonly pricedIn = computed(() => (this.currency() ? `, in ${this.currency()}` : ''));
  protected readonly models = signal<CatalogModel[]>([]);
  protected readonly loading = signal(true);
  protected readonly me = signal<Me | null>(null);

  /**
   * The catalog with **the released models first**, then by name.
   *
   * A reader is almost always asking about something in use, and `approved` is what says so
   * (`FRD-307`). Sorted here rather than at the server, because the warnings count over the
   * **whole** catalog and it is fetched whole for that reason (`FRD-208`).
   */
  private readonly ordered = computed(() =>
    [...this.models()].sort((a, b) => {
      const live = Number(b.approved !== false) - Number(a.approved !== false);
      return live || a.name.localeCompare(b.name);
    }),
  );

  /** The catalog, searched by name, display name **and** provider, and paged. */
  protected readonly view = new TableView<CatalogModel>(
    this.ordered,
    (model) => `${model.name} ${model.display_name ?? ''} ${model.provider ?? ''}`,
  );

  /** Declaring, pricing and releasing a model needs `catalog.write` — prices follow the provider
   *  contract, and a release changes what every use case may call. */
  protected readonly canEdit = computed(() => can(this.me(), 'catalog.write'));

  /** Models in the catalog that would make consumption unaccountable. */
  protected readonly unpriced = computed(() => this.models().filter((m) => !m.is_priced));

  /** Models nobody has described. The gateway serves them at the baseline and refuses everything
   *  beyond it (FRD-114 FR-7), so an undeclared model quietly does less than the list suggests. */
  protected readonly undeclared = computed(() => this.models().filter((m) => !m.is_declared));

  /** The names the catalog already holds, so the browse window can mark them. */
  private readonly catalogued = computed(() => new Set(this.models().map((m) => m.name)));

  // ---- one model, opened --------------------------------------------------------------------

  /** The model whose full declaration is open, if any. One at a time. */
  protected readonly openModel = signal<string | null>(null);
  /** The open model's reachability verdict (`FRD-506`). Cleared when another row opens: a verdict
   *  left under a different model is worse than none. */
  protected readonly check = signal<ModelCheck | null>(null);
  protected readonly checking = signal(false);
  protected readonly checkVerdict = checkVerdict;
  protected readonly detailOf = detailOf;
  protected readonly providerLabel = providerLabel;

  // ---- browsing a vendor's own catalogue (`FRD-507` stage C) --------------------------------
  // A window rather than a picker in the editor: one key can list fifty models, which a window can
  // mark, search and hand exactly one of to the editor. Browsing ends by opening the editor.

  protected readonly showBrowse = signal(false);
  /** The provider being browsed — deliberately **not** the editor's: looking at a list must not
   *  edit the form behind it. */
  protected readonly browseProvider = signal('');
  protected readonly browseSearch = signal('');
  /** What this provider offers, once asked. `null` means not asked or not askable. */
  protected readonly offerings = signal<OfferedModel[] | null>(null);
  protected readonly loadingOfferings = signal(false);
  protected readonly offeringsError = signal<string | null>(null);
  protected readonly providers = this.gateway.list;
  protected readonly loadingProviders = this.gateway.loading;
  protected readonly providersError = this.gateway.error;

  /** The providers that can actually be asked for a list. */
  protected readonly askable = computed(() =>
    (this.providers() ?? []).filter((provider) => provider.canEnumerate),
  );

  /** The ones that cannot — **listed rather than filtered out**: an absent provider reads as a
   *  credential that failed, a present one that says why answers the question (`FRD-507` FR-7). */
  protected readonly unaskable = computed(() =>
    (this.providers() ?? []).filter((provider) => !provider.canEnumerate),
  );

  /** The provider chosen in the browse window, askable or not. */
  protected readonly chosenBrowseProvider = computed(() =>
    this.gateway.find(this.browseProvider()),
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
        this.feedback.fail(response, 'Could not load the model catalog.');
        this.loading.set(false);
      },
    });
  }

  protected toggleDetail(model: CatalogModel): void {
    this.openModel.set(this.openModel() === model.name ? null : model.name);
    this.check.set(null);
  }

  /** Is the open model reachable, or only written down (`FRD-506`)? */
  protected runCheck(model: CatalogModel): void {
    this.checking.set(true);
    this.check.set(null);
    this.service.checkModel(model.name).subscribe({
      next: (verdict) => {
        this.check.set(verdict);
        this.checking.set(false);
      },
      error: (response: unknown) => {
        this.checking.set(false);
        this.feedback.fail(response, 'Could not check this model.');
      },
    });
  }

  protected edit(model: CatalogModel): void {
    this.editor().edit(model);
  }

  protected importServed(model: ServedModel): void {
    this.editor().importServed(model);
  }

  protected remove(model: CatalogModel): void {
    const question = `Remove ${model.name} from the catalog? Requests for it will no longer be priced.`;
    if (!this.confirmService.ask(question)) return;
    this.feedback.busy.set(true);
    this.service.removeModel(model.name).subscribe({
      next: () => {
        this.feedback.busy.set(false);
        this.feedback.succeed(`${model.name} removed.`);
        this.reload();
      },
      error: (response: unknown) => {
        this.feedback.busy.set(false);
        this.feedback.fail(response, 'Could not remove the model.');
      },
    });
  }

  // ---- the browse window --------------------------------------------------------------------

  protected openBrowse(): void {
    this.showBrowse.set(true);
    this.browseSearch.set('');
    this.offerings.set(null);
    this.offeringsError.set(null);
    this.gateway.load(() => {
      const askable = this.askable();
      const remembered = this.browseProvider();

      // A provider kept from a cancelled import is asked again, or the select would name it with
      // nothing under it. One this gateway no longer offers is forgotten rather than asked for.
      if (remembered) {
        const known = (this.providers() ?? []).some((provider) => provider.name === remembered);
        if (askable.some((provider) => provider.name === remembered))
          this.chooseBrowseProvider(remembered);
        else if (!known) this.browseProvider.set('');
        return;
      }

      // One provider that can be asked is not a choice; two or more, and the reader picks.
      if (askable.length === 1) this.chooseBrowseProvider(askable[0].name);
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

  private loadOfferings(provider: string): void {
    this.loadingOfferings.set(true);
    this.service.providerOfferings(provider).subscribe({
      next: (models) => {
        this.loadingOfferings.set(false);
        this.offerings.set(models);
      },
      error: (response: unknown) => {
        this.loadingOfferings.set(false);
        // Inside the window, about the provider on screen: a red bar behind an open window is a
        // report about nothing.
        this.offeringsError.set(
          errorMessage(response, 'Could not ask this provider what it offers.'),
        );
      },
    });
  }

  /** Whether this offered model is already declared — marked, never hidden. */
  protected isCatalogued(model: OfferedModel): boolean {
    return this.catalogued().has(model.name);
  }

  /** The empty form, from the one place it is the right answer: no upstream is configured. */
  protected addByName(): void {
    this.showBrowse.set(false);
    this.editor().add();
  }

  /** Leave the listing for the empty form on the chosen platform, its provenance filled in. */
  protected addManually(): void {
    const upstream = this.chosenBrowseProvider();
    this.showBrowse.set(false);
    this.editor().startOn(upstream);
  }

  /**
   * Take one of the vendor's entries into the editor. The window closes — the list is a choice,
   * made once. A model the catalog already has is **corrected**, not added again, so its measured
   * capabilities and price are not replaced by the vendor's answer.
   */
  protected catalogueOffered(model: OfferedModel): void {
    const provider = this.chosenBrowseProvider();
    const existing = this.models().find((m) => m.name === model.name);
    this.showBrowse.set(false);
    if (existing) {
      this.editor().edit(existing);
      return;
    }
    this.editor().fromOffering(model, provider);
  }
}
