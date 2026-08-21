import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { TablePager } from './table-pager';
import { PAGE_SIZE, TableView } from './table-view';

interface Row {
  name: string;
}

function rows(count: number, prefix = 'row'): Row[] {
  return Array.from({ length: count }, (_, index) => ({ name: `${prefix}-${index}` }));
}

function view(initial: Row[]) {
  const source = signal(initial);
  return { source, view: new TableView<Row>(source, (row) => row.name) };
}

describe('TableView', () => {
  it('shows one page and says how much it is not showing', () => {
    // A silent truncation reads as "that is everything", which is how a list of 801 use cases
    // came to look like a list of 25.
    const { view: v } = view(rows(60));

    expect(v.rows().length).toBe(PAGE_SIZE);
    expect(v.matches().length).toBe(60);
    expect(v.firstShown()).toBe(1);
    expect(v.lastShown()).toBe(PAGE_SIZE);
    expect(v.pageCount()).toBe(3);
  });

  it('walks pages without repeating or skipping a row', () => {
    const { view: v } = view(rows(60));
    const seen: string[] = [];

    for (let page = 1; page <= v.pageCount(); page += 1) {
      v.go(page);
      seen.push(...v.rows().map((row) => row.name));
    }

    expect(seen.length).toBe(60);
    expect(new Set(seen).size).toBe(60);
  });

  it('does not walk past either end', () => {
    const { view: v } = view(rows(30));

    v.previous();
    expect(v.current()).toBe(1);

    v.go(99);
    expect(v.current()).toBe(2);
    v.next();
    expect(v.current()).toBe(2);
  });

  it('filters on anything a person might type', () => {
    const { view: v } = view([{ name: 'Kundenservice' }, { name: 'entwicklung' }]);

    v.search('KUNDEN');
    expect(v.rows().map((row) => row.name)).toEqual(['Kundenservice']);
    expect(v.filtered()).toBe(true);

    v.search('   ');
    expect(v.rows().length).toBe(2);
    expect(v.filtered()).toBe(false);
  });

  it('returns to page one when the search changes', () => {
    // A filter applied on page 4 that leaves you on page 4 shows an empty table, and the reader
    // concludes there are no matches when there are five.
    const { view: v } = view(rows(100));
    v.go(4);

    v.search('row-1');

    expect(v.current()).toBe(1);
    expect(v.rows().length).toBeGreaterThan(0);
  });

  it('does not strand the reader on a page that no longer exists', () => {
    // The row count changes underneath a live view. A page number that outlives its rows shows an
    // empty table with no explanation, which reads as a broken screen rather than a shrunk list.
    const { source, view: v } = view(rows(100));
    v.go(4);

    source.set(rows(3));

    expect(v.current()).toBe(1);
    expect(v.rows().length).toBe(3);
    expect(v.lastShown()).toBe(3);
  });

  it('counts nothing as nothing rather than as one row', () => {
    const { view: v } = view([]);

    expect(v.rows()).toEqual([]);
    expect(v.firstShown()).toBe(0);
    expect(v.lastShown()).toBe(0);
    expect(v.pageCount()).toBe(1);
  });
});

@Component({
  selector: 'app-pager-host',
  imports: [TablePager],
  template: `<app-table-pager [view]="view" noun="use cases" />`,
})
class Host {
  readonly source = signal<Row[]>(rows(60));
  readonly view = new TableView<Row>(this.source, (row) => row.name);
}

function host() {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    element,
    instance: fixture.componentInstance,
    text: () => element.textContent ?? '',
    click: (id: string) => {
      element.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
      fixture.detectChanges();
    },
  };
}

describe('TablePager', () => {
  it('names what is on screen out of what there is', () => {
    const { text } = host();
    expect(text()).toContain('1–25 of 60 use cases');
    expect(text()).toContain('Page 1 of 3');
  });

  it('moves a page at a time, and stops at the ends', () => {
    const harness = host();

    harness.click('pager-next');
    expect(harness.text()).toContain('26–50 of 60');

    harness.click('pager-next');
    expect(harness.text()).toContain('51–60 of 60');
    expect(
      (harness.element.querySelector('[data-testid="pager-next"]') as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('keeps its two buttons adjacent, so a changing page count cannot move them', () => {
    /**
     * **The geometric property, asserted structurally.** The position label sat *between* Previous
     * and Next; the group is pinned to the right, so Next held still and the label pushed
     * Previous. A field sweep measured 8 px on the use-case list the moment a search changed the
     * page count from two digits to one.
     *
     * Asserted here rather than in the browser, and that is the point: reproducing the pixel
     * needs a list long enough to page **and** a search term that changes the count's digit width,
     * which is a fact about how much demo data a machine happens to hold. A browser test written
     * that way passed against the unfixed console on the first try — the vacuous pass this
     * project refuses. What the fix really establishes is that nothing sits between the two
     * buttons, and that is true of the markup regardless of the data.
     */
    const { element } = host();
    const previous = element.querySelector('[data-testid="pager-previous"]')!;
    const next = element.querySelector('[data-testid="pager-next"]')!;

    expect(previous).not.toBeNull();
    expect(next).not.toBeNull();
    expect(previous.nextElementSibling, 'something sits between the two pager buttons').toBe(next);
    expect(previous.previousElementSibling?.textContent).toContain('Page 1 of 3');
  });

  it('says a filtered list is filtered, so it cannot read as the whole', () => {
    const harness = host();
    harness.instance.view.search('row-1');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('(filtered)');
  });

  it('shows the count even on a single page, and no controls', () => {
    // A control that appears only once a list grows teaches nobody it exists — and the count is
    // worth having anyway: a reader who cannot see a total cannot tell a filtered list from a
    // complete one.
    const harness = host();
    harness.instance.source.set(rows(8));
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('1–8 of 8 use cases');
    expect(harness.element.querySelector('[data-testid="pager-next"]')).toBeNull();
  });

  it('says "no rows" rather than "0–0 of 0"', () => {
    const harness = host();
    harness.instance.source.set([]);
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('No use cases');
  });
});
