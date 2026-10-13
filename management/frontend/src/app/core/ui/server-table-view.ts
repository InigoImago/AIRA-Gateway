import { DestroyRef, Signal, computed, inject, signal } from '@angular/core';
import { Observable, Subject, Subscription, debounceTime, distinctUntilChanged } from 'rxjs';
import { Page } from '../api/models';
import { PagedView } from './table-view';

/** How long to wait after the last keystroke before asking the server. */
const TYPING_PAUSE_MS = 250;

/**
 * A list the **server** pages and searches (`FRD-208`).
 *
 * For a list that grows without bound, or whose endpoint is expensive per row — the use-case list
 * computes object-level permissions for every row, so paging in the browser still pays for all of
 * them.
 *
 * - **Typing does not fire a request per keystroke**: a pause, and identical queries are not re-sent.
 * - **A new search starts at page one**, or page 4 of a two-page result reads as "no matches".
 * - **A late response never overwrites a newer one**: requests are switched, not queued.
 * - **The pager moves with the rows**: the page changes when its rows arrive, so a page that
 *   failed to load is never the page the pager names.
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
  /** The page asked for and not answered yet — what a second Next counts from. */
  private pending: number | null = null;

  constructor(
    /** Asks the server for one page. Reports its own errors; this only tracks `loading`. */
    private readonly load: (query: string, page: number) => Observable<Page<T>>,
    /** Called when a page fails, so the screen can say so in the backend's own words. */
    private readonly onError: (response: unknown) => void,
  ) {
    // `matches` exists for the pager, which counts rather than reads: the rows are never all here.
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
        this.fetch(1);
      });
    destroyRef.onDestroy(() => {
      typing.unsubscribe();
      this.inFlight?.unsubscribe();
    });
  }

  /** Fetch the first page. Call from `ngOnInit`. */
  start(): void {
    this.fetch(this.page());
  }

  /** Re-fetch the page currently on screen — after a create, an edit or a delete. */
  reload(): void {
    this.fetch(this.pending ?? this.page());
  }

  search(value: string): void {
    this.typed.next(value);
  }

  go(page: number): void {
    const target = Math.min(Math.max(1, page), this.pageCount());
    if (target === (this.pending ?? this.page())) return;
    this.fetch(target);
  }

  next(): void {
    this.go((this.pending ?? this.page()) + 1);
  }

  previous(): void {
    this.go((this.pending ?? this.page()) - 1);
  }

  current(): number {
    return this.page();
  }

  private fetch(page: number): void {
    // Switched, not queued: a slow request for "a" must not land after a fast one for "abc".
    this.inFlight?.unsubscribe();
    this.pending = page;
    this.loading.set(true);
    this.inFlight = this.load(this.query().trim(), page).subscribe({
      next: (answer) => {
        this.pending = null;
        this.rows.set(answer.results);
        this.total.set(answer.count);
        this.pages.set(answer.pages);
        this.pageSize.set(answer.page_size);
        // The page the server says this is, set with its rows: the pager names what is on screen.
        this.page.set(answer.page);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.pending = null;
        // A page past the end of a list that shrank meanwhile. The server does not clamp, it
        // answers 404 — and stale rows under a banner would leave only Previous as a way out.
        if ((response as { status?: number } | null)?.status === 404 && page > 1) {
          this.fetch(1);
          return;
        }
        this.loading.set(false);
        this.onError(response);
      },
    });
  }
}
