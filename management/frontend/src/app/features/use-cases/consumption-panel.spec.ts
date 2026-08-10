import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ReportRow, UseCaseConsumption } from '../../core/api/models';
import { ConsumptionPanel } from './consumption-panel';

function reportRow(over: Partial<ReportRow> = {}): ReportRow {
  return {
    key: 'demo-uc',
    requests: 59,
    prompt_tokens: 4000,
    completion_tokens: 6664,
    total_tokens: 10664,
    cost_nanos: 3674900,
    cost: '0.003675',
    cached_input_tokens: 0,
    unpriced_requests: 0,
    failed_requests: 0,
    avg_latency_ms: 120,
    max_latency_ms: 900,
    ...over,
  };
}

const NOTHING: UseCaseConsumption = {
  month: null,
  today: null,
  unavailable: false,
  partial: false,
  reason: '',
  outOfScope: false,
};

function setup(consumption: Partial<UseCaseConsumption> = {}) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [ConsumptionPanel],
    providers: [provideRouter([])],
  });

  const fixture = TestBed.createComponent(ConsumptionPanel);
  fixture.componentRef.setInput('slug', 'demo-uc');
  fixture.componentRef.setInput('consumption', { ...NOTHING, ...consumption });
  fixture.detectChanges();

  const dom = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    dom,
    at: (testid: string) => dom.querySelector(`[data-testid="${testid}"]`),
    text: () => dom.textContent ?? '',
  };
}

/**
 * The defect this panel exists for, and the four ways it can be empty.
 *
 * Asserted on the **rendered DOM** rather than on the component's signals, because the fault was
 * never in the arithmetic: the figures existed in `request_logs` and nothing put them on screen.
 * A test of the computation would have passed against the broken console.
 */
describe('ConsumptionPanel', () => {
  it('shows what was consumed, with no budget anywhere in sight', () => {
    const panel = setup({
      month: reportRow(),
      today: reportRow({ requests: 3, total_tokens: 400, cost: '0.000120' }),
    });

    expect(panel.at('consumption-month-tokens')?.textContent).toContain('10664');
    expect(panel.at('consumption-month-cost')?.textContent).toContain('0.003675');
    expect(panel.at('consumption-month-requests')?.textContent).toContain('59');
    expect(panel.at('consumption-today-tokens')?.textContent).toContain('400');
  });

  /** Unknown is not zero. A gateway that did not answer must never render as "spent nothing". */
  it('says the figures are unknown rather than showing a zero it never received', () => {
    const panel = setup({ unavailable: true, reason: 'The gateway could not be reached.' });

    expect(panel.at('consumption-down')).not.toBeNull();
    expect(panel.at('consumption-month-cost')).toBeNull();
  });

  /**
   * §3's rule — no silent failures, and the **backend's own wording** rather than ours. "The
   * gateway did not answer" is a guess, and the wrong one for every failure that is not a timeout.
   */
  it('repeats what the server said about the failure', () => {
    const panel = setup({
      unavailable: true,
      reason: 'A reporting window may span at most 366 days.',
    });

    expect(panel.at('consumption-down')?.textContent).toContain('at most 366 days');
  });

  /**
   * "Nothing happened here" and "this is not yours to see" are two facts that look identical in
   * the rows. The second one names the Keycloak group, and says that administering a use case in
   * the console does not put you in it — which is the thing a reader would otherwise conclude was
   * a broken screen (`FRD-209`: AIRA never writes to the directory).
   */
  it('distinguishes a figure it may not see from a figure that is zero', () => {
    const panel = setup({ outOfScope: true });

    expect(panel.at('consumption-scope')?.textContent).toContain('/use-cases/demo-uc');
    expect(panel.at('consumption-scope')?.textContent).toContain('never writes to the directory');
    expect(panel.at('consumption-down')).toBeNull();
  });

  /**
   * One window arriving and the other not is a **third** state, and it is the one the first
   * version of this feature got wrong: a single failure flag written by two independent requests
   * hid the month that had already been fetched. What is known is shown; the dash says so.
   */
  it('shows the window that arrived when the other one did not', () => {
    const panel = setup({
      month: reportRow(),
      today: null,
      partial: true,
      reason: 'The gateway could not be reached.',
    });

    expect(panel.at('consumption-month-requests')?.textContent).toContain('59');
    expect(panel.at('consumption-today-requests')?.textContent).toContain('—');
    expect(panel.at('consumption-partial')).not.toBeNull();
    expect(panel.at('consumption-down')).toBeNull();
  });

  it('warns that unpriced traffic makes the spend a lower bound', () => {
    const panel = setup({ month: reportRow({ unpriced_requests: 4 }), today: reportRow() });

    expect(panel.at('consumption-unpriced')?.textContent).toContain('unknown, not zero');
  });

  it('shows a use case that really consumed nothing as zero, not as unknown', () => {
    const zero = reportRow({ requests: 0, total_tokens: 0, cost: '0.00', cost_nanos: 0 });
    const panel = setup({ month: zero, today: zero });

    expect(panel.at('consumption-month-requests')?.textContent).toContain('0');
    expect(panel.at('consumption-down')).toBeNull();
    expect(panel.at('consumption-partial')).toBeNull();
  });

  /**
   * Each figure carries what it counts (`FRD-206`). Asserted as **rendered text**, because the
   * hint takes projected content and passing it as an attribute is silently ignored by Angular —
   * three hints on the requests screen said nothing at all for exactly that reason.
   */
  it('explains what each figure counts', () => {
    const panel = setup({ month: reportRow(), today: reportRow() });

    expect(panel.at('help-consumption-cost')).toBeNull(); // closed until pointed at
    (panel.at('info-consumption-cost') as HTMLElement).click();
    panel.fixture.detectChanges();

    expect(panel.at('help-consumption-cost')?.textContent).toContain('unknown is not zero');
  });
});

describe('ConsumptionPanel — the cache share (`FRD-133`)', () => {
  /**
   * Without this figure "tune the caching empirically" is not possible: a cache that has silently
   * stopped working looks exactly like an expensive month, and switching a parameter tells you
   * nothing if no number moves.
   */
  it('shows what fraction of the input came from the cache', () => {
    const panel = setup({
      month: reportRow({ prompt_tokens: 20_000, cached_input_tokens: 18_000 }),
      today: reportRow({ prompt_tokens: 1_000, cached_input_tokens: 0 }),
    });

    expect(panel.at('consumption-month-cached')?.textContent).toContain('90%');
    expect(panel.at('consumption-today-cached')?.textContent).toContain('0%');
  });

  it('says nothing rather than 0% when there was no input at all', () => {
    /** 0 % of nothing is not a cache miss. A use case with no traffic would otherwise read as a
     *  cache that never works, and somebody would go looking for a fault. */
    const panel = setup({ month: reportRow({ prompt_tokens: 0, cached_input_tokens: 0 }) });

    expect(panel.at('consumption-month-cached')?.textContent?.trim()).toBe('—');
  });
});
