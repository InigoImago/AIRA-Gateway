import { Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { CatalogModel, FALLBACK_KINDS, FallbackChains, FallbackKind } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { MultiSelect, MultiSelectOption } from '../../core/ui/multi-select';
import { PageFeedback } from '../../core/ui/page-feedback';
import { KIND_LABELS, canServe } from './fallback-kinds';

const EMPTY: FallbackChains = { generate: [], embed: [], rerank: [] };

/**
 * What answers when a model cannot (`FRD-310`): the installation's fallback chain for each kind of
 * request, on the Models page because it is a decision about models.
 *
 * A panel of that page: it loads and saves the chains itself and reports through the page's one
 * `PageFeedback`. Only a model that can serve a kind is offered for it — an embedding never falls
 * back to a model that writes text — and the server refuses the rest by name as well.
 */
@Component({
  selector: 'app-fallback-chains-card',
  imports: [InfoHint, MultiSelect],
  templateUrl: './fallback-chains-card.html',
})
export class FallbackChainsCard implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly feedback = inject(PageFeedback);

  /** The whole catalogue, as the page loaded it. */
  readonly catalogued = input<CatalogModel[]>([]);
  /** Whether this reader may change a chain (`catalog.write`), as the page decides it. */
  readonly canEdit = input(false);

  protected readonly kinds = FALLBACK_KINDS;
  protected readonly label = (kind: FallbackKind): string => KIND_LABELS[kind];
  protected readonly busy = this.feedback.busy;

  /** The chains as the server last answered, or `null` before it has. */
  protected readonly stored = signal<FallbackChains | null>(null);
  /** What the form holds, kind by kind. */
  protected readonly drafts = signal<FallbackChains>(EMPTY);
  protected readonly loadError = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.loadError.set(null);
    this.service.fallbackChains().subscribe({
      next: (chains) => this.take(chains),
      error: (response: unknown) =>
        this.loadError.set(this.feedback.fail(response, 'Could not load the fallback chains.')),
    });
  }

  /**
   * What may be chosen for `kind`: every approved model that can serve it — and whatever the
   * chain already names that is no longer one of those, offered as itself and marked, so it can be
   * taken out where it is read rather than vanishing from the list.
   */
  protected choices(kind: FallbackKind): MultiSelectOption[] {
    const usable = this.catalogued().filter((model) => model.approved && canServe(model, kind));
    const offered: MultiSelectOption[] = usable.map((model) => ({
      value: model.name,
      label: model.name,
      detail: model.capabilities?.length ? undefined : 'undeclared: generation and embedding',
    }));
    const known = new Map(this.catalogued().map((model) => [model.name, model]));
    for (const name of this.drafts()[kind]) {
      if (usable.some((model) => model.name === name)) continue;
      const model = known.get(name);
      const warning = !model
        ? 'not in the catalogue'
        : !model.approved
          ? 'not approved — skipped'
          : 'cannot serve this kind — skipped';
      offered.push({ value: name, label: name, warning });
    }
    return offered;
  }

  /** Whether `kind`'s chain in the form differs from what the server holds. */
  protected changed(kind: FallbackKind): boolean {
    const stored = this.stored()?.[kind] ?? [];
    const draft = this.drafts()[kind];
    return stored.length !== draft.length || stored.some((name, index) => name !== draft[index]);
  }

  /** A chain chosen in the picker, in the order it was chosen: the order is the order tried. */
  protected choose(kind: FallbackKind, models: string[]): void {
    this.drafts.update((drafts) => ({ ...drafts, [kind]: [...models] }));
  }

  protected save(kind: FallbackKind): void {
    if (!this.canEdit() || this.busy()) return;
    const models = this.drafts()[kind];
    this.feedback.run(this.service.saveFallbackChain(kind, models), {
      failure: `Could not save the fallback chain for ${KIND_LABELS[kind].toLowerCase()}.`,
      success: (chains) => {
        this.take(chains);
        this.feedback.succeed(
          models.length
            ? `${KIND_LABELS[kind]} falls back to ${models.join(', ')}, in that order.`
            : `${KIND_LABELS[kind]} has no fallback: a model that cannot answer is an error.`,
        );
      },
    });
  }

  /** How many kinds have a chain — said in the card's summary. */
  protected readonly configured = computed(
    () => this.kinds.filter((kind) => (this.stored()?.[kind] ?? []).length > 0).length,
  );

  private take(chains: FallbackChains): void {
    const whole = { ...EMPTY, ...chains };
    this.stored.set(whole);
    this.drafts.set({
      generate: [...whole.generate],
      embed: [...whole.embed],
      rerank: [...whole.rerank],
    });
  }
}
