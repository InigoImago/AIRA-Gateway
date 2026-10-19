import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Observable, Subject, of, throwError } from 'rxjs';
import { Trace, TraceDetail, TracePage, TracePayload, UseCase } from '../../core/api/models';
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
  template: `<app-traces-tab [slug]="slug()" [useCase]="useCase()" />`,
  // Only `PageFeedback`: the tab provides its own `Live`, exactly as it does in
  // production, and a harness that provided it here would be testing a different
  // component from the one that ships.
  providers: [PageFeedback],
})
class Host {
  readonly slug = signal('uc-a');
  readonly useCase = signal<UseCase | null>(null);
}

/** The fields of a use case that decide who may read its content, and nothing else. */
function useCaseWith(over: Partial<UseCase>): UseCase {
  return { slug: 'uc-a', store_payloads: true, payload_visibility: 'members', ...over } as UseCase;
}

interface Options {
  /** What `/me` lists. Absent means no incident authority. */
  permissions?: string[];
  roles?: string[];
  pages?: TracePage[];
  /** What `GET /traces/{id}/payload` answers. */
  payload?: Record<string, unknown>;
  payloadFails?: boolean;
  failMore?: boolean;
  /** Answers held back per request id, to answer in whatever order a test needs. */
  heldPayloads?: Record<string, Subject<TracePayload>>;
  /** First-page answers held back, one per call in order; later calls fall back to `pages`. */
  heldPages?: Subject<TracePage>[];
  /** What every first-page load fails with. */
  pagesFail?: unknown;
  /** `/me` fails instead of answering. */
  meFails?: boolean;
  /** Whether `/me` says the installation runs strict data mode. */
  strictData?: boolean;
  /** The use case the page hands the tab; absent is the Requests page, which has none. */
  useCase?: UseCase | null;
  /** What `GET /traces/{id}` answers in place of the listed row. */
  detail?: (id: string) => TraceDetail;
  detailFails?: unknown;
  /** Openings held back per request id. */
  heldDetails?: Record<string, Subject<TraceDetail>>;
  /** What `GET /use-cases/{slug}` answers — asked by the Requests page about an opened row. */
  readUseCase?: (slug: string) => Observable<UseCase>;
  /** The page's slug; empty is the Requests page, which spans every use case. */
  slug?: string;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const queries: Record<string, unknown>[] = [];
  const pages = options.pages ?? [{ traces: [trace()], next_cursor: null, scope: 'use_cases' }];
  // A copy: answered in order, without emptying the list the test still refers to.
  const heldPages = [...(options.heldPages ?? [])];
  let call = 0;
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [
      // The detail links to the use case, so the component genuinely uses `RouterLink`. A
      // harness without a router is testing a different component.
      provideRouter([]),
      {
        provide: MeService,
        useValue: {
          currency: signal(''),
          get: () =>
            options.meFails
              ? throwError(() => ({ status: 503 }))
              : of({
                  roles: options.roles ?? [],
                  permissions: options.permissions ?? [],
                  strict_data: options.strictData,
                }),
        },
      },
      {
        provide: UseCaseService,
        useValue: {
          get: (slug: string): Observable<UseCase> => {
            queries.push({ useCaseFor: slug });
            return options.readUseCase
              ? options.readUseCase(slug)
              : throwError(() => ({ status: 404 }));
          },
          traceDetail: (id: string): Observable<TraceDetail> => {
            queries.push({ detailFor: id });
            if (options.heldDetails?.[id]) return options.heldDetails[id];
            if (options.detailFails) return throwError(() => options.detailFails);
            if (options.detail) return of(options.detail(id));
            const listed = pages.flatMap((page) => page.traces).find((row) => row.id === id);
            return of({ trace: listed ?? trace({ id }), recorded: true });
          },
          tracePayload: (id: string) => {
            queries.push({ payloadFor: id });
            if (options.heldPayloads?.[id]) return options.heldPayloads[id];
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
          traces: (query: Record<string, unknown>): Observable<TracePage> => {
            queries.push(query);
            if (options.failMore && query['cursor']) return throwError(() => ({ status: 500 }));
            if (options.pagesFail && !query['cursor']) return throwError(() => options.pagesFail);
            const held = heldPages.shift();
            if (held) return held;
            const page = pages[Math.min(call, pages.length - 1)];
            call += 1;
            return of(page);
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(Host);
  if (options.useCase) fixture.componentInstance.useCase.set(options.useCase);
  if (options.slug !== undefined) fixture.componentInstance.slug.set(options.slug);
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
      element.querySelector<HTMLElement>('[data-testid^="open-detail-"]')?.click();
      fixture.detectChanges();
    },
    /** Open the first row, then press *Show the prompt and response* under its details. */
    readFirst: () => {
      element.querySelector<HTMLElement>('[data-testid^="open-detail-"]')?.click();
      fixture.detectChanges();
      element.querySelector<HTMLElement>('[data-testid^="read-payload-"]')?.click();
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

  it('shows a half-known token count rather than dropping it, and the unknown half as unknown', () => {
    // "Unknown is not zero": printing the missing half as 0 says the model answered with nothing.
    const { text } = one({ prompt_tokens: 12_345, completion_tokens: null });
    expect(text()).toContain('12,345 in / — out');
    expect(text()).not.toContain('0 out');
  });

  it('prints a cheap request as what it cost, never as zero, through the shared money rule', () => {
    // 12 345 nanos at four decimals is 0.0000 — a priced request shown as free.
    expect(one({ cost_nanos: 12_345 }).text()).toContain('0.000012');
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
        provideRouter([]),
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
     *  this filter without `incident.investigate`, so the console must not put it on screen — and
     *  the question is the permission `/me` lists, not a role list retyped here. */
    const investigator = setup({ permissions: ['incident.investigate'] });
    expect(investigator.testid('trace-source-ip')).not.toBeNull();

    const administrator = setup({ permissions: [] });
    expect(administrator.testid('trace-source-ip')).toBeNull();
    // …while everything an administrator *may* ask stays available to them.
    expect(administrator.testid('trace-credential')).not.toBeNull();
  });

  it('withholds the incident field when the permissions could not be read', () => {
    /** The safe direction. A failed `/me` that left the field on screen would produce a control
     *  that 403s, and the reader would conclude the recording is broken. The answer the stub
     *  would give lists the permission, so only the failure can be what withholds it. */
    const { testid } = setup({ permissions: ['incident.investigate'], meFails: true });

    expect(testid('trace-source-ip')).toBeNull();
  });

  it('asks the permission, never the role', () => {
    // The server decides what a role holds: a senior role with nothing listed is offered nothing,
    // and the permission alone is enough.
    const roleOnly = setup({ roles: ['it-security', 'global-admin'], permissions: [] });
    expect(roleOnly.testid('trace-source-ip')).toBeNull();

    const permissionOnly = setup({ roles: [], permissions: ['incident.investigate'] });
    expect(permissionOnly.testid('trace-source-ip')).not.toBeNull();
  });

  it('does not take a neighbouring permission for this one', () => {
    // Every request's metadata and any request's content are other questions than an incident's.
    const { testid, element } = setup({ permissions: ['trace.read_all', 'payload.read_any'] });

    expect(testid('trace-source-ip')).toBeNull();
    const headers = [...element.querySelectorAll('th')].map((th) => th.textContent?.trim());
    expect(headers).not.toContain('From');
  });

  it('waits for a pause before asking, and asks with what was typed', async () => {
    /** Nine letters must not be nine round trips (`FRD-208`) — and the address has to arrive
     *  trimmed, or a trailing space asks about a machine that does not exist. */
    const { tab, queries } = setup({ permissions: ['incident.investigate'] });
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
    const { testid, readFirst } = setup({ permissions: ['payload.read_any'] });
    readFirst();

    expect(testid('payload-request')?.textContent).toContain('hello');
    expect(testid('payload-response')?.textContent).toContain('hi');
    expect(testid('payload-recorded')).not.toBeNull();
  });

  it('shows a line break inside the prompt as a line break, not as \\n', () => {
    // A system prompt is hundreds of lines; JSON writes each break as `\n`, and a pane that showed
    // those would show one unbroken paragraph.
    const { testid, readFirst } = setup({
      permissions: ['payload.read_any'],
      payload: {
        id: 't1',
        available: true,
        ground: 'incident',
        request: { systemInstruction: 'Be brief.\nAnswer in German.' },
        response: { text: 'ok' },
      },
    });
    readFirst();

    const prompt = testid('payload-request')?.textContent ?? '';
    expect(prompt).toContain('Be brief.\nAnswer in German.');
    expect(prompt).not.toContain('\\n');
  });

  it("closes it again, so one request's content is open at a time", () => {
    const { testid, readFirst, openFirst } = setup({ permissions: ['payload.read_any'] });
    readFirst();
    expect(testid('payload-request')).not.toBeNull();

    openFirst();

    expect(testid('payload-request')).toBeNull();
  });

  it('states which kind of nothing it found', () => {
    /** "Storage is off", "retention removed it" and "this never reached a model" are three
     *  different facts about the installation, and two of them are somebody's to change. A single
     *  "not available" teaches the reader to distrust the screen. */
    const { testid, readFirst } = setup({
      permissions: ['payload.read_any'],
      payload: {
        id: 't1',
        available: false,
        reason: 'not_stored',
        message: 'This use case does not store prompts and responses.',
      },
    });
    readFirst();

    expect(testid('payload-unavailable')?.textContent).toContain('does not store');
    expect(testid('payload-request')).toBeNull();
  });

  it('reports a refused read instead of leaving an empty panel open', () => {
    /** Asserted on the page's **single** `PageFeedback` rather than on this panel's own DOM: one
     *  banner per page is the rule, and the parent renders it. A first version of this test looked
     *  for the sentence inside the tab and failed for exactly that reason — which is the design
     *  working, not a gap. */
    // A member of a use case that leaves content to its members: offered the button, and refused.
    const { fixture, testid, readFirst } = setup({
      permissions: [],
      useCase: useCaseWith({ payload_visibility: 'members' }),
      payloadFails: true,
    });
    readFirst();

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
    const { element } = setup({ permissions: ['incident.investigate'] });

    const headers = [...element.querySelectorAll('th')].map((th) => th.textContent?.trim());
    expect(headers).toContain('From');
  });

  it('withholds that column from a reader who may not investigate', () => {
    const { element } = setup({ permissions: [] });

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
    // In words, short enough to scan; the full sentence is the badge's title (`FRD-628`).
    const badge = named.element.querySelector('.badge--danger');
    expect(badge?.textContent?.trim()).toBe('rate limit');
    expect(badge?.getAttribute('title')).toContain('A rate limit refused this request');

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
    const { element } = setup({ permissions: ['incident.investigate'] });

    const firstCell = element.querySelector('tbody tr td');
    expect(firstCell?.querySelector('[data-testid^="open-detail-"]')).not.toBeNull();
  });

  it("opens a request's details by clicking anywhere on its row — and reads no content", () => {
    /** A 2.5rem target at the far left of a wide table is a small target, so the whole row opens
     *  the details. It does not read the prompt: that read is recorded under the reader's name,
     *  and a click meant to select or copy a subject or a time must not make one (`FRD-622`). */
    const { fixture, element, testid, queries } = setup({ permissions: ['payload.read_any'] });
    element.querySelector<HTMLElement>('tbody tr td:nth-child(2)')?.click();
    fixture.detectChanges();

    expect(testid('payload-request')).toBeNull();
    expect(queries.some((query) => 'payloadFor' in query)).toBe(false);
    expect(element.querySelector('.row-detail')?.textContent).toContain('qwen3:0.6b');

    // The content is one deliberate, labelled press away — and that press is the read.
    testid('read-payload-t1')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();
    expect(queries.filter((query) => 'payloadFor' in query).length).toBe(1);
    expect(testid('payload-request')?.textContent).toContain('hello');
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

describe('TracesTab — one request, linked from the content-read log (`FRD-622` FR-6)', () => {
  async function focusedOn(id: string, options: Options = {}) {
    const harness = setup(options);
    await TestBed.inject(Router).navigateByUrl(`/?request=${id}`);
    harness.fixture.detectChanges();
    return harness;
  }

  it('asks for that one request, marks its row and does not open its content', async () => {
    const harness = await focusedOn('t1');

    expect(harness.queries.at(-1)?.['requestId']).toBe('t1');
    expect(harness.testid('focused-request')?.textContent).toContain('t1');
    expect(harness.element.querySelector('tbody tr.is-focused')).not.toBeNull();
    // Opening the content is a recorded read, so the link does not do it for the reader.
    expect(harness.queries.some((query) => 'payloadFor' in query)).toBe(false);
    expect(harness.testid('payload-request')).toBeNull();
  });

  it('goes back to every request, and the address with it', async () => {
    const harness = await focusedOn('t1');
    harness.click('show-all-requests');
    await harness.fixture.whenStable();
    harness.fixture.detectChanges();

    expect(harness.queries.at(-1)?.['requestId']).toBe('');
    expect(harness.testid('focused-request')).toBeNull();
    expect(TestBed.inject(Router).url).not.toContain('request=');
  });

  it('says a linked request that is gone is gone, rather than that nothing matches', async () => {
    const harness = await focusedOn('gone', {
      pages: [{ traces: [], next_cursor: null, scope: 'use_cases' }],
    });

    expect(harness.testid('no-traces')?.textContent).toContain('no longer in the audit trail');
  });

  it('does not restart the list when another parameter changes', async () => {
    const harness = await focusedOn('t1');
    const asked = harness.queries.length;
    await TestBed.inject(Router).navigateByUrl('/?request=t1&tab=traces');
    harness.fixture.detectChanges();

    expect(harness.queries.length).toBe(asked);
  });
});

/**
 * Two answers in flight at once — the races a slow gateway produces and an instant stub never does.
 */
describe('TracesTab — answers that arrive late', () => {
  const payloadOf = (id: string, prompt: string): TracePayload => ({
    id,
    available: true,
    request: { text: prompt },
    response: { text: 'answer' },
    ground: 'incident',
  });

  it("never shows one request's prompt under another request's row", () => {
    // Open A, then B before A has answered. A answering last used to land under B — one person's
    // prompt attributed to another's name, model and time, under "this read was recorded".
    const held = { a: new Subject<TracePayload>(), b: new Subject<TracePayload>() };
    const harness = setup({
      permissions: ['payload.read_any'],
      heldPayloads: held,
      pages: [
        {
          traces: [
            trace({ id: 'a', subject: 'subject-of-a' }),
            trace({ id: 'b', subject: 'subject-of-b' }),
          ],
          next_cursor: null,
          scope: 'all',
        },
      ],
    });

    harness.click('open-detail-a');
    harness.click('read-payload-a');
    harness.click('open-detail-b');
    harness.click('read-payload-b');
    held.b.next(payloadOf('b', 'PROMPT OF B'));
    held.a.next(payloadOf('a', 'PROMPT OF A'));
    harness.fixture.detectChanges();

    const detail = harness.element.querySelector('.row-detail');
    expect(detail?.previousElementSibling?.textContent).toContain('subject-of-b');
    expect(harness.testid('payload-request')?.textContent).toContain('PROMPT OF B');
    expect(harness.text()).not.toContain('PROMPT OF A');
  });

  it('never places an answer about another request under the open row', () => {
    // Whatever went wrong on the way — a cache, a proxy, a server mixing two up — content is shown
    // only under the request it says it belongs to.
    const held = { b: new Subject<TracePayload>() };
    const harness = setup({
      permissions: ['payload.read_any'],
      heldPayloads: held,
      pages: [
        { traces: [trace({ id: 'a' }), trace({ id: 'b' })], next_cursor: null, scope: 'all' },
      ],
    });

    harness.click('open-detail-b');
    harness.click('read-payload-b');
    held.b.next(payloadOf('a', 'PROMPT OF A'));
    harness.fixture.detectChanges();

    expect(harness.text()).not.toContain('PROMPT OF A');
  });

  it('drops the read still in flight when another row is opened', () => {
    const held = { a: new Subject<TracePayload>(), b: new Subject<TracePayload>() };
    const harness = setup({
      permissions: ['payload.read_any'],
      heldPayloads: held,
      pages: [
        { traces: [trace({ id: 'a' }), trace({ id: 'b' })], next_cursor: null, scope: 'all' },
      ],
    });

    harness.click('open-detail-a');
    harness.click('read-payload-a');
    expect(held.a.observed).toBe(true);
    harness.click('open-detail-b');

    expect(held.a.observed).toBe(false);
  });

  it('runs a filter at once while the first page is still on its way, and shows its rows', () => {
    // The filter's load was skipped while a poll was in flight: the old answer then landed under
    // the ticked box — for ten seconds with live on, for good with it off.
    const first = new Subject<TracePage>();
    const filtered = new Subject<TracePage>();
    const harness = setup({ heldPages: [first, filtered] });

    harness.click('tools-only');
    expect(harness.queries.at(-1)?.['toolsOnly']).toBe(true);

    filtered.next({
      traces: [trace({ id: 'tool-turn', subject: 'filtered' })],
      next_cursor: null,
      scope: 'all',
    });
    first.next({
      traces: [trace({ id: 'any', subject: 'unfiltered' })],
      next_cursor: null,
      scope: 'all',
    });
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('filtered');
    expect(harness.text()).not.toContain('unfiltered');
  });

  it('asks again for every pause in typing that changed the filter, not only the first', async () => {
    // Every keystroke was the same event, so a distinct-check over events let the first pause
    // through and no other: the box said 9c01 while the table listed 7f2a's requests.
    const { tab, queries } = setup();
    const type = (tab as unknown as { setCredential: (v: string) => void }).setCredential.bind(tab);

    type('7f2a');
    await new Promise((resolve) => setTimeout(resolve, 400));
    type('9c01');
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(queries.filter((query) => 'credential' in query).map((q) => q['credential'])).toEqual([
      '',
      '7f2a',
      '9c01',
    ]);
  });

  it('asks nothing new when a pause leaves the filter as it was', async () => {
    const { tab, queries } = setup();
    const type = (tab as unknown as { setCredential: (v: string) => void }).setCredential.bind(tab);

    type('7f2a');
    await new Promise((resolve) => setTimeout(resolve, 400));
    type('7f2a ');
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(queries.filter((query) => query['credential'] === '7f2a').length).toBe(1);
  });

  it('shows the first page when live is switched off while it is still on its way', () => {
    // Switching live off dropped the load in flight, the first one included: the tab then said
    // "Loading requests…" for good, and a reader who paused to read a row never got one.
    const first = new Subject<TracePage>();
    const harness = setup({ heldPages: [first] });

    harness.click('traces-live');
    first.next({
      traces: [trace({ id: 'first', subject: 'arrived-after-the-pause' })],
      next_cursor: null,
      scope: 'all',
    });
    harness.fixture.detectChanges();

    expect((harness.testid('traces-live') as HTMLInputElement).checked).toBe(false);
    expect(harness.text()).toContain('arrived-after-the-pause');
    expect(harness.element.querySelector('.loading')).toBeNull();
  });

  it('reports a failed load in the page banner instead of loading for ever', () => {
    // No load reported anything: a gateway that was down left "Loading requests…" on screen
    // indefinitely, with the server's explanation nowhere.
    const harness = setup({
      pagesFail: { status: 503, error: { error: { message: 'The gateway is restarting.' } } },
    });
    const feedback = harness.fixture.debugElement.injector.get(PageFeedback);

    expect(feedback.error()).toBe('The gateway is restarting.');
    expect(harness.element.querySelector('.loading')).toBeNull();
  });

  it('takes its failure back once a later load answers', () => {
    const held = [new Subject<TracePage>(), new Subject<TracePage>()];
    const harness = setup({ heldPages: held });
    const feedback = harness.fixture.debugElement.injector.get(PageFeedback);

    held[0].error({ status: 502 });
    expect(feedback.error()).toContain('Could not load the requests.');

    harness.click('refusals-only');
    held[1].next({ traces: [trace()], next_cursor: null, scope: 'all' });
    expect(feedback.error()).toBeNull();
  });

  it('drops an older page still on its way when the filter changes', () => {
    // Its rows were drawn from the previous filter and would be appended under the new one.
    const older = new Subject<TracePage>();
    const harness = setup({
      pages: [{ traces: [trace({ id: 't1' })], next_cursor: 'c1', scope: 'all' }],
    });
    const service = TestBed.inject(UseCaseService) as unknown as {
      traces: (query: Record<string, unknown>) => Observable<TracePage>;
    };
    const original = service.traces;
    service.traces = (query) => (query['cursor'] ? older : original(query));

    harness.click('load-more');
    harness.click('mine-only');
    older.next({
      traces: [trace({ id: 'stale', subject: 'from-the-old-filter' })],
      next_cursor: null,
      scope: 'all',
    });
    harness.fixture.detectChanges();

    expect(harness.text()).not.toContain('from-the-old-filter');
  });
});

describe('TracesTab — how stale the list is', () => {
  afterEach(() => vi.useRealTimers());

  it('keeps counting while live is off, instead of saying "0s ago" for good', () => {
    // The stamp read a clock no signal carried, so in this zoneless application it froze at the
    // moment of the last refresh.
    vi.useFakeTimers();
    const harness = setup();
    harness.click('traces-live');

    vi.advanceTimersByTime(3 * 60 * 1000);
    harness.fixture.detectChanges();

    expect(harness.testid('traces-stamp')?.textContent).toContain('3m ago');
  });
});

describe('TracesTab — strict data mode (`FRD-627` FR-7)', () => {
  /** Somebody else's request, as the gateway lists it to a reader who may not see people. */
  const withheld = trace({ id: 'theirs', subject: null, credential: null, person_withheld: true });
  const own = trace({ id: 'mine', subject: 'erika', credential: 'aira_ab12' });
  const page = { traces: [withheld, own], next_cursor: null, scope: 'use_cases' as const };

  it('lists a withheld row as "another member", with no key and no via-key badge', () => {
    const { element, testid } = setup({ strictData: true, pages: [page] });
    const theirs = element.querySelector('[data-testid="open-detail-theirs"]')!.closest('tr')!;
    const mine = element.querySelector('[data-testid="open-detail-mine"]')!.closest('tr')!;

    expect(theirs.textContent).toContain('another member');
    expect(theirs.querySelector('[data-testid="trace-via-key"]')).toBeNull();
    expect(
      theirs.querySelector('[data-testid="trace-person-withheld"]')?.getAttribute('title'),
    ).toContain('Global Administrators and IT Security');
    expect(theirs.textContent).not.toContain('null');
    // The reader's own row keeps its name and its badge.
    expect(mine.textContent).toContain('erika');
    expect(mine.querySelector('[data-testid="trace-via-key"]')).not.toBeNull();
    expect(testid('trace-person-withheld')).not.toBeNull();
  });

  it('shows no key or badge on a withheld row even if one arrived with it', () => {
    // The row's flag decides, not whether a value happens to be there: an older gateway that
    // marked the row and still sent the prefix must not have it rendered.
    const leaky = trace({
      id: 'leaky',
      subject: null,
      credential: 'aira_ab12',
      person_withheld: true,
    });
    const { testid, openFirst, text } = setup({
      strictData: true,
      pages: [{ traces: [leaky], next_cursor: null, scope: 'use_cases' }],
    });

    expect(testid('trace-via-key')).toBeNull();
    openFirst();
    expect(testid('trace-key-withheld')).not.toBeNull();
    expect(text()).not.toContain('aira_ab12');
  });

  it('says the key is withheld in the open row, rather than a dash that reads as "no key"', () => {
    const { openFirst, testid, text } = setup({ strictData: true, pages: [page] });
    openFirst();

    expect(testid('trace-key-withheld')?.textContent).toContain('withheld');
    expect(text()).not.toContain('aira_ab12null');
  });

  it('hides the key filter from a reader who may not see people, and keeps "Only my requests"', () => {
    const { testid, queries, tab } = setup({ strictData: true, permissions: [], pages: [page] });

    expect(testid('trace-credential')).toBeNull();
    expect(testid('mine-only')).not.toBeNull();
    // A prefix left from before `/me` answered is not sent either: the server would refuse it.
    const typed = tab as unknown as {
      credential: { set: (v: string) => void };
      startLive: () => void;
    };
    typed.credential.set('aira_ab');
    typed.startLive();
    expect(queries.at(-1)?.['credential']).toBe('');
  });

  it('keeps the key filter for a reader who may see people, or when the mode is off', () => {
    const investigator = setup({ strictData: true, permissions: ['incident.investigate'] });
    expect(investigator.testid('trace-credential')).not.toBeNull();

    const open = setup({ strictData: false, permissions: [] });
    expect(open.testid('trace-credential')).not.toBeNull();
  });

  it('names a row whose person is not withheld, as before', () => {
    const { text, testid } = setup({
      strictData: true,
      permissions: ['incident.investigate'],
      pages: [{ traces: [trace({ subject: 'bob' })], next_cursor: null, scope: 'use_cases' }],
    });

    expect(text()).toContain('bob');
    expect(testid('trace-person-withheld')).toBeNull();
    expect(testid('trace-via-key')).not.toBeNull();
  });
});

describe('TracesTab — opening a request is recorded', () => {
  it('asks the server for the details and renders them from its answer, not from the list', () => {
    // The screen shows what the server handed over when it recorded the opening. A list row and
    // an opened detail that could differ would be two sources for one request.
    const { openFirst, queries, testid } = setup({
      detail: (id) => ({
        trace: trace({ id, model: 'model-from-the-opening', requested_model: null }),
        recorded: true,
      }),
    });
    openFirst();

    expect(queries.filter((query) => 'detailFor' in query)).toEqual([{ detailFor: 't1' }]);
    expect(testid('trace-detail')?.textContent).toContain('model-from-the-opening');
    expect(testid('trace-detail')?.textContent).not.toContain('qwen3:0.6b');
    expect(testid('detail-recorded')?.textContent).toContain(
      'Opening this was recorded, with your name and the time.',
    );
  });

  it('shows nothing of the details until the opening is on record', () => {
    const held = { t1: new Subject<TraceDetail>() };
    const { openFirst, testid, fixture } = setup({ heldDetails: held });
    openFirst();

    expect(testid('detail-loading')).not.toBeNull();
    expect(testid('trace-detail')).toBeNull();
    expect(testid('detail-recorded')).toBeNull();

    held.t1.next({ trace: trace(), recorded: true });
    fixture.detectChanges();
    expect(testid('detail-loading')).toBeNull();
    expect(testid('trace-detail')).not.toBeNull();
  });

  it('reports a refused opening through the page banner, and says so in the row', () => {
    const { openFirst, testid, fixture } = setup({
      detailFails: { status: 404, error: { error: { message: 'No such request.' } } },
    });
    openFirst();

    const feedback = fixture.debugElement.injector.get(PageFeedback);
    expect(feedback.error()).toBeTruthy();
    expect(testid('detail-failed')).not.toBeNull();
    expect(testid('trace-detail')).toBeNull();
    expect(testid('detail-recorded')).toBeNull();
  });

  it('never places the details of another request under the open row', () => {
    const held = { b: new Subject<TraceDetail>() };
    const harness = setup({
      heldDetails: held,
      pages: [
        { traces: [trace({ id: 'a' }), trace({ id: 'b' })], next_cursor: null, scope: 'all' },
      ],
    });
    harness.click('open-detail-b');
    held.b.next({ trace: trace({ id: 'a', model: 'MODEL OF A' }), recorded: true });
    harness.fixture.detectChanges();

    expect(harness.testid('trace-detail')).toBeNull();
    expect(harness.text()).not.toContain('MODEL OF A');
  });

  it('labels the row toggle as what it does — it opens the details, and reads no content', () => {
    const { testid, element, queries, fixture } = setup({ permissions: ['payload.read_any'] });
    const toggle = element.querySelector<HTMLElement>('[data-testid="open-detail-t1"]')!;
    expect(toggle.getAttribute('aria-label')).toBe('Show the details');

    toggle.click();
    fixture.detectChanges();

    expect(toggle.getAttribute('aria-label')).toBe('Hide the details');
    expect(queries.some((query) => 'payloadFor' in query)).toBe(false);
    expect(testid('payload-request')).toBeNull();
    expect(testid('read-payload-t1')).not.toBeNull();
  });
});

describe('TracesTab — the content button, only for a reader who can read', () => {
  /** Whether the opened row offers *Show the prompt and response*, and the note that goes with it. */
  function offered(options: Options): { button: boolean; note: boolean } {
    const harness = setup(options);
    harness.openFirst();
    const detail = harness.element.querySelector('.row-detail');
    return {
      button: harness.testid('read-payload-t1') !== null,
      note: (detail?.textContent ?? '').includes('Opening them is recorded'),
    };
  }

  it('offers it to a reader who may read any content, on either page and in either mode', () => {
    for (const strictData of [false, true]) {
      expect(offered({ permissions: ['payload.read_any'], strictData })).toEqual({
        button: true,
        note: true,
      });
      expect(
        offered({
          permissions: ['payload.read_any'],
          strictData,
          useCase: useCaseWith({ payload_visibility: 'oversight' }),
        }),
      ).toEqual({ button: true, note: true });
    }
  });

  it("offers it to a use case's people where the use case leaves its content to them", () => {
    for (const payload_visibility of ['members', 'own'] as const) {
      expect(offered({ permissions: [], useCase: useCaseWith({ payload_visibility }) })).toEqual({
        button: true,
        note: true,
      });
    }
  });

  it('withholds it — and its note — where the server could only refuse', () => {
    const nothing = { button: false, note: false };
    // Oversight only: nobody inside the use case reads, its administrators included.
    expect(
      offered({ permissions: [], useCase: useCaseWith({ payload_visibility: 'oversight' }) }),
    ).toEqual(nothing);
    // Nothing stored: there is no content to show.
    expect(
      offered({
        permissions: [],
        useCase: useCaseWith({ store_payloads: false, payload_visibility: 'members' }),
      }),
    ).toEqual(nothing);
    // Strict data mode: every use case is oversight, whatever it stored.
    expect(
      offered({
        permissions: [],
        strictData: true,
        useCase: useCaseWith({ payload_visibility: 'members' }),
      }),
    ).toEqual(nothing);
    // The Requests page has no use case to judge by: only the content permission is offered it.
    expect(offered({ permissions: ['incident.investigate', 'trace.read_all'] })).toEqual(nothing);
  });

  it('still shows the details, and that opening them was recorded, to a reader without it', () => {
    const { openFirst, testid } = setup({ permissions: [], strictData: true });
    openFirst();

    expect(testid('trace-detail')).not.toBeNull();
    expect(testid('detail-recorded')).not.toBeNull();
    expect(testid('read-payload-t1')).toBeNull();
  });
});

describe('TracesTab — the key filter before the mode is known', () => {
  it('holds the key filter back when /me has not said whether people are withheld', () => {
    // Shown before `/me` answered, a strict-mode reader could type a prefix the server refuses.
    const { testid } = setup({ meFails: true });
    expect(testid('trace-credential')).toBeNull();
  });
});

/** A request a pipeline step decided about, and whose answer came from a fallback. */
function decided(over: Partial<Trace> = {}): Trace {
  return trace({
    id: 'r1',
    outcome: 'blocked_by_pipeline',
    status: 400,
    error: 'Request blocked by the prompt-injection filter.',
    pipeline_decisions: [
      { step: 'injection_filter', flagged: true, action: 'blocked', why: 'injection' },
    ],
    ...over,
  });
}

function onePage(...rows: Trace[]): TracePage[] {
  return [{ traces: rows, next_cursor: null, scope: 'use_cases' }];
}

describe('TracesTab — what happened to a request (`FRD-628` FR-5)', () => {
  it('says in the opened row what happened, with the status and the message the caller got', () => {
    const harness = setup({ pages: onePage(decided()) });
    harness.openFirst();

    const story = harness.testid('what-happened');
    expect(story?.textContent).toContain('A pipeline step refused this request.');
    expect(harness.testid('outcome-status')?.textContent).toContain('400');
    expect(harness.testid('caller-message')?.textContent).toBe(
      'Request blocked by the prompt-injection filter.',
    );
    expect(harness.testid('story-line')?.textContent?.trim()).toBe(
      'Prompt-injection filter: refused (verdict: injection)',
    );
  });

  it('says nothing of what happened until the opening is on record', () => {
    const held = new Subject<TraceDetail>();
    const harness = setup({ pages: onePage(decided()), heldDetails: { r1: held } });
    harness.openFirst();
    expect(harness.testid('what-happened')).toBeNull();

    held.next({ trace: decided(), recorded: true });
    harness.fixture.detectChanges();
    expect(harness.testid('what-happened')).not.toBeNull();
  });

  it('names the provider, the region and the thinking share in the details', () => {
    const harness = setup({
      pages: onePage(trace({ provider: 'vertex', region: 'europe-west4', reasoning_tokens: 1234 })),
    });
    harness.openFirst();

    expect(harness.testid('trace-provider')?.textContent).toContain('vertex');
    expect(harness.testid('trace-provider')?.textContent).toContain('europe-west4');
    expect(harness.testid('reasoning-tokens')?.textContent).toContain('1,234 thinking');
  });

  it('shows no thinking line where nothing was thought', () => {
    const harness = setup({ pages: onePage(trace({ reasoning_tokens: null })) });
    harness.openFirst();
    expect(harness.testid('reasoning-tokens')).toBeNull();
  });

  it('names the outcome in words in the list, with the full sentence as the title', () => {
    const harness = setup({ pages: onePage(decided()) });
    const badge = harness.testid('outcome-badge');

    expect(badge?.textContent?.trim()).toBe('refused by pipeline');
    expect(badge?.getAttribute('title')).toBe('A pipeline step refused this request.');
  });

  it('offers the outcome filter in the same words, and sends the outcome itself', () => {
    const harness = setup();
    const options = [...harness.element.querySelectorAll('#trace-outcome option')];
    const budget = options.find((option) => option.textContent?.trim() === 'budget');

    expect(budget?.getAttribute('value')).toBe('budget_exceeded');
  });
});

describe('TracesTab — finding a request by its id (`FRD-628` FR-1)', () => {
  async function find(id: string, options: Options = {}) {
    const harness = setup(options);
    const input = harness.testid('trace-request-id') as HTMLInputElement;
    input.value = id;
    input.dispatchEvent(new Event('input'));
    harness.click('find-request');
    await harness.fixture.whenStable();
    harness.fixture.detectChanges();
    return harness;
  }

  it('says where the id comes from', () => {
    const harness = setup();
    harness.click('info-hint-request-id');
    expect(harness.testid('help-hint-request-id')?.textContent).toContain('X-AIRA-Request-Id');
  });

  it('asks the server for that request, through the address, and opens it', async () => {
    const harness = await find('  r1 ', { pages: onePage(decided()) });

    expect(harness.queries.some((query) => query['requestId'] === 'r1')).toBe(true);
    expect(TestBed.inject(Router).url).toContain('request=r1');
    expect(harness.queries.some((query) => query['detailFor'] === 'r1')).toBe(true);
    expect(harness.testid('what-happened')).not.toBeNull();
    // The details, never the content: that is still a deliberate click of its own.
    expect(harness.queries.some((query) => 'payloadFor' in query)).toBe(false);
  });

  it('opens the row when the id asked for is already the one shown', async () => {
    const harness = setup({ pages: onePage(decided()) });
    await TestBed.inject(Router).navigateByUrl('/?request=r1');
    harness.fixture.detectChanges();
    expect(harness.testid('what-happened')).toBeNull();

    harness.click('find-request');
    expect(harness.testid('what-happened')).not.toBeNull();
  });

  it('asks nothing for an empty field', async () => {
    const harness = await find('   ');
    expect(harness.queries.some((query) => query['requestId'])).toBe(false);
    expect(TestBed.inject(Router).url).not.toContain('request=');
  });

  it('says a request that is not found is not found', async () => {
    const harness = await find('nope', { pages: onePage() });
    expect(harness.testid('no-traces')?.textContent).toContain('no longer in the audit trail');
    expect(harness.queries.some((query) => 'detailFor' in query)).toBe(false);
  });

  it('shows the linked id in the field, and opens nothing for a link', async () => {
    const harness = setup({ pages: onePage(decided()) });
    await TestBed.inject(Router).navigateByUrl('/?request=r1');
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    harness.fixture.detectChanges();

    expect((harness.testid('trace-request-id') as HTMLInputElement).value).toBe('r1');
    // Following the content-read log's link is auditing that log, not adding to it.
    expect(harness.queries.some((query) => 'detailFor' in query)).toBe(false);
  });
});

describe('TracesTab — from a request to the pipeline test (`FRD-628` FR-7)', () => {
  const callable = useCaseWith({
    permissions: { can_admin: false, can_manage: false, is_member: true, may_call: true },
  });

  it('links a request a pipeline step decided about to the use case pipeline page', () => {
    const harness = setup({ pages: onePage(decided()), useCase: callable });
    harness.openFirst();

    const link = harness.testid('try-pipeline');
    expect(link?.textContent).toContain('Try the pipeline with your own text');
    expect(link?.getAttribute('href')).toBe('/use-cases/uc-a/pipeline');
  });

  it('offers no link where the gateway would refuse this reader the test', () => {
    const harness = setup({
      pages: onePage(decided()),
      useCase: useCaseWith({
        permissions: { can_admin: false, can_manage: false, is_member: false, may_call: false },
      }),
    });
    harness.openFirst();
    expect(harness.testid('try-pipeline')).toBeNull();
  });

  it('offers no link for a request only the model chain or a notice decided about', () => {
    const harness = setup({
      pages: onePage(
        trace({
          pipeline_decisions: [
            { step: 'dispatch', action: 'failed', to: 'm1', why: 'HTTP 503' },
            { step: 'notice', action: 'shown' },
          ],
        }),
      ),
      useCase: callable,
    });
    harness.openFirst();
    expect(harness.testid('what-happened')).not.toBeNull();
    expect(harness.testid('try-pipeline')).toBeNull();
  });

  it('asks about the use case on the Requests page, and links only where it may be tried', () => {
    const asked = setup({
      slug: '',
      pages: onePage(decided()),
      readUseCase: () => of(callable),
    });
    asked.openFirst();
    expect(asked.queries.some((query) => query['useCaseFor'] === 'uc-a')).toBe(true);
    expect(asked.testid('try-pipeline')?.getAttribute('href')).toBe('/use-cases/uc-a/pipeline');

    const refused = setup({
      slug: '',
      pages: onePage(decided()),
      readUseCase: () =>
        of(
          useCaseWith({
            permissions: { can_admin: false, can_manage: false, is_member: false, may_call: false },
          }),
        ),
    });
    refused.openFirst();
    expect(refused.testid('try-pipeline')).toBeNull();
  });

  it('offers no link for a use case this reader cannot open', () => {
    const harness = setup({ slug: '', pages: onePage(decided()) });
    harness.openFirst();
    expect(harness.testid('what-happened')).not.toBeNull();
    expect(harness.testid('try-pipeline')).toBeNull();
  });

  it('never places a late answer about the use case under another row', () => {
    const late = new Subject<UseCase>();
    const harness = setup({
      slug: '',
      pages: onePage(decided(), decided({ id: 'r2', pipeline_decisions: null })),
      readUseCase: () => late,
    });
    harness.openFirst();
    // The second row is opened before the first one's answer arrives.
    harness.element.querySelectorAll<HTMLElement>('[data-testid^="open-detail-"]')[1].click();
    harness.fixture.detectChanges();
    late.next(callable);
    harness.fixture.detectChanges();

    expect(harness.testid('try-pipeline')).toBeNull();
  });
});
