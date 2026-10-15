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
  /** Open one read's details, as a reader does: by its button. */
  const open = (id: string) => {
    html().querySelector<HTMLButtonElement>(`[data-testid="open-read-${id}"]`)!.click();
    fixture.detectChanges();
    return html().querySelector<HTMLElement>('[data-testid="read-detail"]')!;
  };
  return {
    fixture,
    queries,
    html,
    open,
    text: () => html().textContent ?? '',
    component: fixture.componentInstance as unknown as Page,
  };
}

describe('ContentReadsPage (`FRD-622` FR-6)', () => {
  it('shows who read which request, from which use case, on what ground and in which role', () => {
    const { html, open } = setup([of({ reads: [READ], next_cursor: null, count: 1 })]);
    const row = html().querySelector('[data-testid="read-read-1"]')!;

    expect(row.textContent).toContain('2026-09-13');
    expect(row.querySelector('[data-testid="read-who"]')?.textContent).toContain('admin');
    expect(row.querySelector('a')?.getAttribute('href')).toBe('/use-cases/kundenservice');
    const detail = open('read-1');
    expect(detail.querySelector('[data-testid="read-request"]')?.textContent).toContain('req-1');
    expect(detail.querySelector('[data-testid="read-ground"]')?.textContent).toContain(
      'Platform role (incident)',
    );
    expect(detail.querySelector('[data-testid="read-roles"]')?.textContent).toContain(
      'Global Administrator',
    );
  });

  it('names the roles as the server does, an installation’s own roles included (`FRD-614` FR-10)', () => {
    const read: ContentRead = { ...READ, roles: ['controlling', 'deleted-role'] };
    const { html, open } = setup([of({ reads: [read], next_cursor: null, count: 1 })]);
    open('read-1');

    // A role deleted since has no name any more; its slug is what the record still holds.
    expect(html().querySelector('[data-testid="read-roles"]')?.textContent?.trim()).toBe(
      'Controlling, deleted-role',
    );
  });

  it('still shows the log when the account cannot be loaded, naming roles by slug', () => {
    const { html, open } = setup(
      [of({ reads: [READ], next_cursor: null, count: 1 })],
      throwError(() => ({ status: 500 })),
    );
    open('read-1');

    expect(html().querySelector('[data-testid="read-roles"]')?.textContent?.trim()).toBe(
      'global-admin',
    );
  });

  it('links a read to the request itself: its row in the use case, not its content', () => {
    const { open } = setup([of({ reads: [READ], next_cursor: null, count: 1 })]);
    const link = open('read-1').querySelector('[data-testid="read-request"]');

    expect(link?.getAttribute('href')).toBe('/use-cases/kundenservice?tab=traces&request=req-1');
  });

  it('links a read without a use case to the cross-use-case requests', () => {
    const loose: ContentRead = { ...READ, id: 'loose', use_case: '' };
    const { open } = setup([of({ reads: [loose], next_cursor: null, count: 1 })]);
    const link = open('loose').querySelector('[data-testid="read-request"]');

    expect(link?.getAttribute('href')).toBe('/requests?request=req-1');
  });

  it('says a read from before roles were kept is unknown, and names the subject without a name', () => {
    const old: ContentRead = { ...READ, id: 'old', username: null, roles: null };
    const { html, open } = setup([of({ reads: [old], next_cursor: null, count: 1 })]);
    const row = html().querySelector('[data-testid="read-old"]')!;

    expect(row.querySelector('[data-testid="read-who"]')?.textContent).toContain('sub-1');
    expect(open('old').querySelector('[data-testid="read-roles"]')?.textContent).toContain(
      'Not recorded',
    );
  });

  it('keeps the table to four columns and opens the rest under the row, one row at a time', () => {
    const other: ContentRead = {
      ...READ,
      id: 'read-2',
      auth_method: 'api_key',
      credential: 'ab12',
    };
    const { html, open, fixture } = setup([
      of({
        reads: [{ ...READ, auth_method: 'oidc', credential: 'aira-console' }, other],
        next_cursor: null,
        count: 2,
      }),
    ]);
    const headers = Array.from(html().querySelectorAll('th')).map((th) => th.textContent?.trim());

    // The four a reader scans by; the request, ground, roles and access wait behind a click.
    expect(headers).toEqual(['Details', 'When', 'Reader', 'What was opened', 'Use case']);
    expect(html().querySelector('[data-testid="read-detail"]')).toBeNull();

    expect(open('read-1').querySelector('[data-testid="read-access"]')?.textContent).toContain(
      'single sign-on · aira-console',
    );
    expect(open('read-2').querySelector('[data-testid="read-access"]')?.textContent).toContain(
      'API key · ab12',
    );
    expect(html().querySelectorAll('[data-testid="read-detail"]').length).toBe(1);

    // The row itself opens and closes too; a link inside it only follows the link.
    html().querySelector<HTMLElement>('[data-testid="read-read-2"]')!.click();
    fixture.detectChanges();
    expect(html().querySelector('[data-testid="read-detail"]')).toBeNull();
    html().querySelector<HTMLElement>('[data-testid="read-read-1"]')!.click();
    fixture.detectChanges();
    expect(
      html().querySelector('[data-testid="open-read-read-1"]')?.getAttribute('aria-expanded'),
    ).toBe('true');
  });

  it('says that nothing has been read yet, rather than showing an empty table', () => {
    const { html, text } = setup([of({ reads: [], next_cursor: null, count: 0 })]);

    expect(html().querySelector('table')).toBeNull();
    expect(text()).toContain('No request has been opened yet.');
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

describe('ContentReadsPage — a typed filter', () => {
  // The filters are debounced and go to the server, so these drive the clock through the inputs a
  // reader types into: a test that sets the signals and resets the view never runs the debounce.
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function type(harness: ReturnType<typeof setup>, id: string, value: string): void {
    const input = harness.html().querySelector<HTMLInputElement>(`[data-testid="${id}"]`)!;
    input.value = value;
    input.dispatchEvent(new Event('input'));
    harness.fixture.detectChanges();
  }

  it('asks again with what was typed once the typing pauses, not once per letter', () => {
    // Each field on its own: one field's pause must not be what carries the other's change.
    const harness = setup([
      of({ reads: [READ], next_cursor: null, count: 1 }),
      of({ reads: [READ], next_cursor: null, count: 1 }),
      of({ reads: [], next_cursor: null, count: 0 }),
    ]);
    type(harness, 'reads-use-case', 'person');
    type(harness, 'reads-use-case', 'personalwesen');
    expect(harness.queries.length).toBe(1);

    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    expect(harness.queries.length).toBe(2);
    expect(harness.queries[1]).toMatchObject({ useCase: 'personalwesen', reader: '' });

    type(harness, 'reads-reader', 'itsec');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    expect(harness.queries.length).toBe(3);
    expect(harness.queries[2]).toMatchObject({ useCase: 'personalwesen', reader: 'itsec' });
    expect(harness.text()).toContain('No reads match these filters.');
  });

  it('takes a failure off the page once a later load succeeds', () => {
    // A banner saying the log could not be loaded, over the log, contradicts the screen it is on.
    const harness = setup([
      throwError(() => ({
        status: 503,
        error: { error: { code: 'unavailable', message: 'The log is unavailable.' } },
      })),
      of({ reads: [READ], next_cursor: null, count: 1 }),
    ]);
    expect(harness.html().querySelector('[role="alert"]')?.textContent).toContain(
      'The log is unavailable.',
    );

    type(harness, 'reads-reader', 'admin');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid="read-read-1"]')).not.toBeNull();
    expect(harness.html().querySelector('[role="alert"]')).toBeNull();
  });
});

describe('ContentReadsPage — what was opened', () => {
  it('says per entry whether the content or the details were opened', () => {
    const { html } = setup([
      of({
        reads: [
          { ...READ, id: 'c', kind: 'content' },
          { ...READ, id: 'm', kind: 'metadata' },
          // From a gateway that predates the field: it recorded content reads only.
          { ...READ, id: 'old' },
        ],
        next_cursor: null,
        count: 3,
      }),
    ]);
    const kind = (id: string) =>
      html().querySelector(`[data-testid="read-${id}"] [data-testid="read-kind"]`)?.textContent;

    expect(Array.from(html().querySelectorAll('th')).map((th) => th.textContent?.trim())).toContain(
      'What was opened',
    );
    expect(kind('c')?.trim()).toBe('prompt and response');
    expect(kind('m')?.trim()).toBe('details');
    expect(kind('old')?.trim()).toBe('prompt and response');
  });

  it('names the ground of a details opening by a role that reads every use case', () => {
    const { html, open } = setup([
      of({
        reads: [{ ...READ, kind: 'metadata', ground: 'oversight' }],
        next_cursor: null,
        count: 1,
      }),
    ]);
    open('read-1');
    expect(html().querySelector('[data-testid="read-ground"]')?.textContent?.trim()).toBe(
      'Platform role (oversight)',
    );
  });

  it('asks the server for one kind when it is chosen, at once and from the first page', () => {
    const harness = setup([
      of({ reads: [READ], next_cursor: null, count: 1 }),
      of({ reads: [], next_cursor: null, count: 0 }),
    ]);
    const select = harness.html().querySelector<HTMLSelectElement>('[data-testid="reads-kind"]')!;
    expect(Array.from(select.options).map((option) => option.value)).toEqual([
      '',
      'content',
      'metadata',
    ]);

    select.value = 'metadata';
    select.dispatchEvent(new Event('change'));
    harness.fixture.detectChanges();

    expect(harness.queries.at(-1)).toMatchObject({ kind: 'metadata' });
    expect(harness.queries.at(-1)?.['cursor']).toBeUndefined();
    expect(harness.text()).toContain('No reads match these filters.');
  });

  it('asks for every kind until one is chosen', () => {
    const harness = setup([of({ reads: [READ], next_cursor: null, count: 1 })]);
    expect(harness.queries[0]).toMatchObject({ kind: '' });
  });
});
