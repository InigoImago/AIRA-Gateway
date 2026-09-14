import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { CursorPage, CursorTableView } from './cursor-table-view';

type Row = { id: string };

function build(answer: (cursor: string | null) => Observable<CursorPage<Row>>, size = 2) {
  const asked: (string | null)[] = [];
  const errors: unknown[] = [];
  const view = TestBed.runInInjectionContext(
    () =>
      new CursorTableView<Row>(
        (cursor) => {
          asked.push(cursor);
          return answer(cursor);
        },
        (response) => errors.push(response),
        size,
      ),
  );
  return { view, asked, errors };
}

/** Five rows in pages of two, cursors `c2` and `c4`. */
function pages(cursor: string | null): Observable<CursorPage<Row>> {
  const start = cursor ? Number(cursor.slice(1)) : 0;
  const rows = ['a', 'b', 'c', 'd', 'e'].slice(start, start + 2).map((id) => ({ id }));
  return of({ rows, next: start + 2 < 5 ? `c${start + 2}` : null, count: 5 });
}

describe('CursorTableView', () => {
  it('holds one page, and follows the server cursor forward and its own trail back', () => {
    const { view, asked } = build(pages);
    view.start();
    expect(view.rows().map((row) => row.id)).toEqual(['a', 'b']);
    expect([view.current(), view.pageCount(), view.firstShown(), view.lastShown()]).toEqual([
      1, 3, 1, 2,
    ]);

    view.next();
    view.next();
    expect(view.rows().map((row) => row.id)).toEqual(['e']);
    expect([view.current(), view.firstShown(), view.lastShown()]).toEqual([3, 5, 5]);

    view.previous();
    expect(view.rows().map((row) => row.id)).toEqual(['c', 'd']);
    expect(asked).toEqual([null, 'c2', 'c4', 'c2']);
  });

  it('goes nowhere past either end', () => {
    const { view, asked } = build(pages);
    view.start();
    view.previous();
    view.next();
    view.next();
    view.next();
    expect(view.current()).toBe(3);
    expect(asked).toEqual([null, 'c2', 'c4']);
  });

  it('starts a changed list again at its first page', () => {
    const { view, asked } = build(pages);
    view.start();
    view.next();
    view.reset();
    expect(view.current()).toBe(1);
    expect(asked).toEqual([null, 'c2', null]);
  });

  it('offers a next page while the server has a cursor, whatever an earlier count said', () => {
    const { view } = build(() => of({ rows: [{ id: 'a' }, { id: 'b' }], next: 'c2', count: 2 }));
    view.start();
    expect(view.pageCount()).toBe(2);
  });

  it('never lets a late answer overwrite the page asked for since', () => {
    const slow = new Subject<CursorPage<Row>>();
    let calls = 0;
    const { view } = build(() => {
      calls += 1;
      return calls === 1 ? slow : of({ rows: [{ id: 'fresh' }], next: null, count: 1 });
    });
    view.start();
    view.reset();
    slow.next({ rows: [{ id: 'stale' }], next: null, count: 1 });
    expect(view.rows().map((row) => row.id)).toEqual(['fresh']);
  });

  it('reports a failed page and stops saying it is loading', () => {
    const { view, errors } = build(() => throwError(() => ({ status: 403 })));
    view.start();
    expect(errors).toEqual([{ status: 403 }]);
    expect(view.loading()).toBe(false);
    expect(view.matches().length).toBe(0);
  });
});
