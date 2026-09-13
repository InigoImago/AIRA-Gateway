import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable, Subscription, interval } from 'rxjs';

/**
 * A view that refreshes itself, without the reader reloading anything (`FRD-502` FR-13–15).
 *
 * Polling rather than a stream: findings and traces change at human speed, and an interval poll of
 * an ordinary endpoint needs no long-lived connection, reconnect story or second delivery path.
 *
 * - **It stops** — on destroy, and while the tab is hidden, so a background tab is not a load
 *   generator.
 * - **It is visible** — `enabled` and `lastUpdated` are for the screen to show.
 * - **It never overlaps itself** — a tick is skipped while a request is in flight.
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
    // Stopped explicitly, not only through `takeUntilDestroyed`: that ends with whichever
    // `DestroyRef` was injected, which lives as long as the application when this is provided in
    // the environment rather than on a component.
    this.destroyRef.onDestroy(() => this.stop());
  }

  /**
   * Run `load` now, and then every `seconds` while enabled and visible. `load` reports its own
   * errors; a failed refresh does not stop the timer.
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
    // Skipped rather than queued: stacking requests behind a slow one turns a refresh interval into
    // a load test against the endpoint that is already struggling.
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
          // The caller's handler reports it; the timer survives, or one blip ends liveness.
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
