import { Component, inject } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { EMPTY, Observable, Subject, of, throwError } from 'rxjs';
import { Live, agoLabel } from './live';

/**
 * A host that provides `Live` **on the component**, exactly as the real screens do.
 *
 * The first version of this harness provided it in the testing module instead, and the
 * "stops on destroy" case failed with seven ticks after destruction — correctly. `DestroyRef`
 * resolves to whichever injector created the service, and an environment-level one outlives every
 * component. A harness that configures a service differently from production is a harness that
 * tests a different service.
 */
@Component({ selector: 'app-live-host', template: '', providers: [Live] })
class Host {
  readonly live = inject(Live);
}

function host() {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  return { fixture, live: fixture.componentInstance.live };
}

/** An error handler for the cases that are not about errors — it fails the test if called. */
const unexpected = (response: unknown) => {
  throw new Error(`unexpected failure: ${String(response)}`);
};

describe('Live', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('loads once immediately, and again on each tick', () => {
    const { live } = host();
    let calls = 0;
    const seen: number[] = [];

    live.start(
      5,
      () => of(++calls),
      (value) => seen.push(value),
      unexpected,
    );

    // The first load is not on the timer: a live view that shows nothing for its first interval
    // is one somebody reloads manually.
    expect(seen).toEqual([1]);
    vi.advanceTimersByTime(5000);
    vi.advanceTimersByTime(5000);
    expect(seen).toEqual([1, 2, 3]);
  });

  it('stops when it is switched off, and resumes when it is switched back on', () => {
    const { live } = host();
    const seen: number[] = [];
    let calls = 0;
    live.start(
      5,
      () => of(++calls),
      (v) => seen.push(v),
      unexpected,
    );

    live.toggle();
    vi.advanceTimersByTime(15000);
    expect(seen).toEqual([1]);

    live.toggle();
    vi.advanceTimersByTime(5000);
    expect(seen).toEqual([1, 2]);
  });

  it('does not poll while the tab is hidden', () => {
    // A console left open in a background tab overnight must not be a load generator.
    const { live } = host();
    const seen: number[] = [];
    let calls = 0;
    const visibility = vi
      .spyOn(document, 'visibilityState', 'get')
      .mockReturnValue('hidden' as DocumentVisibilityState);

    live.start(
      5,
      () => of(++calls),
      (v) => seen.push(v),
      unexpected,
    );
    vi.advanceTimersByTime(20000);
    expect(seen).toEqual([1]);

    visibility.mockReturnValue('visible' as DocumentVisibilityState);
    vi.advanceTimersByTime(5000);
    expect(seen).toEqual([1, 2]);
    visibility.mockRestore();
  });

  it('never stacks a timer tick behind a slow request', () => {
    // A refresh interval that queues turns into a load test against the endpoint that is already
    // struggling.
    const { live } = host();
    const pending = new Subject<number>();
    let started = 0;

    live.start(
      1,
      () => {
        started += 1;
        return pending as Observable<number>;
      },
      () => undefined,
      unexpected,
    );

    vi.advanceTimersByTime(5000);
    expect(started).toBe(1);

    pending.next(1);
    pending.complete();
    expect(live.lastUpdated()).not.toBeNull();
  });

  it('survives a failed poll and keeps the timer', () => {
    // One blip must not end the liveness for the rest of the session.
    const { live } = host();
    let calls = 0;
    const seen: number[] = [];

    live.start(
      5,
      () => {
        calls += 1;
        return calls === 1 ? throwError(() => new Error('nope')) : of(calls);
      },
      (v) => seen.push(v),
      () => undefined,
    );

    expect(seen).toEqual([]);
    vi.advanceTimersByTime(5000);
    expect(seen).toEqual([2]);
  });

  it('hands every failure to the caller, so a view cannot stay "loading" with nothing said', () => {
    // The contract used to be "`load` reports its own errors", and no caller's load did: a gateway
    // that was down left "Loading…" on screen indefinitely with no banner.
    const { live } = host();
    const reported: unknown[] = [];

    live.start(
      5,
      () => throwError(() => ({ status: 502 })),
      () => undefined,
      (response) => reported.push(response),
    );
    expect(reported).toEqual([{ status: 502 }]);
    expect(live.refreshing()).toBe(false);

    vi.advanceTimersByTime(5000);
    expect(reported.length).toBe(2);
  });

  it('reports when it last succeeded, and only then', () => {
    const { live } = host();
    let fail = true;
    live.start(
      5,
      () => (fail ? throwError(() => new Error('nope')) : of(1)),
      () => undefined,
      () => undefined,
    );
    expect(live.lastUpdated()).toBeNull();

    fail = false;
    live.refresh();
    expect(live.lastUpdated()).not.toBeNull();
    expect(live.refreshing()).toBe(false);
  });

  it('stops on destroy', () => {
    const { fixture, live } = host();
    let calls = 0;
    live.start(
      5,
      () => of(++calls),
      () => undefined,
      unexpected,
    );

    fixture.destroy();
    vi.advanceTimersByTime(30000);

    expect(calls).toBe(1);
  });
});

describe('Live — a new question replaces the old one', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('runs a new start at once, even while the previous load is in flight', () => {
    // A filter change while a poll was in flight used to be skipped: the old filter's rows then
    // landed under the new filter, for a whole interval — indefinitely with live off.
    const { live } = host();
    const oldFilter = new Subject<string>();
    let newFilterLoads = 0;
    const shown: string[] = [];

    live.start(
      10,
      () => oldFilter as Observable<string>,
      (rows) => shown.push(rows),
      unexpected,
    );
    live.start(
      10,
      () => {
        newFilterLoads += 1;
        return of('rows for the NEW filter');
      },
      (rows) => shown.push(rows),
      unexpected,
    );

    expect(newFilterLoads).toBe(1);
    oldFilter.next('rows for the OLD filter');
    expect(shown).toEqual(['rows for the NEW filter']);
  });

  it('never delivers the answer to a question that was replaced, even after it lands', () => {
    const { live } = host();
    const oldFilter = new Subject<string>();
    const newFilter = new Subject<string>();
    const shown: string[] = [];
    const show = (rows: string) => shown.push(rows);

    live.start(10, () => oldFilter as Observable<string>, show, unexpected);
    live.start(10, () => newFilter as Observable<string>, show, unexpected);
    newFilter.next('new');
    oldFilter.next('old');
    vi.advanceTimersByTime(9_999);

    expect(shown).toEqual(['new']);
  });

  it('refreshes now, replacing a tick that is still in flight', () => {
    // "Refresh now" was dropped while a poll was in flight, and the button did nothing.
    const { live } = host();
    const answers = [new Subject<number>(), new Subject<number>()];
    let calls = 0;
    const seen: number[] = [];

    live.start(
      5,
      () => answers[Math.min(calls++, 1)] as Observable<number>,
      (v) => seen.push(v),
      unexpected,
    );
    live.refresh();

    expect(calls).toBe(2);
    answers[0].next(1);
    answers[1].next(2);
    expect(seen).toEqual([2]);
  });

  it('settles a load that completes without an answer, and keeps polling', () => {
    // Nothing reset the in-flight mark on an empty completion: the view said "refreshing" for
    // ever and every later tick was skipped as overlapping.
    const { live } = host();
    let calls = 0;

    live.start(
      5,
      () => {
        calls += 1;
        return EMPTY;
      },
      () => undefined,
      unexpected,
    );
    expect(live.refreshing()).toBe(false);

    vi.advanceTimersByTime(10_000);
    expect(calls).toBe(3);
  });

  it('settles a load that answers and never completes', () => {
    const { live } = host();
    const open = new Subject<number>();
    let calls = 0;

    live.start(
      5,
      () => {
        calls += 1;
        return open as Observable<number>;
      },
      () => undefined,
      unexpected,
    );
    open.next(1);
    vi.advanceTimersByTime(5000);

    expect(calls).toBe(2);
  });

  it('drops the answer in flight when asked to, so a page appended by hand is not replaced', () => {
    const { live } = host();
    const pending = new Subject<string>();
    const shown: string[] = [];

    live.start(
      5,
      () => pending as Observable<string>,
      (rows) => shown.push(rows),
      unexpected,
    );
    live.cancel();
    pending.next('the first page again');

    expect(shown).toEqual([]);
    expect(live.refreshing()).toBe(false);
  });

  it('switching off drops a refresh in flight: "off" means what is on screen stays put', () => {
    const { live } = host();
    const refresh = new Subject<string>();
    const shown: string[] = [];
    let calls = 0;

    live.start(
      5,
      () => (++calls === 1 ? of('first rows') : (refresh as Observable<string>)),
      (rows) => shown.push(rows),
      unexpected,
    );
    vi.advanceTimersByTime(5000);
    live.toggle();
    refresh.next('late rows');

    expect(live.enabled()).toBe(false);
    expect(shown).toEqual(['first rows']);
    expect(live.refreshing()).toBe(false);
  });

  it('switching off before the first answer still shows it, so the view never loads for ever', () => {
    // Switched off while the first load was on its way, the view kept "Loading…" for good: the
    // answer was dropped, and with polling off nothing else would ever replace it.
    const { live } = host();
    const first = new Subject<string>();
    const shown: string[] = [];
    let calls = 0;

    live.start(
      5,
      () => {
        calls += 1;
        return first as Observable<string>;
      },
      (rows) => shown.push(rows),
      unexpected,
    );
    live.toggle();
    first.next('first rows');
    vi.advanceTimersByTime(30_000);

    expect(shown).toEqual(['first rows']);
    expect(live.refreshing()).toBe(false);
    expect(calls).toBe(1);
  });

  it('switching off before the first answer still reports its failure', () => {
    const { live } = host();
    const first = new Subject<string>();
    const failures: unknown[] = [];

    live.start(
      5,
      () => first as Observable<string>,
      () => undefined,
      (response) => failures.push(response),
    );
    live.pause();
    first.error('gateway unreachable');

    expect(failures).toEqual(['gateway unreachable']);
  });
});

describe('Live — how stale the screen is', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('ages the stamp while nothing refreshes, rather than freezing it at "0s ago"', () => {
    // The stamp was computed from a clock no signal carried: in a zoneless application it was
    // evaluated when a refresh landed and then stayed "0s ago" for as long as live was off.
    const { live } = host();
    live.start(
      10,
      () => of(1),
      () => undefined,
      unexpected,
    );
    live.pause();
    expect(live.ago()).toBe('0s ago');

    vi.advanceTimersByTime(3 * 60 * 1000);

    expect(live.ago()).toBe('3m ago');
  });

  it('says "not yet" before anything has landed', () => {
    expect(host().live.ago()).toBe('not yet');
  });
});

describe('agoLabel', () => {
  it('says how stale the screen is, in the unit a person reads', () => {
    const now = new Date('2026-08-08T12:00:00Z');
    expect(agoLabel(null)).toBe('not yet');
    expect(agoLabel(new Date('2026-08-08T11:59:48Z'), now)).toBe('12s ago');
    expect(agoLabel(new Date('2026-08-08T11:55:00Z'), now)).toBe('5m ago');
    expect(agoLabel(new Date('2026-08-08T09:00:00Z'), now)).toBe('3h ago');
  });

  it('never says a negative age', () => {
    // Clock skew between the browser and the server is normal; "-3s ago" is not.
    const now = new Date('2026-08-08T12:00:00Z');
    expect(agoLabel(new Date('2026-08-08T12:00:05Z'), now)).toBe('0s ago');
  });
});
