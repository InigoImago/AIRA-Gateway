import { Component, OnInit, computed, inject, input, output, signal } from '@angular/core';
import { finalize } from 'rxjs';
import { CatalogModel, UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { MultiSelect, MultiSelectOption } from '../../core/ui/multi-select';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * Which models this use case may call (`FRD-308`).
 *
 * Two gates with different owners: a Global Administrator approves models for the installation
 * (the catalog, not changeable here); an administrator of this use case releases some of those to
 * it. **Empty means none** — the state every use case starts in — so the empty case is the loudest
 * thing on the panel: such a use case refuses every request, and the cause is on this screen.
 */
@Component({
  selector: 'app-model-release-panel',
  imports: [InfoHint, MultiSelect],
  templateUrl: './model-release-panel.html',
  host: { style: 'display: contents' },
})
export class ModelReleasePanel implements OnInit {
  readonly slug = input.required<string>();
  readonly canManage = input(false);
  /**
   * Whether the Overview tab is open. The panel stays on the page either way, so a release that is
   * "Not saved yet" is still not saved — and still there — after a look at another tab.
   */
  readonly shown = input(true);
  /** What is released today. Owned by the parent, which loaded the use case. */
  readonly released = input<string[]>([]);
  /** The catalogue the page loaded — `null` until it answers, and while it could not be read. */
  readonly catalog = input<CatalogModel[] | null>(null);
  /** Why the catalogue could not be read, if it could not. The page has already reported it. */
  readonly catalogFailure = input('');
  /** Raised after a save, so the parent can take the new use case as its own. */
  readonly saved = output<UseCase>();

  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly loading = computed(() => this.catalog() === null && !this.catalogFailure());
  /**
   * The catalogue did not arrive. Without it nothing can be said about approval: every released
   * model would count as withdrawn and the catalogue as empty — two alarming claims about a
   * catalogue nobody read.
   */
  protected readonly catalogMissing = computed(
    () => this.catalog() === null && !!this.catalogFailure(),
  );

  /** This panel's save is in flight — its own, so another form's save does not relabel it. */
  protected readonly saving = signal(false);

  /** The working set, so nothing is written until Save. */
  protected readonly chosen = signal<Set<string>>(new Set());

  /**
   * Only approved models are offered: the server refuses to release an unapproved one, and a
   * control that invites a click and answers 400 is `FRD-206`'s complaint.
   */
  protected readonly releasable = computed(() =>
    (this.catalog() ?? []).filter((model) => model.approved !== false),
  );

  /**
   * The catalog as the picker needs it. The price rides along as the option's detail, because
   * releasing a model is a spending decision; "no price on file" is not "free" (`FRD-403`).
   */
  protected readonly choices = computed<MultiSelectOption[]>(() =>
    this.releasable().map((model) => ({
      value: model.name,
      label: model.name,
      detail: [
        model.display_name || null,
        model.provider || null,
        model.is_priced ? `${model.input_price_per_million} / 1M in` : 'no price on file',
      ]
        .filter(Boolean)
        .join(' · '),
    })),
  );

  /**
   * Released here and no longer approved. Shown, not dropped: the gateway already refuses them
   * (approval is checked first), and this screen is where the reader can act on it.
   */
  protected readonly withdrawn = computed(() => {
    const approved = new Set(this.releasable().map((model) => model.name));
    return [...this.chosen()].filter((name) => !approved.has(name)).sort();
  });

  /** What the picker binds to. Sorted, so chips do not reorder under the pointer as they toggle. */
  protected readonly chosenList = computed(() => [...this.chosen()].sort());

  protected readonly count = computed(() => this.chosen().size);

  /**
   * Whether anything is unsaved. The separator is `\0` written as an escape, never a raw byte: no
   * model name can contain a NUL, and a literal one makes text tools treat the source as binary.
   */
  protected readonly dirty = computed(() => {
    const before = [...this.released()].sort().join('\0');
    return before !== [...this.chosen()].sort().join('\0');
  });

  ngOnInit(): void {
    this.chosen.set(new Set(this.released()));
  }

  protected setChosen(names: string[]): void {
    // A new Set every time: a zoneless signal compares by reference (`FRD-203` §4).
    this.chosen.set(new Set(names));
  }

  protected save(): void {
    // `feedback.busy()`, not a private flag: `run` clears it on both branches, so a failed save
    // cannot leave the control stuck.
    if (!this.canManage() || this.feedback.busy()) return;
    const chosen = [...this.chosen()].sort();
    this.saving.set(true);
    const request = this.service
      .update(this.slug(), { allowed_models: chosen })
      .pipe(finalize(() => this.saving.set(false)));
    this.feedback.run(request, {
      failure: 'Could not change which models this use case may call.',
      success: (useCase: UseCase) => {
        this.chosen.set(new Set(useCase.allowed_models ?? chosen));
        this.saved.emit(useCase);
        // States what the release now is, and what zero means.
        this.feedback.succeed(
          chosen.length
            ? `${chosen.length} model(s) released. This use case can call those and no others.`
            : 'No model is released. Every request from this use case will be refused until one is.',
        );
      },
    });
  }
}
