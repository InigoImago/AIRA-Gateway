import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { TechnicalRecord, Trace } from '../../core/api/models';
import { traceViewerUrl } from '../../core/runtime-config';
import { TechnicalDetails } from './technical-details';
import { technicalText } from './technical';

/**
 * A `499` as the reported one looked: four and a half minutes open, the provider still answering,
 * keepalives going out, and the caller's side closing the connection. What the panel has to make
 * plain is the silence before it closed — what tells a proxy's idle timeout from a cap on length.
 */
const LEFT: TechnicalRecord = {
  phase: 'mid_answer',
  timeline_ms: {
    dispatched: 41,
    response_started: 15_052,
    provider_first_chunk: 61_310,
    last_byte_to_caller: 239_880,
    caller_left: 240_013,
    recorded: 282_650,
  },
  to_caller: { bytes: 18_422, writes: 31, keepalives: 9 },
};

const REFUSED: TechnicalRecord = {
  phase: 'waiting_for_provider',
  timeline_ms: { dispatched: 12, recorded: 480 },
  provider: {
    status: 429,
    reason: 'RESOURCE_EXHAUSTED',
    request_id: 'req_011CUabc',
    retry_after: '30',
    host: 'europe-west1-aiplatform.googleapis.com',
  },
  exception: {
    chain: ['aira_gateway.upstreams.base.UpstreamError', 'httpx.ReadError'],
    errno: 'ECONNRESET',
  },
};

function trace(over: Partial<Trace> = {}): Trace {
  return {
    id: 'r1',
    created_at: '2026-10-30T10:00:00Z',
    operation: 'streamGenerateContent',
    api: 'gemini',
    model: 'claude-opus-5-5',
    requested_model: 'claude-opus-5-5',
    model_selection: 'direct',
    status: 499,
    outcome: 'client_gone',
    prompt_tokens: 1,
    completion_tokens: 1,
    total_tokens: 2,
    latency_ms: 282_650,
    cost_nanos: null,
    provider: 'vertex',
    region: 'europe-west1',
    trace_id: '5241c2e38610880761e6ea3b607f2bba',
    subject: 'ada',
    credential: 'aira_ab12',
    use_case: 'uc-a',
    tool_calls: null,
    technical: LEFT,
    ...over,
  };
}

@Component({
  selector: 'app-technical-host',
  imports: [TechnicalDetails],
  template: `<app-technical-details [trace]="trace()" />`,
})
class Host {
  readonly trace = signal(trace());
}

function render(over: Partial<Trace> = {}) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.trace.set(trace(over));
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    element,
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    all: (id: string) =>
      [...element.querySelectorAll(`[data-testid="${id}"]`)].map((node) =>
        node.textContent?.trim(),
      ),
  };
}

function setTraceUrl(url: string | undefined): void {
  (window as unknown as { __AIRA_CONFIG__?: unknown }).__AIRA_CONFIG__ = { traceUrl: url };
}

afterEach(() => setTraceUrl(undefined));

describe('TechnicalDetails', () => {
  it('lays the milestones out in the order they came, each with the gap before it', () => {
    const { element } = render();
    const rows = [...element.querySelectorAll('[data-testid="technical-timeline"] tbody tr')];

    expect(rows.map((row) => row.getAttribute('data-milestone'))).toEqual([
      'dispatched',
      'response_started',
      'provider_first_chunk',
      'last_byte_to_caller',
      'caller_left',
      'recorded',
    ]);
    const cells = (index: number) =>
      [...rows[index].querySelectorAll('td')].map((cell) => cell.textContent?.trim());
    expect(cells(4)).toEqual(['Caller’s connection closed', '240013', '+133']);
    expect(cells(0)[2]).toBe('');
  });

  it('says how long the connection was open and how long it had been silent when it closed', () => {
    const { all, testid } = render();

    expect(all('technical-connection')).toEqual([
      '240,013 ms (240.0 s)',
      '133 ms since the last byte',
    ]);
    expect(testid('technical-sent')?.textContent).toContain(
      '18,422 bytes in 31 writes, 9 keepalives',
    );
    expect(testid('technical-phase')?.textContent).toContain('still answering');
  });

  it('shows the provider’s status word, its request id and the exception chain as written', () => {
    const { all, testid } = render({ status: 429, outcome: 'upstream_error', technical: REFUSED });

    expect(all('technical-provider')).toEqual([
      '429',
      'RESOURCE_EXHAUSTED',
      'req_011CUabc',
      '30',
      'europe-west1-aiplatform.googleapis.com',
    ]);
    expect(testid('technical-chain')?.textContent).toBe(
      'aira_gateway.upstreams.base.UpstreamError ← httpx.ReadError',
    );
    expect(testid('technical-errno')?.textContent).toBe('ECONNRESET');
    expect(testid('technical-connection')).toBeNull();
  });

  it('is open for a request that failed and folded for one that was served', () => {
    expect((render().testid('technical') as HTMLDetailsElement).open).toBe(true);
    const served = render({
      status: 200,
      outcome: 'served',
      technical: { phase: 'completed', timeline_ms: { recorded: 3 } },
    });
    expect((served.testid('technical') as HTMLDetailsElement).open).toBe(false);
  });

  it('says there is no record rather than showing an empty table', () => {
    const { testid } = render({ technical: null });

    expect(testid('technical-none')).not.toBeNull();
    expect(testid('technical-timeline')).toBeNull();
  });

  it('links the trace where the installation names a viewer, and shows the id where it does not', () => {
    const plain = render();
    expect(plain.testid('technical-trace-id')?.textContent).toBe(
      '5241c2e38610880761e6ea3b607f2bba',
    );
    expect(plain.testid('technical-trace-link')).toBeNull();

    setTraceUrl('https://grafana.example/explore?query={trace_id}');
    const linked = render();
    expect(linked.testid('technical-trace-link')?.getAttribute('href')).toBe(
      'https://grafana.example/explore?query=5241c2e38610880761e6ea3b607f2bba',
    );
  });

  it('copies the same text it shows, and says so when the browser refuses', async () => {
    const written: string[] = [];
    const clipboard = { writeText: async (text: string) => void written.push(text) };
    Object.defineProperty(navigator, 'clipboard', { value: clipboard, configurable: true });
    const { testid, fixture } = render();

    (testid('technical-copy') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(written).toEqual([testid('technical-text')?.textContent]);
    expect(fixture.nativeElement.textContent).toContain('Copied.');

    clipboard.writeText = async () => {
      throw new Error('denied');
    };
    (testid('technical-copy') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(testid('technical-copy-failure')?.textContent).toContain('refused');
  });
});

describe('the text for a ticket', () => {
  it('carries the request, its course and what the caller was told — never who made it', () => {
    const text = technicalText(trace({ error: 'The caller left.' }));

    expect(text).toContain('AIRA request r1');
    expect(text).toContain('Trace id: 5241c2e38610880761e6ea3b607f2bba');
    expect(text).toContain('Phase: mid_answer');
    expect(text).toMatch(/caller_left\s+240013/);
    expect(text).toContain('Silence before it closed: 133 ms since the last byte');
    expect(text).toContain('Caller was told: The caller left.');
    // Strict data mode may withhold the person from the reader copying this; a ticket never needs it.
    expect(text).not.toContain('ada');
    expect(text).not.toContain('aira_ab12');
  });
});

describe('traceViewerUrl', () => {
  it('links only an http(s) address that names the id', () => {
    setTraceUrl('javascript:alert({trace_id})');
    expect(traceViewerUrl('abc')).toBeNull();
    setTraceUrl('https://grafana.example/explore');
    expect(traceViewerUrl('abc')).toBeNull();
    setTraceUrl('');
    expect(traceViewerUrl('abc')).toBeNull();
    setTraceUrl('http://tempo:3200/trace/{trace_id}?again={trace_id}');
    expect(traceViewerUrl('abc')).toBe('http://tempo:3200/trace/abc?again=abc');
    expect(traceViewerUrl(null)).toBeNull();
  });
});
