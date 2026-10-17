import { Component, computed, inject, input, output, signal } from '@angular/core';
import { ModelReference, ReferenceEntry } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { errorMessage } from '../../core/api/error-message';

/** The platforms the reference describes, as the gateway names them — what Management's
 *  `reference.normalize.PROVIDERS` maps the list onto. */
export const REFERENCE_PLATFORMS: ReadonlySet<string> = new Set(['vertex', 'generative-language']);

/** What one row of the reference offers to the page: the draft, and where it came from. */
export interface ReferencePick {
  entry: ReferenceEntry;
  reference: Pick<ModelReference, 'source' | 'as_of' | 'currency'>;
}

/**
 * Models a public list describes for one platform, to start a catalog entry from (`FRD-508`).
 *
 * For the platform that publishes no list of its own (Vertex), this is the only list there is; for
 * one that does, it adds what that list leaves out — prices, context, how the model reasons. It
 * fills a form and decides nothing: the entry is saved, approved and released by a person.
 */
@Component({
  selector: 'app-reference-models',
  templateUrl: './reference-models.html',
  host: { style: 'display: contents' },
})
export class ReferenceModels {
  private readonly service = inject(UseCaseService);

  /** The provider name the gateway stamps on the platform's models. */
  readonly platform = input.required<string>();
  /** The names the catalog holds, so a row says so rather than offering a second entry. */
  readonly catalogued = input<ReadonlySet<string>>(new Set());
  /** Raised with the row somebody chose. */
  readonly pick = output<ReferencePick>();

  protected readonly reference = signal<ModelReference | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly search = signal('');

  /** The platform's rows, current models first: a deprecated one is listed, never put first. */
  protected readonly rows = computed(() => {
    const words = this.search().trim().toLowerCase();
    return (this.reference()?.entries ?? [])
      .filter((entry) => entry.platform === this.platform())
      .filter(
        (entry) =>
          !words ||
          entry.name.toLowerCase().includes(words) ||
          entry.display_name.toLowerCase().includes(words),
      )
      .sort(
        (a, b) =>
          Number(a.status === 'deprecated') - Number(b.status === 'deprecated') ||
          b.release_date.localeCompare(a.release_date),
      );
  });
  protected readonly total = computed(
    () => (this.reference()?.entries ?? []).filter((e) => e.platform === this.platform()).length,
  );

  constructor() {
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.service.modelReference().subscribe({
      next: (reference) => {
        this.loading.set(false);
        this.reference.set(reference);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.error.set(errorMessage(response, 'Could not load the model reference.'));
      },
    });
  }

  protected choose(entry: ReferenceEntry): void {
    const reference = this.reference();
    if (!reference) return;
    this.pick.emit({
      entry,
      reference: { source: reference.source, as_of: reference.as_of, currency: reference.currency },
    });
  }

  protected price(value: string | null): string {
    return value ?? '—';
  }
}
