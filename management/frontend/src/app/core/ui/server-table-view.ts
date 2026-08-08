import { DestroyRef, Signal, computed, inject, signal } from '@angular/core';
import { Observable, Subject, Subscription, debounceTime, distinctUntilChanged } from 'rxjs';
import { Page } from '../api/models';
import { PagedView } from './table-view';

/** How long to wait after the last keystroke before asking the server. */
const TYPING_PAUSE_MS = 250;

/**
 * A list the **server** pages and searches.
 *
 * `TableView` fetches everything and slices it in the browser, which is right for a list that is
 * small and already in hand. It is wrong for a list that grows without bound, and the measurement
 * says why: `GET /api/v1/use-cases/` takes seconds on an installation with several hundred use
 * cases, because the serializer computes object-level permissions per row. Paging in the browser
 * leaves every one of those computations happening, every load — the reader waits exactly as long
 * and then sees twenty-five rows.
 *
 * Three behaviours this owns, each a way a server-paged list goes wrong:
 *
 * - **Typing does not fire a request per keystroke.** A 250 ms pause, and identical queries are
 *   not re-sent. Without it a nine-letter search is nine round trips against an endpoint that is
 *   already the slow one.
 * - **A new search starts at page one.** Otherwise a filter applied on page 4 asks the server for
 *   page 4 of a two-page result and gets nothing, which reads as "no matches".
 * - **A late response never overwrites a newer one.** Requests are switched, not queued: the
 *   previous one is unsubscribed, so a slow "a" cannot land after a fast "abc" and repopulate the
 *   table with the wrong rows.
 */
export class ServerTableView<T> implements PagedView {
  readonly query = signal('');
  readonly page = signal(1);
  readonly pageSize = signal(25);

  /** The rows of the page currently on screen. */
  readonly rows = signal<T[]>([]);
  /** How many rows match, across every page — what "of 801" refers to. */
  readonly total = signal(0);
  readonly pages = signal(1);
  readonly loading = signal(false);

  readonly matches: Signal<unknown[]>;
  readonly pageCount: Signal<number>;
  readonly firstShown: Signal<number>;
  readonly lastShown: Signal<number>;
  readonly filtered: Signal<boolean>;

  private readonly typed = new Subject<string>();
  private inFlight: Subscription | null = null;

  constructor(
    /** Asks the server for one page. Reports its own errors; this only tracks `loading`. */
    private readonly load: (query: string, page: number) => Observable<Page<T>>,
    /** Called when a page fails, so the screen can say so in the backend's own words. */
    private readonly onError: (response: unknown) => void,
  ) {
    // `matches` exists for the pager, which counts rather than reads. The rows themselves are
    // never all here — that is the point.
    this.matches = computed(() => new Array(this.total()));
    this.pageCount = computed(() => Math.max(1, this.pages()));
    this.firstShown = computed(() => (this.total() ? (this.page() - 1) * this.pageSize() + 1 : 0));
    this.lastShown = computed(() => (this.page() - 1) * this.pageSize() + this.rows().length);
    this.filtered = computed(() => this.query().trim().length > 0);

    const destroyRef = inject(DestroyRef);
    const typing = this.typed
      .pipe(debounceTime(TYPING_PAUSE_MS), distinctUntilChanged())
      .subscribe((value) => {
        this.query.set(value);
        this.page.set(1);
        this.fetch();
      });
    destroyRef.onDestroy(() => {
      typing.unsubscribe();
      this.inFlight?.unsubscribe();
    });
  }

  /** Fetch the first page. Call from `ngOnInit`. */
  start(): void {
    this.fetch();
  }

  /** Re-fetch the page currently on screen — after a create, an edit or a delete. */
  reload(): void {
    this.fetch();
  }

  search(value: string): void {
    this.typed.next(value);
  }

  go(page: number): void {
    const target = Math.min(Math.max(1, page), this.pageCount());
    if (target === this.page()) return;
    this.page.set(target);
    this.fetch();
  }

  next(): void {
    this.go(this.page() + 1);
  }

  previous(): void {
    this.go(this.page() - 1);
  }

  current(): number {
    return this.page();
  }

  private fetch(): void {
    // Switched, not queued: a slow request for "a" must not land after a fast one for "abc" and
    // repopulate the table with rows nobody asked for.
    this.inFlight?.unsubscribe();
    this.loading.set(true);
    this.inFlight = this.load(this.query().trim(), this.page()).subscribe({
      next: (page) => {
        this.rows.set(page.results);
        this.total.set(page.count);
        this.pages.set(page.pages);
        this.pageSize.set(page.page_size);
        // The server has the last word on which page this is: asking for page 9 of a list that
        // shrank to three pages must not leave the pager claiming 9.
        this.page.set(page.page);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.onError(response);
      },
    });
  }
}
