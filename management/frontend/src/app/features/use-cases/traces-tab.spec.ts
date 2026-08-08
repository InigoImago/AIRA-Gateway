import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { Trace, TracePage } from '../../core/api/models';
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
  pages?: TracePage[];
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
      {
        provide: UseCaseService,
        useValue: {
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
    click: (id: string) => {
      element.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
      fixture.detectChanges();
    },
  };
}

describe('TracesTab', () => {
  it('shows what happened, and asks only about this use case', () => {
    const { text, queries } = setup();

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
    const { text } = setup({
      pages: [{ traces: [trace({ cost_nanos: 2_500_000 })], next_cursor: null, scope: 'all' }],
    });

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
    return setup({ pages: [{ traces: [trace(over)], next_cursor: null, scope: 'all' }] });
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

    expect(text()).not.toContain('0 / 0');
    expect(text()).toContain('—');
  });

  it('shows a half-known token count rather than dropping it', () => {
    expect(one({ prompt_tokens: 12, completion_tokens: null }).text()).toContain('12 / 0');
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

    expect(harness.text()).toContain('asked for b');
    expect(harness.text()).not.toContain('·');
  });
  // ---- what an incident needs to see (`FRD-131` FR-7, `FRD-502`) ---------------------------

  it('shows the functions the model asked for', () => {
    const { text } = setup({
      pages: [
        {
          traces: [trace({ tool_calls: { declared: 3, called: ['read_file', 'bash'] } })],
          next_cursor: null,
          scope: 'all',
        },
      ],
    });

    expect(text()).toContain('read_file');
  });

  it('distinguishes "offered and not used" from "never offered"', () => {
    /** Two different events, and only one of them is a model behaving oddly. A row showing a dash
     *  for both would hide the interesting one. */
    const { text } = setup({
      pages: [
        {
          traces: [trace({ tool_calls: { declared: 4, called: [] } })],
          next_cursor: null,
          scope: 'all',
        },
      ],
    });

    expect(text()).toContain('4 offered, none called');
  });

  it('asks the server for tool turns rather than filtering what it already has', () => {
    /** Asserted by watching the **request**: a client-side filter would pass a "the right rows are
     *  on screen" test too, while showing the reader one page of whatever happened to be loaded. */
    const { click, queries } = setup();
    click('tools-only');

    expect(queries.some((query) => query['toolsOnly'] === true)).toBe(true);
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
