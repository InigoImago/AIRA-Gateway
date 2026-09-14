import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { ContentRead, ContentReadPage, Me } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ContentReadsPage } from './content-reads-page';

const READ: ContentRead = {
  id: 'read-1',
  created_at: '2026-09-13T10:15:00+00:00',
  request_log_id: 'req-1',
  use_case: 'kundenservice',
  subject: 'sub-1',
  username: 'admin',
  ground: 'incident',
  roles: ['global-admin'],
};

interface Page {
  useCase: { set: (v: string) => void };
  reader: { set: (v: string) => void };
  view: { reset: () => void };
}

/** The roles' names as the server gives them — worded apart from any the console once wrote. */
const ME: Me = {
  subject: 's',
  username: 'admin',
  email: '',
  roles: [],
  use_cases: [],
  role_labels: { 'global-admin': 'Global Administrator', controlling: 'Controlling' },
};

function setup(pages: Observable<ContentReadPage>[], me: Observable<Me> = of(ME)) {
  TestBed.resetTestingModule();
  const queries: Record<string, unknown>[] = [];
  const service = {
    contentReads: (query: Record<string, unknown>) => {
      queries.push(query);
      return pages.shift() ?? of({ reads: [], next_cursor: null, count: 0 });
    },
  };
  TestBed.configureTestingModule({
    imports: [ContentReadsPage],
    providers: [
      { provide: UseCaseService, useValue: service },
      { provide: MeService, useValue: { currency: signal(''), get: () => me } },
      provideRouter([]),
    ],
  });
  const fixture = TestBed.createComponent(ContentReadsPage);
  fixture.detectChanges();
  const html = () => fixture.nativeElement as HTMLElement;
  return {
    fixture,
    queries,
    html,
    text: () => html().textContent ?? '',
    component: fixture.componentInstance as unknown as Page,
  };
}

describe('ContentReadsPage (`FRD-622` FR-6)', () => {
  it('shows who read which request, from which use case, on what ground and in which role', () => {
    const { html } = setup([of({ reads: [READ], next_cursor: null, count: 1 })]);
    const row = html().querySelector('[data-testid="read-read-1"]')!;

    expect(row.textContent).toContain('13.09.2026');
    expect(row.querySelector('[data-testid="read-who"]')?.textContent).toContain('admin');
    expect(row.querySelector('[data-testid="read-request"]')?.textContent).toContain('req-1');
    expect(row.querySelector('[data-testid="read-ground"]')?.textContent).toContain(
      'Platform role (incident)',
    );
    expect(row.querySelector('[data-testid="read-roles"]')?.textContent).toContain(
      'Global Administrator',
    );
    expect(row.querySelector('a')?.getAttribute('href')).toBe('/use-cases/kundenservice');
  });

  it('names the roles as the server does, an installation’s own roles included (`FRD-614` FR-10)', () => {
    const read: ContentRead = { ...READ, roles: ['controlling', 'deleted-role'] };
    const { html } = setup([of({ reads: [read], next_cursor: null, count: 1 })]);

    // A role deleted since has no name any more; its slug is what the record still holds.
    expect(html().querySelector('[data-testid="read-roles"]')?.textContent?.trim()).toBe(
      'Controlling, deleted-role',
    );
  });

  it('still shows the log when the account cannot be loaded, naming roles by slug', () => {
    const { html } = setup(
      [of({ reads: [READ], next_cursor: null, count: 1 })],
      throwError(() => ({ status: 500 })),
    );

    expect(html().querySelector('[data-testid="read-roles"]')?.textContent?.trim()).toBe(
      'global-admin',
    );
  });

  it('links a read to the request itself: its row in the use case, not its content', () => {
    const { html } = setup([of({ reads: [READ], next_cursor: null, count: 1 })]);
    const link = html().querySelector('[data-testid="read-read-1"] [data-testid="read-request"]');

    expect(link?.getAttribute('href')).toBe('/use-cases/kundenservice?tab=traces&request=req-1');
  });

  it('links a read without a use case to the cross-use-case requests', () => {
    const loose: ContentRead = { ...READ, id: 'loose', use_case: '' };
    const { html } = setup([of({ reads: [loose], next_cursor: null, count: 1 })]);
    const link = html().querySelector('[data-testid="read-loose"] [data-testid="read-request"]');

    expect(link?.getAttribute('href')).toBe('/requests?request=req-1');
  });

  it('says a read from before roles were kept is unknown, and names the subject without a name', () => {
    const old: ContentRead = { ...READ, id: 'old', username: null, roles: null };
    const { html } = setup([of({ reads: [old], next_cursor: null, count: 1 })]);
    const row = html().querySelector('[data-testid="read-old"]')!;

    expect(row.querySelector('[data-testid="read-roles"]')?.textContent).toContain('Not recorded');
    expect(row.querySelector('[data-testid="read-who"]')?.textContent).toContain('sub-1');
  });

  it('says that nothing has been read yet, rather than showing an empty table', () => {
    const { html, text } = setup([of({ reads: [], next_cursor: null, count: 0 })]);

    expect(html().querySelector('table')).toBeNull();
    expect(text()).toContain('No stored content has been read yet.');
  });

  it('asks with the filters and says when they match nothing', () => {
    const harness = setup([
      of({ reads: [READ], next_cursor: null, count: 1 }),
      of({ reads: [], next_cursor: null, count: 0 }),
    ]);
    harness.component.useCase.set('personalwesen');
    harness.component.reader.set('itsec');
    harness.component.view.reset();
    harness.fixture.detectChanges();

    expect(harness.queries[1]).toMatchObject({ useCase: 'personalwesen', reader: 'itsec' });
    expect(harness.text()).toContain('No reads match these filters.');
  });

  it('shows one page at a time, and goes forward and back by cursor', () => {
    const firstPage = Array.from({ length: 50 }, (_, i) => ({ ...READ, id: `read-${i}` }));
    const harness = setup([
      of({ reads: firstPage, next_cursor: 'c1', count: 51 }),
      of({ reads: [{ ...READ, id: 'read-50' }], next_cursor: null, count: 51 }),
      of({ reads: firstPage, next_cursor: 'c1', count: 51 }),
    ]);
    const pager = () => harness.html().querySelector('[data-testid="reads-pager"]')!.textContent!;
    const rows = () => harness.html().querySelectorAll('tbody tr');
    expect(rows().length).toBe(50);
    expect(pager()).toContain('1–50 of 51 reads');
    expect(pager()).toContain('Page 1 of 2');

    harness.html().querySelector<HTMLButtonElement>('[data-testid="pager-next"]')!.click();
    harness.fixture.detectChanges();

    // The next page replaces the first: the browser holds one page, never the log.
    expect(harness.queries[1]).toMatchObject({ cursor: 'c1' });
    expect(rows().length).toBe(1);
    expect(pager()).toContain('51–51 of 51 reads');
    expect(pager()).toContain('Page 2 of 2');

    harness.html().querySelector<HTMLButtonElement>('[data-testid="pager-previous"]')!.click();
    harness.fixture.detectChanges();

    expect(harness.queries[2]['cursor']).toBeUndefined();
    expect(rows().length).toBe(50);
  });

  it('reports a refusal instead of an empty page', () => {
    const refused = throwError(() => ({
      status: 403,
      error: { error: { code: 403, message: 'The content-read log is available to …' } },
    }));
    const { html } = setup([refused]);

    expect(html().querySelector('[role="alert"]')).not.toBeNull();
    expect(html().querySelector('[data-testid="no-reads"]')).not.toBeNull();
  });
});
