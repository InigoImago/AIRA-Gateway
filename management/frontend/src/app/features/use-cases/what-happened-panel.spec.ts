import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { PipelineDecision, Trace } from '../../core/api/models';
import { WhatHappenedPanel } from './what-happened-panel';

function trace(over: Partial<Trace> = {}): Trace {
  return {
    id: 'r1',
    created_at: '2026-10-16T10:00:00Z',
    operation: 'generateContent',
    api: 'gemini',
    model: 'gemini-2.5-flash',
    requested_model: 'gemini-2.5-flash',
    model_selection: 'direct',
    status: 200,
    outcome: 'served',
    prompt_tokens: 1,
    completion_tokens: 1,
    total_tokens: 2,
    latency_ms: 10,
    cost_nanos: null,
    provider: 'vertex',
    region: 'europe-west4',
    trace_id: null,
    subject: 'ada',
    credential: null,
    use_case: 'uc-a',
    tool_calls: null,
    ...over,
  };
}

@Component({
  selector: 'app-story-host',
  imports: [WhatHappenedPanel],
  template: `<app-what-happened [trace]="trace()" [tryLink]="link()" />`,
})
class Host {
  readonly trace = signal(trace());
  readonly link = signal<string[] | null>(null);
}

function render(over: Partial<Trace> = {}, link: string[] | null = null) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host], providers: [provideRouter([])] });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.trace.set(trace(over));
  fixture.componentInstance.link.set(link);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    element,
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    lines: () =>
      [...element.querySelectorAll('[data-testid="story-line"]')].map((line) =>
        line.textContent?.trim(),
      ),
    tone: (index: number) =>
      element.querySelectorAll('[data-testid="story-line"]')[index]?.className ?? '',
  };
}

/** The story of a request whose only decision is this one — refused, so no "Answered by" line. */
function lineFor(decision: PipelineDecision): string | undefined {
  return render({ outcome: 'blocked_by_pipeline', status: 400, pipeline_decisions: [decision] })
    .lines()
    .at(0);
}

describe('WhatHappenedPanel — the outcome', () => {
  const sentences: [string, number, string][] = [
    ['served', 200, 'The request was answered.'],
    ['rate_limited', 429, 'A rate limit refused this request'],
    ['budget_exceeded', 429, 'A budget refused this request'],
    ['suspended', 403, 'A suspension covers this caller'],
    ['blocked_by_pipeline', 400, 'A pipeline step refused this request.'],
    ['no_capable_model', 400, 'No released model can do what this request asks for'],
    ['model_not_found', 404, 'The model asked for is not known here'],
    ['invalid_request', 400, 'The request was malformed'],
    ['request_too_large', 413, 'larger than the gateway accepts'],
    ['upstream_error', 502, 'The model provider answered with an error'],
    ['internal_error', 500, 'The gateway failed'],
    ['client_gone', 499, 'The caller went away'],
    ['diagnostic', 200, 'A model check'],
  ];

  for (const [outcome, status, words] of sentences) {
    it(`says ${outcome} in words, with the status`, () => {
      const panel = render({ outcome, status, model: '' });
      expect(panel.testid('outcome-sentence')?.textContent).toContain(words);
      expect(panel.testid('outcome-status')?.textContent?.trim()).toBe(`HTTP ${status}`);
    });
  }

  it('still says an outcome it has no sentence for, rather than nothing', () => {
    const panel = render({ outcome: 'quota_moved', status: 429 });
    expect(panel.testid('outcome-sentence')?.textContent).toContain('quota moved');
  });

  it('reads a row from before outcomes by its status', () => {
    expect(render({ outcome: null, status: 204 }).testid('outcome-sentence')?.textContent).toBe(
      'The request was answered.',
    );
    expect(
      render({ outcome: null, status: 502 }).testid('outcome-sentence')?.textContent,
    ).toContain('status 502');
  });

  it('shows the message the caller was given, whole and as text', () => {
    const message = 'All candidates failed:\n<b>m1</b> 503; m2 timed out';
    const panel = render({ outcome: 'upstream_error', status: 502, error: message });
    const shown = panel.testid('caller-message');

    expect(shown?.textContent).toBe(message);
    // Text, never markup: a message carries whatever an upstream wrote into it.
    expect(shown?.querySelector('b')).toBeNull();
  });

  it('shows no message block for a request that succeeded', () => {
    expect(render({ error: null }).testid('caller-message')).toBeNull();
  });
});

describe('WhatHappenedPanel — the timeline', () => {
  it('says each verdict of the prompt-injection filter', () => {
    expect(lineFor({ step: 'injection_filter', flagged: false, action: 'flag' })).toBe(
      'Prompt-injection filter: passed',
    );
    expect(
      lineFor({ step: 'injection_filter', flagged: true, action: 'flag', why: 'injection' }),
    ).toBe('Prompt-injection filter: flagged and let through (verdict: injection)');
    expect(
      lineFor({
        step: 'injection_filter',
        flagged: true,
        action: 'blocked',
        why: 'injection (by the heuristic)',
      }),
    ).toBe('Prompt-injection filter: refused (verdict: injection (by the heuristic))');
  });

  it('marks a refusal as one and a flag as a warning, and leaves a pass plain', () => {
    const panel = render({
      outcome: 'blocked_by_pipeline',
      status: 400,
      pipeline_decisions: [
        { step: 'injection_filter', flagged: false },
        { step: 'injection_filter', flagged: true, action: 'flag' },
        { step: 'injection_filter', flagged: true, action: 'blocked' },
      ],
    });
    expect(panel.tone(0)).not.toMatch(/is-warning|is-danger/);
    expect(panel.tone(1)).toContain('is-warning');
    expect(panel.tone(2)).toContain('is-danger');
  });

  it('says where routing sent a request, or why it kept the model', () => {
    expect(lineFor({ step: 'model_route', category: 'code', from: 'flash', to: 'pro' })).toBe(
      'Routing: sent from flash to pro (category code)',
    );
    expect(lineFor({ step: 'model_route', action: 'unchanged', why: 'no_category_matched' })).toBe(
      'Routing: kept the model (no category matched)',
    );
    expect(
      lineFor({ step: 'model_route', action: 'unchanged', category: 'chat', why: 'matched' }),
    ).toBe('Routing: kept the model (category chat routes to the model asked for)');
    expect(lineFor({ step: 'model_route', action: 'unchanged', why: 'matched' })).toBe(
      'Routing: kept the model',
    );
    expect(lineFor({ step: 'model_route', action: 'not_asked', why: 'classifier_failed' })).toBe(
      'Routing: not asked (classifier unavailable)',
    );
    expect(lineFor({ step: 'model_route', action: 'not_asked', why: 'no_classifier' })).toBe(
      'Routing: not asked (no classifier configured)',
    );
  });

  it('says what the personal-data filter did, and what it did when it could not', () => {
    expect(lineFor({ step: 'pii_filter', action: 'redacted', why: 'pii-model' })).toBe(
      'Personal data: replaced (found by pii-model)',
    );
    expect(
      lineFor({ step: 'pii_filter', action: 'redacted', why: 'm', texts: 8, changed: 3 }),
    ).toBe('Personal data: replaced in 3 of 8 texts (found by m)');
    expect(lineFor({ step: 'pii_filter', action: 'unchanged', why: 'm' })).toBe(
      'Personal data: nothing to replace',
    );
    expect(lineFor({ step: 'pii_filter', action: 'blocked', why: 'detector timed out' })).toBe(
      'Personal data: could not be removed — refused — detector timed out',
    );
    expect(lineFor({ step: 'pii_filter', action: 'allowed', why: 'detector timed out' })).toBe(
      'Personal data: could not be removed — sent unchanged (configured to allow) — detector timed out',
    );
  });

  it('names each model passed over, and then the one that answered, before the notice', () => {
    const panel = render({
      outcome: 'served',
      model: 'gemini-2.5-pro',
      model_selection: 'fallback:2',
      pipeline_decisions: [
        { step: 'model_route', action: 'unchanged', why: 'no_category_matched' },
        { step: 'dispatch', action: 'skipped', to: 'claude-x', why: 'cannot read images' },
        { step: 'dispatch', action: 'failed', to: 'gemini-2.5-flash', why: 'HTTP 503' },
        { step: 'notice', action: 'shown', why: 'first request today' },
      ],
    });

    expect(panel.lines()).toEqual([
      'Routing: kept the model (no category matched)',
      'Model claude-x: skipped — cannot read images',
      'Model gemini-2.5-flash: failed — HTTP 503',
      'Answered by gemini-2.5-pro (fallback 2)',
      'Notice: shown — first request today',
    ]);
  });

  it('puts the answer before a notice when no model was passed over, and marks a route', () => {
    const panel = render({
      model: 'pro',
      model_selection: 'route',
      pipeline_decisions: [
        { step: 'model_route', from: 'flash', to: 'pro' },
        { step: 'notice', action: 'withheld' },
      ],
    });
    expect(panel.lines()).toEqual([
      'Routing: sent from flash to pro',
      'Answered by pro (routed)',
      'Notice: withheld',
    ]);
  });

  it('says which model answered even when nothing was decided on the way', () => {
    expect(render({ model: 'flash', model_selection: 'direct' }).lines()).toEqual([
      'Answered by flash',
    ]);
  });

  it('names no answering model for a request that was not answered', () => {
    const panel = render({
      outcome: 'upstream_error',
      status: 502,
      pipeline_decisions: [{ step: 'dispatch', action: 'failed', to: 'm1', why: 'HTTP 500' }],
    });
    expect(panel.lines()).toEqual(['Model m1: failed — HTTP 500']);
  });

  it('says a step or an action it does not know, rather than dropping it', () => {
    const panel = render({
      outcome: 'blocked_by_pipeline',
      status: 400,
      pipeline_decisions: [
        { step: 'toxicity_filter', action: 'blocked', why: 'hate' },
        { step: 'pii_filter', action: 'quarantined' },
        { step: 'dispatch', action: 'parked', to: 'm9' },
        { step: 'notice', action: 'deferred' },
        { step: 'model_route' },
        {},
      ],
    });
    expect(panel.lines()).toEqual([
      'toxicity filter: action blocked, why hate',
      'Personal data: action quarantined',
      'dispatch: action parked, to m9',
      'Notice: action deferred',
      'Routing: ran',
      'A step: ran',
    ]);
  });

  it('shows no timeline for a refusal nothing was decided about', () => {
    expect(render({ outcome: 'rate_limited', status: 429 }).testid('story')).toBeNull();
  });
});

describe('WhatHappenedPanel — around the timeline', () => {
  it('names the controls that ran on a fallback', () => {
    const panel = render({ degraded: { budgets: 'local counters', rate_limit: 'memory' } });
    expect(panel.testid('degraded')?.textContent).toBe(
      'Some controls ran on a fallback: budgets (on local counters), rate limit (on memory).',
    );
  });

  it('says nothing about fallbacks when none was used', () => {
    expect(render({ degraded: {} }).testid('degraded')).toBeNull();
    expect(render({ degraded: null }).testid('degraded')).toBeNull();
  });

  it('links to the pipeline test only when the page offers it', () => {
    expect(render().testid('try-pipeline')).toBeNull();
    const panel = render({}, ['/use-cases', 'uc-a', 'pipeline']);
    expect(panel.testid('try-pipeline')?.getAttribute('href')).toBe('/use-cases/uc-a/pipeline');
  });
});
