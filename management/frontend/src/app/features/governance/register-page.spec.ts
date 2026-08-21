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
  applyPreset: (preset: 'this-month' | 'custom') => void;
  toggle: (slug: string) => void;
  isOpen: (slug: string) => boolean;
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

  it('puts on the closed row only what a reader compares rows by', () => {
    // Four columns and a caret, where this began with nine. The test is as much about what is
    // **absent** as about what is there: nine columns is not more information, it is the same
    // information arranged so none of it can be scanned.
    const { text } = setup();

    expect(text()).toContain('Demo');
    expect(text()).toContain('Answering customer questions');
    expect(text()).toContain('kept 7 day(s)');

    expect(text()).not.toContain('Prompt to the model');
    expect(text()).not.toContain('gemini-2.5-flash');
    expect(text()).not.toContain('2 member(s), 1 group(s)');
  });

  it('opens a row onto everything the columns no longer carry', () => {
    const harness = setup();
    harness.component.toggle('demo-uc');
    harness.fixture.detectChanges();

    const text = harness.text();
    expect(text).toContain('Prompt to the model');
    expect(text).toContain('gemini-2.5-flash');
    expect(text).toContain('europe-west1');
    expect(text).toContain('2 member(s), 1 group(s)');
    expect(text).toContain('12 in this period');
  });

  it('closes a row it opened, and leaves the others alone', () => {
    const harness = setup(
      of(register({ use_cases: [entry({ slug: 'one' }), entry({ slug: 'two' })] })),
    );

    harness.component.toggle('one');
    harness.component.toggle('two');
    harness.fixture.detectChanges();
    expect(harness.component.isOpen('one')).toBe(true);
    expect(harness.component.isOpen('two')).toBe(true);

    harness.component.toggle('one');
    harness.fixture.detectChanges();
    expect(harness.component.isOpen('one')).toBe(false);
    expect(harness.component.isOpen('two')).toBe(true);
  });

  it('lets several rows be open at once — a register is read by comparing', () => {
    // The deliberate difference from the request list, which keeps one open. Opening a request
    // fetches its payload; here everything is already loaded, and the question this screen answers
    // is *these two side by side*. One-at-a-time would close the row being compared against.
    const harness = setup(
      of(
        register({
          use_cases: [
            entry({ slug: 'alpha', processing: 'Alpha processing' }),
            entry({ slug: 'beta', processing: 'Beta processing' }),
          ],
        }),
      ),
    );

    harness.component.toggle('alpha');
    harness.component.toggle('beta');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('Alpha processing');
    expect(harness.text()).toContain('Beta processing');
  });

  it('marks the open row for a screen reader as well as for an eye', () => {
    const harness = setup();
    const button = () => harness.testid('register-open-demo-uc');

    expect(button()?.getAttribute('aria-expanded')).toBe('false');

    harness.component.toggle('demo-uc');
    harness.fixture.detectChanges();

    expect(button()?.getAttribute('aria-expanded')).toBe('true');
  });

  it('never swaps the caret for a second glyph, which is a different width', () => {
    // The small constant jiggle nobody can point at: `▸` and `▾` do not measure the same in the
    // fonts a console is read in, so a table that swaps them moves every cell on the row. One
    // glyph, rotated by CSS.
    const harness = setup();
    const caret = () =>
      harness.testid('register-open-demo-uc')?.querySelector('.row-toggle__caret');

    const before = caret()?.textContent;
    harness.component.toggle('demo-uc');
    harness.fixture.detectChanges();

    expect(caret()?.textContent).toBe(before);
    expect(caret()?.classList.contains('is-open')).toBe(true);
  });

  it('declares a fixed column width for every column, so an open row cannot resize them', () => {
    // The columns of an automatic table are measured from every cell it holds, the opened detail
    // included — so opening one row moves the columns of all the others. The guard is structural
    // rather than visual: a `<col>` per column, and the detail spanning exactly that many.
    const { element } = setup();
    const table = element.querySelector('[data-testid="register-table"]');
    const columns = table?.querySelectorAll('colgroup > col') ?? [];
    const headers = table?.querySelectorAll('thead th') ?? [];

    expect(columns.length).toBe(headers.length);
    expect(table?.classList.contains('table--register')).toBe(true);
  });

  it('spans the detail across exactly the columns above it', () => {
    // A colspan smaller than the table leaves a phantom column; larger, and some browsers grow
    // the table by one. Either way the row above stops lining up with the row below.
    const harness = setup();
    harness.component.toggle('demo-uc');
    harness.fixture.detectChanges();

    const table = harness.element.querySelector('[data-testid="register-table"]');
    const detail = table?.querySelector('.row-detail > td');
    expect(detail?.getAttribute('colspan')).toBe(
      String(table?.querySelectorAll('colgroup > col').length),
    );
  });

  it('prints no erasure deadline for a use case that stores nothing', () => {
    // The number is still in the database — turning storage back on should not lose it — and
    // printed beside "not stored" it reads as a promise about data that was never written.
    const { text } = setup(
      of(register({ use_cases: [entry({ prompts_stored: false, retention_days: null })] })),
    );

    expect(text()).toContain('not stored');
    expect(text()).not.toContain('day(s)');
    // And it is on the closed row, because "does this use case keep prompts" is the question the
    // table is scanned for rather than a detail about one use case.
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

  it('opens a row that has nothing filled in without printing blanks', () => {
    // Every one of these is a field a use case is allowed to leave empty, and an empty cell in a
    // register reads as "not applicable" rather than "nobody wrote it down".
    const harness = setup(
      of(
        register({
          use_cases: [
            entry({ purpose: '', processing: '', models: [], processed_in: [], requests: 0 }),
          ],
        }),
      ),
    );
    harness.component.toggle('demo-uc');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('none released');
    expect(harness.text()).toContain('no traffic in this period');
    expect(harness.text()).toContain('—');
  });

  it('says of each released model whether it is catalogued, where, and whether approved', () => {
    const harness = setup(
      of(
        register({
          use_cases: [
            entry({
              models: [
                model({ name: 'ghost', catalogued: false }),
                model({ name: 'unregioned', regions: [], approved: false }),
              ],
            }),
          ],
        }),
      ),
    );
    harness.component.toggle('demo-uc');
    harness.fixture.detectChanges();

    const text = harness.text();
    expect(text).toContain('not in the catalogue');
    // A model addressed by name alone, which most dialects do — not a missing region.
    expect(text).toContain('no region');
    expect(text).toContain('not approved');
  });

  it('carries the controls and the members-see-their-own rule into the detail', () => {
    const harness = setup(
      of(
        register({
          use_cases: [
            entry({
              tools: true,
              prompt_caching: true,
              cache_ttl: '15m',
              reasoning: true,
              own_requests_only: true,
            }),
          ],
        }),
      ),
    );
    harness.component.toggle('demo-uc');
    harness.fixture.detectChanges();

    const text = harness.text();
    expect(text).toContain('tools on');
    expect(text).toContain('caching 15m');
    expect(text).toContain('reasoning on');
    expect(text).toContain('members see only their own requests');
  });

  it('asks for a period of its own without reloading until it is given one', () => {
    // "Custom…" offers two date fields and a Show button. Reloading on the moment the preset
    // changes would fetch the *old* window and look like the choice did nothing.
    const harness = setup();
    expect(harness.calls.length).toBe(1);

    harness.component.applyPreset('custom');
    harness.fixture.detectChanges();

    expect(harness.calls.length).toBe(1);
    expect(harness.element.querySelector('#register-from')).not.toBeNull();
    expect(harness.element.querySelector('#register-to')).not.toBeNull();
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
