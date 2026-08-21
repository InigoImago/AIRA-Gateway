import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { CatalogModel, Register, RegisterEntry, RegisterModel } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { RegisterPage } from './register-page';

/**
 * The register screen (`FRD-608`).
 *
 * The tests worth having here are the ones about **reading** rather than about rendering: that a
 * finding is visible as a finding, that "not stored" does not print an erasure deadline, that a
 * retired use case is in the table and says so, and that a failed load is never an empty table.
 * The last is the one that matters most on this screen of all of them — an empty register and an
 * unreachable gateway look identical, and somebody would take the first as evidence.
 */

function model(over: Partial<RegisterModel> = {}): RegisterModel {
  return {
    name: 'gemini-2.5-flash',
    provider: 'vertex',
    publisher: 'google',
    regions: ['europe-west1'],
    approved: true,
    catalogued: true,
    ...over,
  };
}

function entry(over: Partial<RegisterEntry> = {}): RegisterEntry {
  return {
    slug: 'demo-uc',
    name: 'Demo',
    status: 'live',
    purpose: 'Answering customer questions',
    processing: 'Prompt to the model, answer back.',
    models: [model()],
    prompts_stored: true,
    retention_days: 7,
    own_requests_only: false,
    tools: false,
    prompt_caching: false,
    cache_ttl: '5m',
    reasoning: false,
    members: 2,
    groups: 1,
    requests: 12,
    processed_in: [{ region: 'europe-west1', provider: 'vertex', requests: 12 }],
    unexpected_regions: [],
    ...over,
  };
}

function register(over: Partial<Register> = {}): Register {
  return {
    from: '2026-08-01',
    to: '2026-09-01',
    scope: 'all',
    use_cases: [entry()],
    processed_in: [{ region: 'europe-west1', provider: 'vertex', requests: 12 }],
    catalogue: ['gemini-2.5-flash'],
    last_erasure: { ran_at: '2026-08-21T03:00:00+00:00', payloads_cleared: 1412, rows_deleted: 0 },
    ...over,
  };
}

interface Page {
  load: () => void;
  onSearch: (term: string) => void;
  toggleFindings: (only: boolean) => void;
  download: () => void;
  exporting: () => boolean;
}

function setup(
  response: Observable<Register> = of(register()),
  csv?: Observable<Blob>,
  authored: Observable<CatalogModel[]> = of([{ name: 'gemini-2.5-flash' }]),
) {
  TestBed.resetTestingModule();
  const calls: Array<{ from: string; to: string }> = [];
  const exports: Array<{ from: string; to: string }> = [];
  const service = {
    register: (from: string, to: string) => {
      calls.push({ from, to });
      return response;
    },
    registerCsv: (from: string, to: string) => {
      exports.push({ from, to });
      return csv ?? of(new Blob(['use_case\n'], { type: 'text/csv' }));
    },
    // Management's half of the catalogue comparison. Answered rather than left undefined: the
    // page asks for it on every load, and a double that throws would fail these tests for a
    // reason that has nothing to do with what they assert.
    models: () => authored,
  };
  TestBed.configureTestingModule({
    imports: [RegisterPage],
    providers: [{ provide: UseCaseService, useValue: service }],
  });
  const fixture = TestBed.createComponent(RegisterPage);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    calls,
    exports,
    element,
    component: fixture.componentInstance as unknown as Page,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    all: (id: string) => Array.from(element.querySelectorAll(`[data-testid="${id}"]`)),
  };
}

describe('RegisterPage', () => {
  it('loads the current month on arrival, without being asked for a period', () => {
    const { calls, text } = setup();

    expect(calls.length).toBe(1);
    expect(text()).toContain('Register of processing activities');
  });

  it('puts the whole row on one line: purpose, processing, models, storage, people', () => {
    const { text } = setup();

    expect(text()).toContain('Answering customer questions');
    expect(text()).toContain('Prompt to the model');
    expect(text()).toContain('gemini-2.5-flash');
    expect(text()).toContain('europe-west1');
    expect(text()).toContain('kept 7 day(s)');
    expect(text()).toContain('2 member(s), 1 group(s)');
  });

  it('prints no erasure deadline for a use case that stores nothing', () => {
    // The number is still in the database — turning storage back on should not lose it — and
    // printed beside "not stored" it reads as a promise about data that was never written.
    const { text } = setup(
      of(register({ use_cases: [entry({ prompts_stored: false, retention_days: null })] })),
    );

    expect(text()).toContain('not stored');
    expect(text()).not.toContain('day(s)');
  });

  it('keeps a retired use case in the register and marks it', () => {
    const { text, testid } = setup(of(register({ use_cases: [entry({ status: 'retired' })] })));

    expect(testid('retired')).not.toBeNull();
    expect(text()).toContain('Demo');
  });

  it('shows a region the configuration does not name as a finding', () => {
    const { all } = setup(
      of(register({ use_cases: [entry({ unexpected_regions: ['us-central1'] })] })),
    );

    expect(all('register-finding').map((el) => el.textContent?.trim())).toEqual([
      'processed in us-central1',
    ]);
  });

  it('names a released model the installation will not serve, and says which fault it is', () => {
    const { all } = setup(
      of(
        register({
          use_cases: [
            entry({
              models: [model({ name: 'ghost', catalogued: false }), model({ approved: false })],
            }),
          ],
        }),
      ),
    );

    expect(all('register-finding').map((el) => el.textContent?.trim())).toEqual([
      'ghost is not in the catalogue',
      'gemini-2.5-flash is not approved',
    ]);
  });

  it('can be narrowed to the rows with a finding, and is not by default', () => {
    const clean = entry({ slug: 'clean' });
    const flagged = entry({ slug: 'flagged', unexpected_regions: ['us-central1'] });
    const harness = setup(of(register({ use_cases: [clean, flagged] })));

    expect(harness.testid('register-row-clean')).not.toBeNull();

    harness.component.toggleFindings(true);
    harness.fixture.detectChanges();

    expect(harness.testid('register-row-clean')).toBeNull();
    expect(harness.testid('register-row-flagged')).not.toBeNull();
  });

  it('says when the sweep last ran, and warns when it never has', () => {
    const ran = setup();
    expect(ran.testid('register-erasure')?.textContent).toContain('1412');

    const never = setup(of(register({ last_erasure: null })));
    expect(never.testid('register-erasure')?.textContent).toContain('no recorded pass');
  });

  it('says whether this is the whole installation or only the reader’s own use cases', () => {
    expect(setup().testid('register-scope')?.textContent).toContain('Every use case');
    expect(
      setup(of(register({ scope: 'use_cases' }))).testid('register-scope')?.textContent,
    ).toContain('member of');
  });

  it('shows the installation’s own traffic separately from the rows', () => {
    // Break-glass keys, the console's own model checks, demo traffic — the traffic that belongs to
    // no use case, and therefore to no row above.
    const { testid } = setup();

    expect(testid('register-installation')).not.toBeNull();
  });

  it('shows no installation total to a reader who does not oversee one', () => {
    const { testid } = setup(of(register({ scope: 'use_cases', processed_in: [] })));

    expect(testid('register-installation')).toBeNull();
  });

  it('never shows an empty table when the register could not be loaded', () => {
    // **The one that matters on this screen.** An empty register and an unreachable gateway look
    // identical, and this is the screen where somebody would take the first as evidence.
    const { text, testid } = setup(throwError(() => new Error('gateway down')));

    expect(text()).toContain('Could not load the register');
    expect(testid('register-table')).toBeNull();
  });

  it('exports the period it is showing', () => {
    const harness = setup();
    harness.component.download();

    expect(harness.exports.length).toBe(1);
    expect(harness.exports[0].from).toBe(harness.calls[0].from);
    expect(harness.exports[0].to).toBe(harness.calls[0].to);
  });

  it('says so when the export fails, rather than looking like it worked', () => {
    const harness = setup(
      of(register()),
      throwError(() => new Error('nope')),
    );
    harness.component.download();
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('Could not export the register');
    expect(harness.component.exporting()).toBe(false);
  });

  it('says when the two planes disagree about the catalogue', () => {
    // `FRD-608` §4: a model the gateway could serve that Management has no row for is one no
    // screen shows and no role can remove. Both planes keep a catalogue and nothing compared them.
    const harness = setup(
      of(register({ catalogue: ['gemini-2.5-flash', 'mock-1'] })),
      undefined,
      of([{ name: 'gemini-2.5-flash' }, { name: 'gemini-2.5-pro' }]),
    );
    harness.fixture.detectChanges();

    const drift = harness.testid('register-drift');
    expect(drift?.textContent).toContain('mock-1');
    expect(drift?.textContent).toContain('gemini-2.5-pro');
  });

  it('shows nothing about the catalogue when the two planes agree', () => {
    // A panel that is always there saying "in agreement" is a panel nobody reads on the day it
    // stops saying that.
    const harness = setup();
    harness.fixture.detectChanges();

    expect(harness.testid('register-drift')).toBeNull();
  });

  it('does not report drift while Management is unreachable', () => {
    // A diff against a list that never arrived reports every model as missing — the loudest
    // possible way to be wrong, on the screen whose findings are meant to be acted on.
    const harness = setup(
      of(register()),
      undefined,
      throwError(() => new Error('down')),
    );
    harness.fixture.detectChanges();

    expect(harness.testid('register-drift')).toBeNull();
  });
});
