import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable, Subscription, interval } from 'rxjs';

/**
 * A view that refreshes itself, without the reader reloading anything (`FRD-502` FR-13–15).
 *
 * Polling rather than a stream, deliberately. Server-sent events would push, and would also need a
 * long-lived connection per open console through whatever proxy sits in front, a reconnect story,
 * and a second delivery path for facts that already have one. Findings and traces change at human
 * speed, so an interval poll of an ordinary endpoint is the smaller thing — and the easier one to
 * reason about when it breaks.
 *
 * Three properties this exists to guarantee, each of which is a way live views go wrong:
 *
 * - **It stops.** On destroy, and while the tab is hidden. A console left open in a background tab
 *   overnight must not be a load generator.
 * - **It is visible.** `enabled` and `lastUpdated` are for the screen to show. A view that changes
 *   under somebody who did not ask it to is a view they stop trusting.
 * - **It never overlaps itself.** A slow response must not stack requests behind it; the next tick
 *   is skipped while one is in flight.
 */
@Injectable()
export class Live {
  private readonly destroyRef = inject(DestroyRef);

  /** Whether polling is on. Bound to a control, because the reader owns this. */
  readonly enabled = signal(true);
  /** When the last successful refresh landed — shown as "updated N seconds ago". */
  readonly lastUpdated = signal<Date | null>(null);
  /** True while a refresh is in flight, so a screen can say so without flickering its content. */
  readonly refreshing = signal(false);

  private subscription: Subscription | null = null;
  private inFlight = false;

  constructor() {
    // Explicit, rather than relying on `takeUntilDestroyed` alone. That operator ends with
    // *whichever* `DestroyRef` was injected, which depends on where this service is provided — on
    // a component it is the component's, in a module it is the environment's, and the second lives
    // as long as the application. A timer that only stops when the service happens to be provided
    // in the right place is a timer somebody will provide in the wrong one.
    this.destroyRef.onDestroy(() => this.stop());
  }

  /**
   * Run `load` now, and then every `seconds` while enabled and visible.
   *
   * `load` reports its own errors; a failed refresh must not stop the timer, or one blip ends the
   * liveness for the rest of the session.
   */
  start<T>(seconds: number, load: () => Observable<T>, onValue: (value: T) => void): void {
    this.stop();
    this.run(load, onValue);
    this.subscription = interval(seconds * 1000)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        if (!this.enabled() || this.hidden()) return;
        this.run(load, onValue);
      });
  }

  /** Refresh once, whatever the timer is doing — for a "refresh now" button. */
  refresh<T>(load: () => Observable<T>, onValue: (value: T) => void): void {
    this.run(load, onValue);
  }

  toggle(): void {
    this.enabled.update((on) => !on);
  }

  stop(): void {
    this.subscription?.unsubscribe();
    this.subscription = null;
  }

  private hidden(): boolean {
    return typeof document !== 'undefined' && document.visibilityState === 'hidden';
  }

  private run<T>(load: () => Observable<T>, onValue: (value: T) => void): void {
    // Skipped rather than queued: stacking requests behind a slow one turns a refresh interval
    // into a load test against the very endpoint that is already struggling.
    if (this.inFlight) return;
    this.inFlight = true;
    this.refreshing.set(true);
    load()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (value) => {
          onValue(value);
          this.lastUpdated.set(new Date());
          this.inFlight = false;
          this.refreshing.set(false);
        },
        error: () => {
          // The caller's own handler reports it. What matters here is that the timer survives:
          // one failed poll must not end the liveness for the rest of the session.
          this.inFlight = false;
          this.refreshing.set(false);
        },
      });
  }
}

/** "updated 12s ago" — short, and honest about how stale the screen is. */
export function agoLabel(at: Date | null, now: Date = new Date()): string {
  if (!at) return 'not yet';
  const seconds = Math.max(0, Math.round((now.getTime() - at.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes}m ago` : `${Math.round(minutes / 60)}h ago`;
}
