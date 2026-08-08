import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { Page } from '../api/models';
import { ServerTableView } from './server-table-view';

interface Row {
  name: string;
}

function page(rows: Row[], over: Partial<Page<Row>> = {}): Page<Row> {
  return {
    count: rows.length,
    page: 1,
    page_size: 25,
    pages: 1,
    results: rows,
    ...over,
  };
}

/** A component, because `ServerTableView` injects a `DestroyRef` — as every real caller does. */
@Component({ selector: 'app-server-view-host', template: '' })
class Host {
  readonly asked = signal<{ query: string; page: number }[]>([]);
  readonly errors = signal<unknown[]>([]);
  answer: (query: string, page: number) => Observable<Page<Row>> = () => of(page([]));

  readonly view = new ServerTableView<Row>(
    (query, pageNumber) => {
      this.asked.update((all) => [...all, { query, page: pageNumber }]);
      return this.answer(query, pageNumber);
    },
    (response) => this.errors.update((all) => [...all, response]),
  );
}

function host(answer?: (query: string, page: number) => Observable<Page<Row>>) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  if (answer) fixture.componentInstance.answer = answer;
  return { fixture, instance: fixture.componentInstance, view: fixture.componentInstance.view };
}

describe('ServerTableView', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('asks the server for the first page, and reports the whole count', () => {
    const rows = [{ name: 'a' }, { name: 'b' }];
    const harness = host(() => of(page(rows, { count: 801, pages: 33 })));
    harness.view.start();

    expect(harness.instance.asked()).toEqual([{ query: '', page: 1 }]);
    expect(harness.view.rows()).toEqual(rows);
    // The total is the server's, not the length of what arrived: a list that does not say how
    // much it is not showing reads as complete.
    expect(harness.view.total()).toBe(801);
    expect(harness.view.pageCount()).toBe(33);
  });

  it('does not send a request per keystroke', () => {
    // Nine letters against the slowest endpoint in the console is nine round trips.
    const harness = host(() => of(page([])));
    harness.view.start();

    for (const value of ['k', 'ku', 'kun', 'kund']) {
      harness.view.search(value);
      vi.advanceTimersByTime(50);
    }
    vi.advanceTimersByTime(300);

    expect(harness.instance.asked().slice(1)).toEqual([{ query: 'kund', page: 1 }]);
  });

  it('does not re-ask for a query it just asked for', () => {
    const harness = host(() => of(page([])));
    harness.view.start();

    harness.view.search('kunde');
    vi.advanceTimersByTime(300);
    harness.view.search('kunde');
    vi.advanceTimersByTime(300);

    expect(harness.instance.asked().length).toBe(2);
  });

  it('returns to page one when the search changes', () => {
    // Otherwise a filter applied on page 4 asks the server for page 4 of a two-page result and
    // gets nothing, which reads as "no matches".
    const harness = host(() => of(page([{ name: 'a' }], { count: 100, pages: 4 })));
    harness.view.start();
    harness.view.next();
    expect(harness.instance.asked().at(-1)?.page).toBe(2);

    harness.view.search('anything');
    vi.advanceTimersByTime(300);

    expect(harness.instance.asked().at(-1)).toEqual({ query: 'anything', page: 1 });
  });

  it('never lets a slow answer overwrite a newer one', () => {
    // A slow request for "a" landing after a fast one for "abc" would repopulate the table with
    // rows nobody asked for, and the reader has no way to tell.
    const slow = new Subject<Page<Row>>();
    const harness = host((query) => (query === 'a' ? slow : of(page([{ name: 'abc row' }]))));
    harness.view.start();

    harness.view.search('a');
    vi.advanceTimersByTime(300);
    harness.view.search('abc');
    vi.advanceTimersByTime(300);

    slow.next(page([{ name: 'stale row' }]));
    slow.complete();

    expect(harness.view.rows()).toEqual([{ name: 'abc row' }]);
  });

  it("takes the server's word for which page this is", () => {
    // Asking for page 9 of a list that shrank to three must not leave the pager claiming 9.
    const harness = host(() => of(page([{ name: 'a' }], { page: 3, pages: 3, count: 60 })));
    harness.view.start();
    harness.view.go(9);

    expect(harness.view.current()).toBe(3);
  });

  it('does not walk past either end', () => {
    const harness = host(() => of(page([{ name: 'a' }], { count: 40, pages: 2 })));
    harness.view.start();

    harness.view.previous();
    expect(harness.instance.asked().length).toBe(1);

    harness.view.next();
    harness.view.next();
    expect(harness.instance.asked().at(-1)?.page).toBe(2);
  });

  it('reports a failed page instead of leaving the reader with stale rows and no word', () => {
    const harness = host(() => throwError(() => ({ status: 500 })));
    harness.view.start();

    expect(harness.instance.errors().length).toBe(1);
    expect(harness.view.loading()).toBe(false);
  });

  it('counts nothing as nothing rather than as one row', () => {
    const harness = host(() => of(page([], { count: 0, pages: 1 })));
    harness.view.start();

    expect(harness.view.firstShown()).toBe(0);
    expect(harness.view.lastShown()).toBe(0);
  });

  it('stops asking once the component is gone', () => {
    // A debounce that outlives its screen is a request against an endpoint nobody is reading.
    const harness = host(() => of(page([])));
    harness.view.start();
    harness.view.search('kunde');

    harness.fixture.destroy();
    vi.advanceTimersByTime(1000);

    expect(harness.instance.asked().length).toBe(1);
  });
});
