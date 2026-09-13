import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { ContentRead, ContentReadPage } from '../../core/api/models';
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
  load: () => void;
}

function setup(pages: Observable<ContentReadPage>[]) {
  TestBed.resetTestingModule();
  const queries: Record<string, unknown>[] = [];
  const service = {
    contentReads: (query: Record<string, unknown>) => {
      queries.push(query);
      return pages.shift() ?? of({ reads: [], next_cursor: null });
    },
  };
  TestBed.configureTestingModule({
    imports: [ContentReadsPage],
    providers: [{ provide: UseCaseService, useValue: service }, provideRouter([])],
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
    const { html } = setup([of({ reads: [READ], next_cursor: null })]);
    const row = html().querySelector('[data-testid="read-read-1"]')!;

    expect(row.textContent).toContain('13.09.2026');
    expect(row.querySelector('[data-testid="read-who"]')?.textContent).toContain('admin');
    expect(row.querySelector('[data-testid="read-request"]')?.textContent).toContain('req-1');
    expect(row.querySelector('[data-testid="read-ground"]')?.textContent).toContain(
      'Platform role (incident)',
    );
    expect(row.querySelector('[data-testid="read-roles"]')?.textContent).toContain(
      'Global administrator',
    );
    expect(row.querySelector('a')?.getAttribute('href')).toBe('/use-cases/kundenservice');
  });

  it('links a read to the request itself: its row in the use case, not its content', () => {
    const { html } = setup([of({ reads: [READ], next_cursor: null })]);
    const link = html().querySelector('[data-testid="read-read-1"] [data-testid="read-request"]');

    expect(link?.getAttribute('href')).toBe('/use-cases/kundenservice?tab=traces&request=req-1');
  });

  it('links a read without a use case to the cross-use-case requests', () => {
    const loose: ContentRead = { ...READ, id: 'loose', use_case: '' };
    const { html } = setup([of({ reads: [loose], next_cursor: null })]);
    const link = html().querySelector('[data-testid="read-loose"] [data-testid="read-request"]');

    expect(link?.getAttribute('href')).toBe('/requests?request=req-1');
  });

  it('says a read from before roles were kept is unknown, and names the subject without a name', () => {
    const old: ContentRead = { ...READ, id: 'old', username: null, roles: null };
    const { html } = setup([of({ reads: [old], next_cursor: null })]);
    const row = html().querySelector('[data-testid="read-old"]')!;

    expect(row.querySelector('[data-testid="read-roles"]')?.textContent).toContain('Not recorded');
    expect(row.querySelector('[data-testid="read-who"]')?.textContent).toContain('sub-1');
  });

  it('says that nothing has been read yet, rather than showing an empty table', () => {
    const { html, text } = setup([of({ reads: [], next_cursor: null })]);

    expect(html().querySelector('table')).toBeNull();
    expect(text()).toContain('No stored content has been read yet.');
  });

  it('asks with the filters and says when they match nothing', () => {
    const harness = setup([
      of({ reads: [READ], next_cursor: null }),
      of({ reads: [], next_cursor: null }),
    ]);
    harness.component.useCase.set('personalwesen');
    harness.component.reader.set('itsec');
    harness.component.load();
    harness.fixture.detectChanges();

    expect(harness.queries[1]).toMatchObject({ useCase: 'personalwesen', reader: 'itsec' });
    expect(harness.text()).toContain('No reads match these filters.');
  });

  it('pages with the cursor and appends', () => {
    const harness = setup([
      of({ reads: [READ], next_cursor: 'c1' }),
      of({ reads: [{ ...READ, id: 'read-2' }], next_cursor: null }),
    ]);
    harness.html().querySelector<HTMLButtonElement>('[data-testid="reads-load-more"]')!.click();
    harness.fixture.detectChanges();

    expect(harness.queries[1]).toMatchObject({ cursor: 'c1' });
    expect(harness.html().querySelectorAll('tbody tr').length).toBe(2);
    expect(harness.html().querySelector('[data-testid="reads-load-more"]')).toBeNull();
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
