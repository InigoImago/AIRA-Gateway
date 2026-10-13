import { Component, computed, inject, input, output, signal } from '@angular/core';
import { ServedModel } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * What the gateway serves and the catalog does not know yet (`FRD-507`).
 *
 * Asked for, never loaded: a list of everything an endpoint offers, beside an "Add" button on every
 * visit, reads as a to-do list and invites the bulk approval `FRD-307` exists to prevent.
 */
@Component({
  selector: 'app-served-models',
  templateUrl: './served-models.html',
  host: { style: 'display: contents' },
})
export class ServedModels {
  private readonly service = inject(UseCaseService);
  private readonly feedback = inject(PageFeedback);

  /** Raised with the served model somebody chose to catalogue. */
  readonly catalogue = output<ServedModel>();

  /**
   * The names the catalog holds now. The gateway's `airaDeclared` follows the catalog over the
   * event bus, so it lags a save; a model saved a moment ago is not offered as missing again.
   */
  readonly catalogued = input<ReadonlySet<string>>(new Set());

  /** What the gateway serves, or `null` until asked. */
  protected readonly served = signal<ServedModel[] | null>(null);
  protected readonly discovering = signal(false);
  /**
   * Served and **not in the catalog** — a different absence from the page's `undeclared`
   * (catalogued, no capabilities declared): one needs an entry, the other a measurement.
   */
  protected readonly notCatalogued = computed(() =>
    (this.served() ?? []).filter(
      (model) => model.airaDeclared === false && !this.catalogued().has(model.name),
    ),
  );
  protected readonly alreadyCatalogued = computed(
    () => (this.served() ?? []).length - this.notCatalogued().length,
  );

  protected discover(): void {
    // A new question replaces the last one's outcome: a failure left above the answer that has
    // since arrived would contradict it.
    this.feedback.clear();
    this.discovering.set(true);
    this.service.servedModels().subscribe({
      next: (models) => {
        this.discovering.set(false);
        this.served.set(models);
      },
      error: (error: unknown) => {
        this.discovering.set(false);
        this.feedback.fail(error, 'Could not ask the gateway which models it serves.');
      },
    });
  }
}
