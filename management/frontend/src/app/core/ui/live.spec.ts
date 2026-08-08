import { Component, inject } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
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
    );
    vi.advanceTimersByTime(20000);
    expect(seen).toEqual([1]);

    visibility.mockReturnValue('visible' as DocumentVisibilityState);
    vi.advanceTimersByTime(5000);
    expect(seen).toEqual([1, 2]);
    visibility.mockRestore();
  });

  it('never stacks a request behind a slow one', () => {
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
    );

    expect(seen).toEqual([]);
    vi.advanceTimersByTime(5000);
    expect(seen).toEqual([2]);
  });

  it('reports when it last succeeded, and only then', () => {
    const { live } = host();
    live.start(
      5,
      () => throwError(() => new Error('nope')),
      () => undefined,
    );
    expect(live.lastUpdated()).toBeNull();

    live.refresh(
      () => of(1),
      () => undefined,
    );
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
    );

    fixture.destroy();
    vi.advanceTimersByTime(30000);

    expect(calls).toBe(1);
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
