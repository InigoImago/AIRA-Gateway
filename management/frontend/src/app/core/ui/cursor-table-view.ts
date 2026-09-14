import { DestroyRef, Signal, computed, inject, signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';
import { PagedView } from './table-view';

/** One page as a cursor-paged endpoint answers it. */
export interface CursorPage<T> {
  rows: T[];
  /** The cursor of the next page, or `null` on the last. */
  next: string | null;
  /** Every row the filters match, across all pages. */
  count: number;
}

/**
 * A list the **server** pages by cursor — for a log that grows while it is read.
 *
 * An offset page under an appending table shows some rows twice and skips others; a cursor anchors
 * each page to the last row of the one before. So this keeps the trail of cursors that opened the
 * pages behind the current one: "Next" follows the server's cursor, "Previous" goes back along the
 * trail, and the browser holds the page on screen and nothing more.
 *
 * - **A late response never overwrites a newer one**: requests are switched, not queued.
 * - **A changed filter is a different list**: {@link reset} starts it again at its first page.
 */
export class CursorTableView<T> implements PagedView {
  /** The rows of the page on screen. */
  readonly rows = signal<T[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);

  readonly matches: Signal<unknown[]>;
  readonly pageCount: Signal<number>;
  readonly firstShown: Signal<number>;
  readonly lastShown: Signal<number>;
  readonly filtered: Signal<boolean>;

  /** The cursor that opened each page up to the current one; the first page's is `null`. */
  private readonly trail = signal<(string | null)[]>([null]);
  private readonly following = signal<string | null>(null);
  private readonly page = computed(() => this.trail().length);
  private inFlight: Subscription | null = null;

  constructor(
    /** Asks the server for the page a cursor opens (`null`: the first). */
    private readonly load: (cursor: string | null) => Observable<CursorPage<T>>,
    /** Called when a page fails, so the screen can say so in the backend's own words. */
    private readonly onError: (response: unknown) => void,
    /** What one page holds, which is what the server is asked for. */
    readonly pageSize: number,
    isFiltered: () => boolean = () => false,
  ) {
    // `matches` exists for the pager, which counts rather than reads: the rows are never all here.
    this.matches = computed(() => new Array(this.total()));
    // At least one page past this one while the server offers a cursor, whatever the count said
    // before a read was recorded since.
    this.pageCount = computed(() =>
      Math.max(
        1,
        Math.ceil(this.total() / this.pageSize),
        this.page() + (this.following() ? 1 : 0),
      ),
    );
    this.firstShown = computed(() =>
      this.rows().length ? (this.page() - 1) * this.pageSize + 1 : 0,
    );
    this.lastShown = computed(() => (this.page() - 1) * this.pageSize + this.rows().length);
    this.filtered = computed(isFiltered);
    inject(DestroyRef).onDestroy(() => this.inFlight?.unsubscribe());
  }

  /** Fetch the first page. Call from `ngOnInit`. */
  start(): void {
    this.fetch();
  }

  /** The first page again — after a filter changed, or to see what was recorded since. */
  reset(): void {
    this.trail.set([null]);
    this.fetch();
  }

  next(): void {
    const cursor = this.following();
    if (!cursor || this.loading()) return;
    this.trail.update((trail) => [...trail, cursor]);
    this.fetch();
  }

  previous(): void {
    if (this.page() === 1 || this.loading()) return;
    this.trail.update((trail) => trail.slice(0, -1));
    this.fetch();
  }

  current(): number {
    return this.page();
  }

  private fetch(): void {
    this.inFlight?.unsubscribe();
    this.loading.set(true);
    const trail = this.trail();
    this.inFlight = this.load(trail[trail.length - 1]).subscribe({
      next: (page) => {
        this.rows.set(page.rows);
        this.total.set(page.count);
        this.following.set(page.next);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.onError(response);
      },
    });
  }
}
