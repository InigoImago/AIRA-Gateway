import { Signal, computed, signal } from '@angular/core';

/** How many rows a page holds before the reader has to ask for more. */
export const PAGE_SIZE = 25;

/**
 * What a pager needs to know, without knowing what the rows are.
 *
 * `TableView<T>` is invariant in `T` (its search function takes a `T`), so the pager takes this
 * row-agnostic part and every table keeps its own row type.
 */
export interface PagedView {
  readonly matches: Signal<unknown[]>;
  readonly pageCount: Signal<number>;
  readonly firstShown: Signal<number>;
  readonly lastShown: Signal<number>;
  readonly filtered: Signal<boolean>;
  current(): number;
  next(): void;
  previous(): void;
}

/**
 * A list somebody has to find something in: filtered, then paged, in the browser.
 *
 * - **Searching resets to page one**, or a filter applied on page 4 shows an empty table and the
 *   reader concludes there are no matches.
 * - **The reader is told what they are not seeing**: "25 of 801", never a silent truncation.
 *
 * Client-side because these lists arrive in one response already; a list unbounded in time pages
 * at the server instead (`ServerTableView`, `FRD-502` §4.2).
 */
export class TableView<T> implements PagedView {
  readonly query = signal('');
  readonly page = signal(1);
  readonly pageSize = signal(PAGE_SIZE);

  /** Rows after the search box, before paging — the figure "N matches" refers to. */
  readonly matches: Signal<T[]>;
  /** The rows this page actually shows. */
  readonly rows: Signal<T[]>;
  readonly pageCount: Signal<number>;
  readonly firstShown: Signal<number>;
  readonly lastShown: Signal<number>;
  /** True when the search box is holding rows back — so an empty table can say *why*. */
  readonly filtered: Signal<boolean>;

  constructor(
    private readonly source: Signal<T[]>,
    /** Everything about a row that a person might type to find it. */
    private readonly haystack: (row: T) => string,
  ) {
    this.matches = computed(() => {
      const needle = this.query().trim().toLowerCase();
      if (!needle) return this.source();
      return this.source().filter((row) => this.haystack(row).toLowerCase().includes(needle));
    });

    this.pageCount = computed(() =>
      Math.max(1, Math.ceil(this.matches().length / this.pageSize())),
    );

    this.rows = computed(() => {
      // Clamped: the row count changes underneath a live view, and a page number that outlives its
      // rows shows an empty table with no explanation.
      const page = Math.min(this.page(), this.pageCount());
      const start = (page - 1) * this.pageSize();
      return this.matches().slice(start, start + this.pageSize());
    });

    this.firstShown = computed(() => (this.matches().length ? this.offset() + 1 : 0));
    this.lastShown = computed(() => this.offset() + this.rows().length);
    this.filtered = computed(() => this.query().trim().length > 0);
  }

  /** Type in the search box. Always returns to page one — see the class comment. */
  search(value: string): void {
    this.query.set(value);
    this.page.set(1);
  }

  go(page: number): void {
    this.page.set(Math.min(Math.max(1, page), this.pageCount()));
  }

  next(): void {
    this.go(this.page() + 1);
  }

  previous(): void {
    this.go(this.page() - 1);
  }

  /** The page actually being shown, which is not always the one that was asked for. */
  current(): number {
    return Math.min(this.page(), this.pageCount());
  }

  private offset(): number {
    return (this.current() - 1) * this.pageSize();
  }
}
