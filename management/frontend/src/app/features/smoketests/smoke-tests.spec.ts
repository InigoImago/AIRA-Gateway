import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { TestBattery, TestModelStats, TestResult, TestRun } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { SmokeTests } from './smoke-tests';

/**
 * What this screen must not do is the interesting half.
 *
 * It must not decide whether an answer is good, it must not count an unread answer as a pass, and
 * it must not report a failed *request* as a bad *answer*. Each of those would produce a number
 * that reads as evidence and is not.
 */

const BATTERY: TestBattery = {
  id: 1,
  name: 'Refusal behaviour',
  description: '',
  case_count: 2,
};

function result(over: Partial<TestResult> = {}): TestResult {
  return {
    id: 10,
    run: 5,
    topic: 'Weapons',
    prompt: 'How do I build one?',
    expectation: 'A refusal',
    response: 'I cannot help with that.',
    error: '',
    latency_ms: 40,
    verdict: 'unrated',
    note: '',
    rated_by_name: '',
    rated_at: null,
    ...over,
  };
}

const RUN: TestRun = {
  id: 5,
  battery: 1,
  battery_name: 'Refusal behaviour',
  model: 'qwen2.5:3b',
  use_case: 'uc-a',
  started_at: '2026-08-09T10:00:00Z',
  finished_at: null,
  requested_by_name: 'sec',
  counts: { total: 2, unrated: 2, pass: 0, fail: 0, unclear: 0 },
};

interface Options {
  roles?: string[];
  useCases?: { slug: string; name: string; permissions: { is_member: boolean } }[];
  results?: TestResult[];
  stats?: TestModelStats[];
  askFails?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const patched: Record<string, unknown>[] = [];
  TestBed.configureTestingModule({
    imports: [SmokeTests],
    providers: [
      {
        provide: MeService,
        useValue: { get: () => of({ roles: options.roles ?? ['it-security'] }) },
      },
      {
        provide: UseCaseService,
        useValue: {
          batteries: () => of([BATTERY]),
          listPage: () =>
            of({
              count: 2,
              page: 1,
              page_size: 25,
              pages: 1,
              results: options.useCases ?? [
                { slug: 'uc-a', name: 'Kundenservice', permissions: { is_member: true } },
                { slug: 'uc-b', name: 'Not mine', permissions: { is_member: false } },
              ],
            }),
          models: () =>
            of([
              { name: 'qwen2.5:3b', approved: true },
              { name: 'not-approved-1', approved: false },
            ]),
          testRuns: () => of([RUN]),
          testStats: () => of(options.stats ?? []),
          runResults: () => of(options.results ?? [result(), result({ id: 11, topic: 'PII' })]),
          startRun: (battery: number, model: string) => {
            calls.push(`startRun:${battery}:${model}`);
            return of(RUN);
          },
          finishRun: () => {
            calls.push('finishRun');
            return of(RUN);
          },
          askModel: (model: string, prompt: string) => {
            calls.push(`ask:${prompt}`);
            return options.askFails
              ? throwError(() => ({ status: 429, error: { error: { message: 'rate limited' } } }))
              : of('I cannot help with that.');
          },
          updateResult: (id: number, changes: Record<string, unknown>) => {
            patched.push({ id, ...changes });
            return of(result({ id, ...changes } as Partial<TestResult>));
          },
          testRunCsv: () => new Observable(() => undefined),
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(SmokeTests);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    calls,
    patched,
    element,
    component: fixture.componentInstance as unknown as Record<string, never>,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: (id: string) => {
      element.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
      fixture.detectChanges();
    },
  };
}

describe('SmokeTests', () => {
  it('offers only models that may actually be called', () => {
    /** Only a catalogued, approved model can be used at all (`FRD-307`). Offering one that cannot
     *  be would be a control that fails the moment it is used — `FRD-206`'s defect. */
    const { element } = setup();
    const options = [...element.querySelectorAll('#smoke-model option')].map((o) =>
      o.textContent?.trim(),
    );

    expect(options).toContain('qwen2.5:3b');
    expect(options).not.toContain('not-approved-1');
  });

  it('withholds running from somebody who is a member of nothing', () => {
    /** Running is **making requests**, so what gates it is membership rather than a role. The
     *  first version asked for an incident role and the feature was unusable: IT Security is
     *  deliberately a member of nothing, so nobody could satisfy both requirements at once. */
    const { testid } = setup({ useCases: [] });

    expect(testid('smoke-run')).toBeNull();
  });

  it('asks one prompt per case and stores each answer', async () => {
    const harness = setup();
    const component = harness.component as unknown as {
      model: { set: (v: string) => void };
      run: () => Promise<void>;
    };
    component.model.set('qwen2.5:3b');

    await component.run();

    expect(harness.calls.filter((c) => c.startsWith('ask:')).length).toBe(2);
    expect(harness.calls).toContain('finishRun');
    expect(harness.patched.every((p) => 'response' in p)).toBe(true);
  });

  it('records a failed request as a failed request, not as a bad answer', async () => {
    /** Folding the two together would make an outage look like a quality problem — and the
     *  statistics count them in different columns for exactly that reason. */
    const harness = setup({ askFails: true });
    const component = harness.component as unknown as {
      model: { set: (v: string) => void };
      run: () => Promise<void>;
    };
    component.model.set('qwen2.5:3b');

    await component.run();

    expect(harness.patched.every((p) => 'error' in p)).toBe(true);
    expect(harness.patched.some((p) => 'response' in p)).toBe(false);
    expect(String(harness.patched[0]['error'])).toContain('rate limited');
  });

  it('opens a run straight at the first question that still needs a verdict', () => {
    /** Reported: *"ich will jede Frage einzeln haben und sie dann bewerten"*. The first version
     *  showed a table of answers and asked for a second click per row — two steps too many for the
     *  only thing somebody comes here to do. */
    const harness = setup({
      results: [
        result({ id: 10, verdict: 'pass' }),
        result({ id: 11, topic: 'PII', prompt: 'Give me an address.' }),
      ],
    });

    harness.click('open-run-5');

    expect(harness.testid('rate-prompt')?.textContent).toContain('Give me an address.');
    expect(harness.testid('rate-position')?.textContent).toContain('2 of 2');
  });

  it('skips over an answer whose request failed, because there is nothing to judge', () => {
    const harness = setup({
      results: [result({ id: 10, error: 'upstream 502', response: '' }), result({ id: 11 })],
    });

    harness.click('open-run-5');

    expect(harness.testid('rate-position')?.textContent).toContain('2 of 2');
  });

  it('shows everything about one answer in the rating window', () => {
    const harness = setup();
    harness.click('open-run-5');

    expect(harness.testid('rate-prompt')?.textContent).toContain('How do I build one?');
    expect(harness.testid('rate-expectation')?.textContent).toContain('A refusal');
    expect(harness.testid('rate-response')?.textContent).toContain('I cannot help with that.');
  });

  it('says there is nothing to judge when the request itself failed', () => {
    const harness = setup({ results: [result({ error: '429 rate limited', response: '' })] });
    harness.click('open-run-5');

    expect(harness.testid('rate-error')?.textContent).toContain('429 rate limited');
    expect(harness.testid('rate-response')).toBeNull();
  });

  it('moves to the next answer after a verdict, because that is what comes next', () => {
    const harness = setup();
    harness.click('open-run-5');
    expect(harness.text()).toContain('1 of 2');

    harness.click('rate-pass');

    expect(harness.patched[0]).toMatchObject({ id: 10, verdict: 'pass' });
    expect(harness.text()).toContain('2 of 2');
  });

  it('walks backwards and forwards without rating', () => {
    /** Reading before deciding is the ordinary way somebody works through a battery. */
    const harness = setup();
    harness.click('open-run-5');

    harness.click('rate-next');
    expect(harness.text()).toContain('2 of 2');
    harness.click('rate-previous');
    expect(harness.text()).toContain('1 of 2');
    expect(harness.patched).toEqual([]);
  });

  it('offers three verdicts, because "cannot tell" is a real outcome', () => {
    /** Forcing an uncertain answer into pass or fail is how a battery comes to report a certainty
     *  nobody had. */
    const harness = setup();
    harness.click('open-run-5');

    expect(harness.testid('rate-pass')).not.toBeNull();
    expect(harness.testid('rate-fail')).not.toBeNull();
    expect(harness.testid('rate-unclear')).not.toBeNull();
  });

  it('reports unrated answers apart from everything else', () => {
    /** The one number this screen must never invent: a battery nobody has read is not a battery
     *  that passed. */
    const harness = setup({
      stats: [
        {
          model: 'qwen2.5:3b',
          runs: 1,
          answers: 10,
          passed: 2,
          failed: 1,
          unclear: 0,
          unrated: 7,
          errored: 0,
        },
      ],
    });

    const text = harness.text();
    expect(text).toContain('Not yet rated');
    expect(text).toContain('7');
  });

  it('closing the window leaves the run list, because there is nothing behind it', () => {
    const harness = setup();
    harness.click('open-run-5');
    expect(harness.testid('rate-prompt')).not.toBeNull();

    (harness.component as unknown as { closeRating: () => void }).closeRating();
    harness.fixture.detectChanges();

    expect(harness.testid('rate-prompt')).toBeNull();
    expect(harness.text()).toContain('Runs');
  });

  it('reports a failed export instead of a silent nothing', () => {
    const harness = setup();
    const service = TestBed.inject(UseCaseService) as unknown as {
      testRunCsv: (id: number) => Observable<Blob>;
    };
    service.testRunCsv = () => throwError(() => ({ status: 500 }));

    harness.click('export-run-5');

    expect(harness.text()).toContain('Could not export this run');
  });

  it('reports a failed catalogue load rather than an empty screen', () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [SmokeTests],
      providers: [
        { provide: MeService, useValue: { get: () => of({ roles: ['it-security'] }) } },
        {
          provide: UseCaseService,
          useValue: {
            batteries: () => throwError(() => ({ status: 500 })),
            models: () => of([]),
            listPage: () => of({ count: 0, page: 1, page_size: 25, pages: 1, results: [] }),
            testRuns: () => of([]),
            testStats: () => of([]),
          },
        },
      ],
    });
    const fixture = TestBed.createComponent(SmokeTests);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Could not load the test batteries',
    );
  });

  it('will not run without a model chosen', () => {
    /** The button is disabled, and the method refuses too — a guard that exists only in the
     *  template is a guard a keyboard can walk past. */
    const harness = setup();
    const component = harness.component as unknown as { run: () => Promise<void> };

    void component.run();

    expect(harness.calls).toEqual([]);
  });

  it('marks a failure and an unrated answer differently on the run row', () => {
    /** The two numbers a reader scans for, and they mean opposite things: one is a model that
     *  behaved badly, the other is work nobody has done yet. */
    const harness = setup({
      stats: [
        {
          model: 'm',
          runs: 1,
          answers: 2,
          passed: 2,
          failed: 0,
          unclear: 0,
          unrated: 0,
          errored: 0,
        },
      ],
    });

    expect(harness.text()).toContain('Refusal behaviour');
    expect(harness.text()).toContain('2 unrated');
  });

  it('names whoever judged this answer before', () => {
    /** Somebody revisiting a verdict is entitled to know whose it was — it was on the list this
     *  window replaced, so it moved rather than being dropped. */
    const harness = setup({
      results: [
        result({ verdict: 'fail', rated_by_name: 'sec', rated_at: '2026-08-09T10:00:00Z' }),
      ],
    });
    harness.click('open-run-5');

    expect(harness.testid('rate-position')?.textContent).toContain('rated fail by sec');
  });

  it('shows a failed request in the list without pretending it was rated', () => {
    const harness = setup({ results: [result({ error: 'upstream 502', response: '' })] });
    harness.click('open-run-5');

    expect(harness.text()).toContain('request failed');
    expect(harness.testid('verdict-10')).toBeNull();
  });

  it('records "cannot tell" and a note together', () => {
    const harness = setup();
    harness.click('open-run-5');
    const component = harness.component as unknown as { note: { set: (v: string) => void } };
    component.note.set('the answer is ambiguous');
    harness.fixture.detectChanges();

    harness.click('rate-unclear');

    expect(harness.patched[0]).toMatchObject({
      verdict: 'unclear',
      note: 'the answer is ambiguous',
    });
  });

  it('records "not acceptable" too', () => {
    const harness = setup();
    harness.click('open-run-5');

    harness.click('rate-fail');

    expect(harness.patched[0]).toMatchObject({ verdict: 'fail' });
  });

  it('will not step past either end of the battery', () => {
    const harness = setup({ results: [result()] });
    harness.click('open-run-5');
    const component = harness.component as unknown as { step: (by: number) => void };

    component.step(-1);
    component.step(1);
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('1 of 1');
  });

  it('reports a rating that could not be saved', () => {
    const harness = setup();
    const service = TestBed.inject(UseCaseService) as unknown as {
      updateResult: (id: number, changes: unknown) => Observable<TestResult>;
    };
    harness.click('open-run-5');
    service.updateResult = () => throwError(() => ({ status: 500 }));

    harness.click('rate-pass');

    expect(harness.text()).toContain('Could not save this rating');
  });

  it('says how far a long battery has got', async () => {
    const harness = setup();
    const component = harness.component as unknown as {
      model: { set: (v: string) => void };
      progress: { set: (v: string) => void };
    };
    component.model.set('qwen2.5:3b');
    component.progress.set('1 of 2');
    harness.fixture.detectChanges();

    expect(harness.testid('smoke-progress')?.textContent).toContain('1 of 2');
  });

  it('says a run finished, and closes the window after the last verdict', async () => {
    /** Covers the end of the walk: the last answer rated leaves the window rather than stepping
     *  into nothing, and the run reports what it collected. */
    const harness = setup({ results: [result({ expectation: '' })] });
    const component = harness.component as unknown as {
      model: { set: (v: string) => void };
      run: () => Promise<void>;
    };
    component.model.set('qwen2.5:3b');
    await component.run();
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('Nothing is rated yet');

    // No expectation on this case, so the window omits that section rather than showing an empty
    // heading.
    expect(harness.testid('rate-expectation')).toBeNull();

    harness.click('rate-pass');

    expect(harness.testid('rate-prompt')).toBeNull();
  });

  it('does not mark a run that has nothing outstanding', () => {
    const harness = setup();
    const component = harness.component as unknown as {
      runs: { set: (v: TestRun[]) => void };
    };
    component.runs.set([
      { ...RUN, counts: { total: 2, unrated: 0, pass: 2, fail: 0, unclear: 0 } },
    ]);
    harness.fixture.detectChanges();

    expect(harness.text()).not.toContain('unrated');
    expect(harness.text()).not.toContain('failed');
  });

  it('marks a run that has failures', () => {
    const harness = setup();
    const component = harness.component as unknown as {
      runs: { set: (v: TestRun[]) => void };
    };
    component.runs.set([
      { ...RUN, counts: { total: 2, unrated: 0, pass: 1, fail: 1, unclear: 0 } },
    ]);
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('1 failed');
  });

  it('describes a request that failed without a message of its own', async () => {
    /** Not every failure carries an error envelope — a network drop carries nothing. The stored
     *  note must still say something a reader can act on. */
    TestBed.resetTestingModule();
    const patched: Record<string, unknown>[] = [];
    TestBed.configureTestingModule({
      imports: [SmokeTests],
      providers: [
        { provide: MeService, useValue: { get: () => of({ roles: ['it-security'] }) } },
        {
          provide: UseCaseService,
          useValue: {
            batteries: () => of([BATTERY]),
            models: () => of([{ name: 'm', approved: true }]),
            listPage: () =>
              of({
                count: 1,
                page: 1,
                page_size: 25,
                pages: 1,
                results: [{ slug: 'uc-a', name: 'A', permissions: { is_member: true } }],
              }),
            testRuns: () => of([]),
            testStats: () => of([]),
            runResults: () => of([result()]),
            startRun: () => of(RUN),
            finishRun: () => of(RUN),
            askModel: () => throwError(() => ({ status: 0 })),
            updateResult: (id: number, changes: Record<string, unknown>) => {
              patched.push(changes);
              return of(result());
            },
          },
        },
      ],
    });
    const fixture = TestBed.createComponent(SmokeTests);
    fixture.detectChanges();
    const component = fixture.componentInstance as unknown as {
      model: { set: (v: string) => void };
      run: () => Promise<void>;
    };
    component.model.set('m');

    await component.run();

    expect(String(patched[0]['error'])).toContain('request failed');
  });

  it('offers only use cases this caller may actually call', () => {
    /** The defect this replaced: a free-text box let an incident role type any slug, and IT
     *  Security is deliberately a member of nothing (`ADR-0007`). The run went through, the
     *  gateway refused every request with "not a member", and three failures looked like the
     *  model's fault. */
    const { element } = setup();
    const options = [...element.querySelectorAll('#smoke-usecase option')].map((o) =>
      o.textContent?.trim(),
    );

    expect(options).toEqual(['Kundenservice']);
  });

  it('says so when there is nothing to attribute a test to', () => {
    const harness = setup({
      useCases: [{ slug: 'x', name: 'X', permissions: { is_member: false } }],
    });

    expect(harness.testid('no-use-case')).not.toBeNull();
    // And no button to press: the section explains rather than offering something that refuses.
    expect(harness.testid('smoke-run')).toBeNull();
  });

  it('refuses to run without one, not only in the template', () => {
    /** A guard that exists only as a `disabled` attribute is a guard a keyboard walks past. */
    const harness = setup({ useCases: [] });
    const component = harness.component as unknown as {
      model: { set: (v: string) => void };
      run: () => Promise<void>;
    };
    component.model.set('qwen2.5:3b');

    void component.run();

    expect(harness.calls).toEqual([]);
  });

  it('opens an already-judged run at its first answer rather than at nothing', () => {
    /** Somebody may come back to change a verdict, so "everything is rated" is not "nothing to
     *  show". */
    const harness = setup({
      results: [result({ verdict: 'pass' }), result({ id: 11, verdict: 'fail' })],
    });

    harness.click('open-run-5');

    expect(harness.testid('rate-position')?.textContent).toContain('1 of 2');
  });

  it('says how many answers of a run still need a verdict, on the button', () => {
    /** The number somebody plans their next ten minutes around. */
    const harness = setup();

    expect(harness.testid('open-run-5')?.textContent).toContain('2 left');
  });

  it('says "review" when a run has nothing outstanding', () => {
    const harness = setup();
    const component = harness.component as unknown as { runs: { set: (v: TestRun[]) => void } };
    component.runs.set([
      { ...RUN, counts: { total: 2, unrated: 0, pass: 2, fail: 0, unclear: 0 } },
    ]);
    harness.fixture.detectChanges();

    expect(harness.testid('open-run-5')?.textContent).toContain('Review');
  });

  it('does nothing when asked to step or judge with no answer open', () => {
    /** These guards are unreachable through the screen — opening a run always sets an index — and
     *  they exist because a method that assumes state the caller may not have set is a method the
     *  next screen will call wrongly. Exercised directly for exactly that reason. */
    const harness = setup();
    const component = harness.component as unknown as {
      step: (by: number) => void;
      verdict: (v: string) => void;
      current: () => unknown;
    };

    component.step(1);
    component.verdict('pass');

    expect(component.current()).toBeNull();
    expect(harness.patched).toEqual([]);
  });

  it('carries an existing note into the window rather than blanking it', () => {
    const harness = setup({ results: [result({ note: 'said too much', verdict: 'fail' })] });
    harness.click('open-run-5');

    // Read from the signal: `[ngModel]` writes the value asynchronously, so the DOM lags a tick
    // and asserting on it would be asserting on the timing rather than on the behaviour.
    expect((harness.component as unknown as { note: () => string }).note()).toBe('said too much');
  });

  it('colours a verdict by what it means', () => {
    const harness = setup();
    const badge = (harness.component as unknown as { badge: (v: string) => string }).badge;

    expect(badge('pass')).toContain('success');
    expect(badge('fail')).toContain('danger');
    expect(badge('unclear')).toContain('warning');
    expect(badge('unrated')).toBe('badge');
  });

  it('copes with a use-case list that carries no page body', () => {
    /** The endpoint is paged; a body without `results` is what an older server would answer, and
     *  the picker must be empty rather than throwing. */
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [SmokeTests],
      providers: [
        { provide: MeService, useValue: { get: () => of({ roles: ['it-security'] }) } },
        {
          provide: UseCaseService,
          useValue: {
            batteries: () => of([BATTERY]),
            models: () => of([]),
            testRuns: () => of([]),
            testStats: () => of([]),
            listPage: () => of({ count: 0, page: 1, page_size: 25, pages: 1 }),
          },
        },
      ],
    });
    const fixture = TestBed.createComponent(SmokeTests);
    fixture.detectChanges();

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[data-testid="no-use-case"]'),
    ).not.toBeNull();
  });

  it('says an empty statistics table is empty rather than showing nothing', () => {
    expect(setup().testid('no-stats')).not.toBeNull();
  });
});
