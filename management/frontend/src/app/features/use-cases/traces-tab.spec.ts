import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { Trace, TracePage } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { TracesTab } from './traces-tab';

function trace(over: Partial<Trace> = {}): Trace {
  return {
    id: 't1',
    created_at: '2026-08-08T10:00:00Z',
    operation: 'generateContent',
    api: 'gemini',
    model: 'qwen3:0.6b',
    requested_model: 'qwen3:0.6b',
    model_selection: null,
    status: 200,
    outcome: 'served',
    prompt_tokens: 10,
    completion_tokens: 20,
    total_tokens: 30,
    latency_ms: 412,
    cost_nanos: 2_500_000,
    provider: 'ollama',
    region: null,
    trace_id: 'abc123',
    subject: 'ada',
    credential: 'aira_ab12',
    use_case: 'uc-a',
    tool_calls: null,
    ...over,
  };
}

@Component({
  selector: 'app-traces-host',
  imports: [TracesTab],
  template: `<app-traces-tab [slug]="slug()" />`,
  // Only `PageFeedback`: the tab provides its own `Live`, exactly as it does in
  // production, and a harness that provided it here would be testing a different
  // component from the one that ships.
  providers: [PageFeedback],
})
class Host {
  readonly slug = signal('uc-a');
}

interface Options {
  /** What `/me` answers. Absent means a role with no incident authority. */
  roles?: string[];
  pages?: TracePage[];
  /** What `GET /traces/{id}/payload` answers. */
  payload?: Record<string, unknown>;
  payloadFails?: boolean;
  failMore?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const queries: Record<string, unknown>[] = [];
  const pages = options.pages ?? [{ traces: [trace()], next_cursor: null, scope: 'use_cases' }];
  let call = 0;
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [
      // The detail links to the use case, so the component genuinely uses `RouterLink`. A
      // harness without a router is testing a different component.
      provideRouter([]),
      {
        provide: MeService,
        useValue: { get: () => of({ roles: options.roles ?? [] }) },
      },
      {
        provide: UseCaseService,
        useValue: {
          tracePayload: (id: string) => {
            queries.push({ payloadFor: id });
            if (options.payloadFails) return throwError(() => ({ status: 403 }));
            return of(
              options.payload ?? {
                id,
                available: true,
                request: { text: 'hello' },
                response: { text: 'hi' },
                ground: 'incident',
              },
            );
          },
          traces: (query: Record<string, unknown>) => {
            queries.push(query);
            if (options.failMore && query['cursor']) return throwError(() => ({ status: 500 }));
            const page = pages[Math.min(call, pages.length - 1)];
            call += 1;
            return of(page);
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  const tab = fixture.debugElement.children[0].componentInstance as unknown as Record<
    string,
    never
  >;
  return {
    fixture,
    element,
    queries,
    tab,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    rows: () => element.querySelectorAll('tbody tr'),
    /** Open the first row. Most of what a trace records now lives in the detail rather than in a
     *  column — the table was eleven columns wide and scrolled sideways, so it carries what a
     *  reader *scans* by and the rest belongs to the one request they chose to look at. */
    openFirst: () => {
      element.querySelector<HTMLElement>('[data-testid^="open-payload-"]')?.click();
      fixture.detectChanges();
    },
    click: (id: string) => {
      element.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
      fixture.detectChanges();
    },
  };
}

describe('TracesTab', () => {
  it('shows what happened, and asks only about this use case', () => {
    const { text, queries, openFirst } = setup();
    openFirst();

    expect(queries[0]['useCase']).toBe('uc-a');
    expect(text()).toContain('generateContent');
    expect(text()).toContain('qwen3:0.6b');
    expect(text()).toContain('ada');
  });

  it('shows no prompt or response, and says so on the page', () => {
    // `FRD-502` FR-11. The server sends metadata only; the screen states the guarantee, because a
    // reader who assumes prompts are one click away asks for the click.
    const { text } = setup();

    expect(text()).toContain('Prompts and responses are not shown here');
  });

  it('renders money as money, keeping the exact integer in the API', () => {
    const { text, openFirst } = setup({
      pages: [{ traces: [trace({ cost_nanos: 2_500_000 })], next_cursor: null, scope: 'all' }],
    });
    openFirst();

    expect(text()).toContain('0.0025');
  });

  it('does not print an unpriced request as free', () => {
    // "Unpriced is not zero" (`FRD-403`), the same rule the reporting screen keeps.
    const { text } = setup({
      pages: [{ traces: [trace({ cost_nanos: null })], next_cursor: null, scope: 'all' }],
    });

    expect(text()).not.toContain('0.0000');
    expect(text()).toContain('—');
  });

  it('asks the server for refusals rather than filtering what it happens to have', () => {
    // Filtering client-side would silently mean "refusals within the last 50 requests", which
    // reads as "there were none" exactly when there were many.
    const { queries, click } = setup();
    click('refusals-only');

    expect(queries.at(-1)?.['refusalsOnly']).toBe(true);
  });

  it('never sends a cursor from a different filter', () => {
    // A cursor means nothing under another filter; paging on with it mixes two result sets, and
    // the reader cannot tell.
    const first: TracePage = { traces: [trace()], next_cursor: 'c1', scope: 'all' };
    const harness = setup({ pages: [first] });
    (harness.tab as unknown as { toggleRefusals: (on: boolean) => void }).toggleRefusals(true);

    expect(harness.queries.at(-1)?.['cursor']).toBeUndefined();
  });

  it('keeps the two filters from contradicting each other', () => {
    const harness = setup();
    const tab = harness.tab as unknown as {
      toggleRefusals: (on: boolean) => void;
      setOutcome: (v: string) => void;
      refusalsOnly: () => boolean;
      outcome: () => string;
    };

    tab.toggleRefusals(true);
    tab.setOutcome('served');
    expect(tab.refusalsOnly()).toBe(false);

    tab.toggleRefusals(true);
    expect(tab.outcome()).toBe('');
  });

  it('appends the next page instead of replacing it', () => {
    const harness = setup({
      pages: [
        { traces: [trace({ id: 't1' })], next_cursor: 'c1', scope: 'all' },
        { traces: [trace({ id: 't2' })], next_cursor: null, scope: 'all' },
      ],
    });

    expect(harness.rows().length).toBe(1);
    harness.click('load-more');
    expect(harness.rows().length).toBe(2);
    expect(harness.queries.at(-1)?.['cursor']).toBe('c1');
  });

  it('switches live off while paging', () => {
    // A refresh would replace the first page and throw away everything scrolled to — the single
    // most annoying way a live table can behave.
    const harness = setup({
      pages: [
        { traces: [trace()], next_cursor: 'c1', scope: 'all' },
        { traces: [trace({ id: 't2' })], next_cursor: null, scope: 'all' },
      ],
    });
    harness.click('load-more');
    const toggle = harness.testid('traces-live') as HTMLInputElement;
    expect(toggle.checked).toBe(false);
  });

  it('offers no "load more" when there is no more', () => {
    expect(setup().testid('load-more')).toBeNull();
  });

  it('reports a failed page through the page banner, not a second one of its own', () => {
    // One banner per page. A panel that grows its own turns a page with six panels into a page
    // with six places to look for the same failure.
    const harness = setup({
      pages: [{ traces: [trace()], next_cursor: 'c1', scope: 'all' }],
      failMore: true,
    });

    harness.click('load-more');
    const pageFeedback = harness.fixture.debugElement.injector.get(PageFeedback);
    expect(pageFeedback.error()).toContain('Could not load more requests.');
    expect(harness.element.querySelectorAll('.callout--danger').length).toBe(0);
  });

  it('says "no requests match" rather than showing an empty table', () => {
    const harness = setup({ pages: [{ traces: [], next_cursor: null, scope: 'all' }] });
    expect(harness.testid('no-traces')).not.toBeNull();
  });

  it('does not print "nothing happened" when the truth is "you cannot see this"', () => {
    // Found live: the gateway reads membership from the identity provider's groups, so an
    // administrator of a use case created in this console sees an empty tab while traffic flows.
    // An empty state that states the wrong reason is worse than one that states none — the reader
    // concludes the recording is broken, and then distrusts every figure on the page.
    const harness = setup({
      pages: [{ traces: [], next_cursor: null, scope: 'use_cases', in_scope: false }],
    });

    expect(harness.testid('no-traces')).toBeNull();
    expect(harness.testid('not-in-scope')?.textContent).toContain('identity provider');
    // And it names the group, because "ask somebody to fix it" is only actionable with the name.
    expect(harness.text()).toContain('/use-cases/uc-a');
  });

  it('reads a gateway that says nothing about scope as "in scope"', () => {
    // A field a deployed gateway does not send yet must not turn every tab into a warning.
    const harness = setup({ pages: [{ traces: [], next_cursor: null, scope: 'use_cases' }] });
    expect(harness.testid('no-traces')).not.toBeNull();
  });
});

describe('TracesTab — reading one row', () => {
  function one(over: Partial<Trace>) {
    const harness = setup({ pages: [{ traces: [trace(over)], next_cursor: null, scope: 'all' }] });
    harness.openFirst();
    return harness;
  }

  it('names both the model asked for and the one that answered, when they differ', () => {
    // With a cross-vendor fallback chain they routinely differ, and "why did that model answer"
    // and "why did the spend triple" have no answer without the pair (`FRD-122`).
    const { text } = one({
      model: 'claude-sonnet-4',
      requested_model: 'gemini-2.0-flash',
      model_selection: 'fallback:1',
    });

    expect(text()).toContain('claude-sonnet-4');
    expect(text()).toContain('asked for gemini-2.0-flash');
    expect(text()).toContain('fallback:1');
  });

  it('does not repeat the model when nothing was routed', () => {
    expect(one({ model: 'm', requested_model: 'm' }).text()).not.toContain('asked for');
  });

  it('marks a refusal as a refusal, naming the outcome', () => {
    // The single most useful column on the page: `FRD-122` exists because refusals used to leave
    // no row at all, and a row that reads "429" without saying which control fired is half a fact.
    const { text } = one({ outcome: 'suspended', status: 429 });

    expect(text()).toContain('suspended');
  });

  it('falls back to the status when a row predates the outcome column', () => {
    expect(one({ outcome: null, status: 502 }).text()).toContain('502');
  });

  it('does not show a request of unknown size as zero tokens', () => {
    // "Unknown is not zero" — a refused request never reached a model, and printing 0/0 would
    // read as "it answered with nothing".
    const { text } = one({ prompt_tokens: null, completion_tokens: null, latency_ms: null });

    expect(text()).not.toContain('0 in / 0 out');
    expect(text()).toContain('—');
  });

  it('shows a half-known token count rather than dropping it', () => {
    expect(one({ prompt_tokens: 12, completion_tokens: null }).text()).toContain('12 in / 0 out');
  });

  it('shows the trace id, which is what correlates this row with the logs', () => {
    expect(one({ trace_id: 'deadbeef' }).text()).toContain('deadbeef');
    expect(one({ trace_id: null }).text()).toContain('—');
  });
});

describe('TracesTab — the live strip', () => {
  it('keeps the busy state in the space it already occupies', () => {
    // Measured with a layout-shift observer, not guessed: the stamp swapping "updating…" for
    // "updated 12s ago" moved the button beside it on every tick. That is the jiggle a reader
    // notices without being able to name it, and the smallest ones are the most unsettling
    // because nothing appears to have happened.
    const harness = setup();
    const stamp = harness.testid('traces-stamp');

    expect(stamp?.textContent).toContain('updated');
    expect(stamp?.querySelector('.live__dot')).not.toBeNull();
    // The word never changes — only the dot's class does.
    expect(stamp?.textContent).not.toContain('updating');
  });
});

describe('TracesTab — while a page is in flight', () => {
  it('says it is loading, and refuses a second press', () => {
    const pending = new Subject<TracePage>();
    TestBed.resetTestingModule();
    let first = true;
    TestBed.configureTestingModule({
      imports: [Host],
      providers: [
        {
          provide: UseCaseService,
          useValue: {
            traces: () => {
              if (first) {
                first = false;
                return of({ traces: [trace()], next_cursor: 'c1', scope: 'all' } as TracePage);
              }
              return pending;
            },
          },
        },
      ],
    });
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    element.querySelector<HTMLElement>('[data-testid="load-more"]')!.click();
    fixture.detectChanges();

    const button = element.querySelector<HTMLButtonElement>('[data-testid="load-more"]')!;
    expect(button.textContent).toContain('Loading…');
    expect(button.disabled).toBe(true);
    pending.complete();
  });

  it('does not print a routing note when there was no routing', () => {
    const harness = setup({
      pages: [
        {
          traces: [trace({ model: 'a', requested_model: 'b', model_selection: null })],
          next_cursor: null,
          scope: 'all',
        },
      ],
    });
    harness.openFirst();

    expect(harness.text()).toContain('asked for b');
    expect(harness.text()).not.toContain('·');
  });
  // ---- what an incident needs to see (`FRD-131` FR-7, `FRD-502`) ---------------------------

  it('shows the functions the model asked for', () => {
    const { text, openFirst } = setup({
      pages: [
        {
          traces: [trace({ tool_calls: { declared: 3, called: ['read_file', 'bash'] } })],
          next_cursor: null,
          scope: 'all',
        },
      ],
    });
    openFirst();

    expect(text()).toContain('read_file');
  });

  it('distinguishes "offered and not used" from "never offered"', () => {
    /** Two different events, and only one of them is a model behaving oddly. A row showing a dash
     *  for both would hide the interesting one. */
    const { text, openFirst } = setup({
      pages: [
        {
          traces: [trace({ tool_calls: { declared: 4, called: [] } })],
          next_cursor: null,
          scope: 'all',
        },
      ],
    });
    openFirst();

    expect(text()).toContain('4 offered, none called');
  });

  it('asks the server for tool turns rather than filtering what it already has', () => {
    /** Asserted by watching the **request**: a client-side filter would pass a "the right rows are
     *  on screen" test too, while showing the reader one page of whatever happened to be loaded. */
    const { click, queries } = setup();
    click('tools-only');

    expect(queries.some((query) => query['toolsOnly'] === true)).toBe(true);
  });

  it('offers the source address only where the server would answer it', () => {
    /** `FRD-206`: an action nobody can carry out is worse than an absent one. The server refuses
     *  this filter without an incident role, so the console must not put it on screen — and the
     *  predicate is the shared one, not a role list retyped here. */
    const investigator = setup({ roles: ['it-security'] });
    expect(investigator.testid('trace-source-ip')).not.toBeNull();

    const administrator = setup({ roles: [] });
    expect(administrator.testid('trace-source-ip')).toBeNull();
    // …while everything an administrator *may* ask stays available to them.
    expect(administrator.testid('trace-credential')).not.toBeNull();
  });

  it('withholds the incident field when the role could not be read', () => {
    /** The safe direction. A failed `/me` that left the field on screen would produce a control
     *  that 403s, and the reader would conclude the recording is broken. */
    const { testid } = setup({ roles: [] });

    expect(testid('trace-source-ip')).toBeNull();
  });

  it('waits for a pause before asking, and asks with what was typed', async () => {
    /** Nine letters must not be nine round trips (`FRD-208`) — and the address has to arrive
     *  trimmed, or a trailing space asks about a machine that does not exist. */
    const { tab, queries } = setup({ roles: ['it-security'] });
    const before = queries.length;

    (tab as unknown as { setSourceIp: (v: string) => void }).setSourceIp(' 10.0.0.7 ');
    (tab as unknown as { setSourceIp: (v: string) => void }).setSourceIp('10.0.0.7 ');
    expect(queries.length).toBe(before);

    await new Promise((resolve) => setTimeout(resolve, 400));

    const asked = queries.filter((query) => query['sourceIp']);
    expect(asked.length).toBe(1);
    expect(asked[0]['sourceIp']).toBe('10.0.0.7');
  });

  // ---- the prompt and the answer (`FRD-505`) -----------------------------------------------

  it("opens a request's content in place, and says the read was recorded", () => {
    /** The record is the condition on which `ADR-0009` was reopened, not a log line beside it —
     *  so the reader is told, rather than it happening quietly behind them. */
    const { fixture, testid, element } = setup({ roles: ['it-security'] });
    element.querySelector<HTMLElement>('[data-testid^="open-payload-"]')?.click();
    fixture.detectChanges();

    expect(testid('payload-request')?.textContent).toContain('hello');
    expect(testid('payload-response')?.textContent).toContain('hi');
    expect(testid('payload-recorded')).not.toBeNull();
  });

  it("closes it again, so one request's content is open at a time", () => {
    const { fixture, testid, element } = setup({ roles: ['it-security'] });
    const open = () => {
      element.querySelector<HTMLElement>('[data-testid^="open-payload-"]')?.click();
      fixture.detectChanges();
    };
    open();
    expect(testid('payload-request')).not.toBeNull();

    open();

    expect(testid('payload-request')).toBeNull();
  });

  it('states which kind of nothing it found', () => {
    /** "Storage is off", "retention removed it" and "this never reached a model" are three
     *  different facts about the installation, and two of them are somebody's to change. A single
     *  "not available" teaches the reader to distrust the screen. */
    const { fixture, testid } = setup({
      roles: ['it-security'],
      payload: {
        id: 't1',
        available: false,
        reason: 'not_stored',
        message: 'This use case does not store prompts and responses.',
      },
    });
    (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLElement>('[data-testid^="open-payload-"]')
      ?.click();
    fixture.detectChanges();

    expect(testid('payload-unavailable')?.textContent).toContain('does not store');
    expect(testid('payload-request')).toBeNull();
  });

  it('reports a refused read instead of leaving an empty panel open', () => {
    /** Asserted on the page's **single** `PageFeedback` rather than on this panel's own DOM: one
     *  banner per page is the rule, and the parent renders it. A first version of this test looked
     *  for the sentence inside the tab and failed for exactly that reason — which is the design
     *  working, not a gap. */
    const { fixture, testid } = setup({ roles: [], payloadFails: true });
    (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLElement>('[data-testid^="open-payload-"]')
      ?.click();
    fixture.detectChanges();

    // From the **host's** injector, not `TestBed.inject`: the harness provides `PageFeedback` on
    // the host component exactly as a page does, and an environment-level lookup would resolve a
    // different instance — the `Live` teardown lesson of `FRD-502`, one service over.
    const feedback = fixture.debugElement.injector.get(PageFeedback);
    // The **server's** sentence, not the fallback: a refusal here is a real answer about a real
    // permission, and it names who may. The fallback exists for a request that failed without
    // saying anything (`core/api/error-message.ts`), which is a different situation.
    expect(feedback.error()).toBeTruthy();
    expect(testid('payload-request')).toBeNull();
  });

  it('shows the calling machine as a column, not only as a filter', () => {
    /** The defect this fixes: the address could be searched for and never seen, so the filter
     *  could not be populated from the screen that offered it. */
    const { element } = setup({ roles: ['it-security'] });

    const headers = [...element.querySelectorAll('th')].map((th) => th.textContent?.trim());
    expect(headers).toContain('From');
  });

  it('withholds that column from a role that may not act on an incident', () => {
    const { element } = setup({ roles: [] });

    const headers = [...element.querySelectorAll('th')].map((th) => th.textContent?.trim());
    expect(headers).not.toContain('From');
  });

  // ---- what the reader is actually looking at ----------------------------------------------

  it('does not mark a served request as a failure', () => {
    /** Reported from the running console: a **200 shown in red**. `outcome` arrived with
     *  `FRD-122`, so every row written before it is NULL, and the badge fell through to the
     *  danger branch showing the status. A status column that calls a success a problem is the
     *  one thing it must never do. */
    const { element } = setup({
      pages: [{ traces: [trace({ outcome: null, status: 200 })], next_cursor: null, scope: 'all' }],
    });

    expect(element.querySelector('.badge--danger')).toBeNull();
    expect(element.querySelector('.badge--success')?.textContent?.trim()).toBe('served');
  });

  it('names a failure by its outcome, and falls back to the status only when there is none', () => {
    const named = setup({
      pages: [
        {
          traces: [trace({ outcome: 'rate_limited', status: 429 })],
          next_cursor: null,
          scope: 'all',
        },
      ],
    });
    expect(named.element.querySelector('.badge--danger')?.textContent?.trim()).toBe('rate_limited');

    const unnamed = setup({
      pages: [{ traces: [trace({ outcome: null, status: 500 })], next_cursor: null, scope: 'all' }],
    });
    // Warning, not danger: nobody recorded *why*, and inventing a name would be worse than the
    // number that was actually written down.
    expect(unnamed.element.querySelector('.badge--warning')?.textContent?.trim()).toBe('500');
  });

  it('puts the control that opens a request in the first column', () => {
    /** It was last. The table scrolls sideways once it carries a use case and an address, so the
     *  control was **off screen** and a reader had no way to learn it existed — reported as "the
     *  button was hidden behind the scroll, I did not even know it was there". */
    const { element } = setup({ roles: ['it-security'] });

    const firstCell = element.querySelector('tbody tr td');
    expect(firstCell?.querySelector('[data-testid^="open-payload-"]')).not.toBeNull();
  });

  it('opens a request by clicking anywhere on its row', () => {
    /** A 2.5rem target at the far left of a wide table is a small target. The row is the whole
     *  width of the thing the reader is looking at. */
    const { fixture, element, testid } = setup({ roles: ['it-security'] });
    element.querySelector<HTMLElement>('tbody tr')?.click();
    fixture.detectChanges();

    expect(testid('payload-request')).not.toBeNull();
  });

  it('marks a request a pipeline step objected to', () => {
    /** The interesting one is a **served** flagged request: it looks like every other 200 until
     *  something says otherwise. */
    const { element } = setup({
      pages: [{ traces: [trace({ flagged: true })], next_cursor: null, scope: 'all' }],
    });

    expect(element.querySelector('[data-testid="flagged"]')).not.toBeNull();
  });

  it('asks the server for only those requests', () => {
    const { click, queries } = setup();
    click('flagged-only');

    expect(queries.some((query) => query['flaggedOnly'] === true)).toBe(true);
  });

  it('carries the filters onto the next page', () => {
    /** The cursor came from *this* filter set. Paging with a different one appends rows the reader
     *  has just excluded — one list, two questions, and no error anywhere. */
    const { click, queries } = setup({
      pages: [
        // Three, because switching the filter reloads the first page: the third response is the
        // one the cursor is spent on.
        { traces: [trace()], next_cursor: 'c1', scope: 'all' },
        { traces: [trace()], next_cursor: 'c1', scope: 'all' },
        { traces: [trace({ id: 't2' })], next_cursor: null, scope: 'all' },
      ],
    });
    click('tools-only');
    click('load-more');

    const paged = queries.filter((query) => query['cursor']);
    expect(paged.length).toBeGreaterThan(0);
    expect(paged.every((query) => query['toolsOnly'] === true)).toBe(true);
  });

  it('asks the server for my own requests', () => {
    const { click, queries } = setup();
    click('mine-only');

    expect(queries.some((query) => query['mine'] === true)).toBe(true);
  });
});

/**
 * Whose name the caller line is (`FRD-604`).
 *
 * The question this answers is IT Security's on a bad day: a coding agent misbehaved, whose was
 * it? The audit row names the person the key was **issued to**, which is who answers for the
 * credential — and is not necessarily who wrote the request. Without a marker an investigator
 * reads a colleague's name beside an agent's traffic and concludes a human typed it, which is
 * both wrong and the sort of wrong that gets somebody accused.
 */
describe('TracesTab — whose name is on the row', () => {
  it('marks a request made with an API key as made with one', () => {
    const { text, testid } = setup({
      pages: [
        {
          traces: [trace({ subject: 'vscheibe', credential: 'aira_ab12' })],
          next_cursor: null,
          scope: 'use_cases',
        },
      ],
    });

    expect(text()).toContain('vscheibe');
    expect(testid('trace-via-key')).not.toBeNull();
  });

  /** An interactive caller has no marker, because there the name really is the person who asked.
   *  Marking both would make the distinction useless, which is the same as not making it. */
  it('leaves an interactive caller unmarked', () => {
    const { testid } = setup({
      pages: [
        {
          traces: [trace({ subject: 'vscheibe', credential: null })],
          next_cursor: null,
          scope: 'use_cases',
        },
      ],
    });

    expect(testid('trace-via-key')).toBeNull();
  });
});
