import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { Observable, Subscription, interval } from 'rxjs';

/**
 * How often the "updated N ago" stamp re-reads the clock. The label counts seconds, so a coarser
 * tick would print an age that is wrong by up to the tick.
 */
const CLOCK_MS = 1000;

/** One live view's load: what to fetch, what to do with it, and how to report a failure. */
interface LiveJob<T> {
  load: () => Observable<T>;
  onValue: (value: T) => void;
  onError: (response: unknown) => void;
}

/**
 * A view that refreshes itself, without the reader reloading anything (`FRD-502` FR-13–15).
 *
 * Polling rather than a stream: findings and traces change at human speed, and an interval poll of
 * an ordinary endpoint needs no long-lived connection, reconnect story or second delivery path.
 *
 * - **It stops** — on destroy, and while the tab is hidden, so a background tab is not a load
 *   generator.
 * - **It is visible** — `enabled`, `refreshing` and `ago` are for the screen to show.
 * - **A timer tick never overlaps a load** — it is skipped while one is in flight, because stacking
 *   requests behind a slow one turns a refresh interval into a load test against the endpoint that
 *   is already struggling.
 * - **A new question replaces the old one** — `start` and `refresh` drop the load in flight, so an
 *   answer to the previous filter can never land under the new one.
 * - **Every failure is reported** — `onError` is required, so no caller can leave a view loading
 *   forever with nothing said.
 */
@Injectable()
export class Live {
  private readonly destroyRef = inject(DestroyRef);

  /** Whether polling is on. Bound to a control, because the reader owns this. */
  readonly enabled = signal(true);
  /** When the last successful refresh landed. */
  readonly lastUpdated = signal<Date | null>(null);
  /** True while a refresh is in flight, so a screen can say so without flickering its content. */
  readonly refreshing = signal(false);

  private readonly clock = signal(new Date());
  /**
   * The time, re-read every second while the page is visible — for anything on a live view that
   * changes with the clock rather than with the data, such as a stop that expires. A signal, because
   * this application is zoneless: `new Date()` read in a template is evaluated when something else
   * re-renders the view, and then stays put for as long as nothing does — with live off, for good.
   */
  readonly now = this.clock.asReadonly();
  /** "updated 12s ago" — how stale the screen is, re-read every second. */
  readonly ago = computed(() => agoLabel(this.lastUpdated(), this.now()));

  private job: LiveJob<unknown> | null = null;
  private timer: Subscription | null = null;
  private inFlight: Subscription | null = null;
  /** Bumped whenever a load is dropped, so a late callback from it can change nothing. */
  private generation = 0;
  /**
   * Whether the question `start` asked has been answered — a value or a failure. Until then the
   * load in flight is not a refresh but the only answer the view will get, and switching off must
   * not drop it: the view would say "loading" for ever with nothing on its way.
   */
  private settledOnce = false;

  constructor() {
    const ticking = interval(CLOCK_MS).subscribe(() => {
      if (!this.hidden()) this.clock.set(new Date());
    });
    // Stopped explicitly rather than through `takeUntilDestroyed`: that ends with whichever
    // `DestroyRef` was injected, which lives as long as the application when this is provided in
    // the environment rather than on a component.
    this.destroyRef.onDestroy(() => {
      this.stop();
      ticking.unsubscribe();
    });
  }

  /**
   * Run `load` now, and then every `seconds` while enabled and visible. A load already in flight —
   * the previous filter's, typically — is dropped. A failed refresh is reported through `onError`
   * and does not stop the timer: one blip must not end the liveness.
   */
  start<T>(
    seconds: number,
    load: () => Observable<T>,
    onValue: (value: T) => void,
    onError: (response: unknown) => void,
  ): void {
    this.stop();
    this.job = { load, onValue, onError } as LiveJob<unknown>;
    this.settledOnce = false;
    this.run();
    this.timer = interval(seconds * 1000).subscribe(() => {
      if (!this.enabled() || this.hidden() || this.inFlight) return;
      this.run();
    });
  }

  /**
   * Run the started load once, now, whatever the timer is doing — for a "refresh now" button. A
   * tick still in flight is dropped rather than waited for: the reader asked for now.
   */
  refresh(): void {
    this.cancel();
    this.run();
  }

  /**
   * Drop the load in flight, so its answer never lands. For a caller about to replace what the
   * view shows by other means — appending an older page, say — which a late refresh would undo.
   */
  cancel(): void {
    this.generation += 1;
    this.inFlight?.unsubscribe();
    this.inFlight = null;
    this.refreshing.set(false);
  }

  /**
   * Switch polling off and drop a refresh in flight: "off" means what is on screen stays put. The
   * first answer to the question on screen is kept, because nothing else would ever replace the
   * view's "loading" with rows or a failure.
   */
  pause(): void {
    this.enabled.set(false);
    if (this.settledOnce) this.cancel();
  }

  toggle(): void {
    if (this.enabled()) {
      this.pause();
    } else {
      this.enabled.set(true);
    }
  }

  stop(): void {
    this.timer?.unsubscribe();
    this.timer = null;
    this.cancel();
  }

  private hidden(): boolean {
    return typeof document !== 'undefined' && document.visibilityState === 'hidden';
  }

  /** Start the job's load. The callers decide what happens to one in flight: a tick never runs
   *  beside one, and `start` and `refresh` drop it first. */
  private run(): void {
    const job = this.job;
    if (!job) return;
    const generation = this.generation;
    const current = () => generation === this.generation;
    // Settled by the first value as well as by completion: a load that answers and never completes
    // must not hold off every later tick, and one that completes without answering must not leave
    // the view marked as refreshing.
    const settle = () => {
      if (!current()) return;
      this.inFlight = null;
      this.refreshing.set(false);
      this.settledOnce = true;
    };
    this.refreshing.set(true);
    const subscription = job.load().subscribe({
      next: (value) => {
        if (!current()) return;
        settle();
        job.onValue(value);
        this.lastUpdated.set(new Date());
        this.clock.set(new Date());
      },
      error: (response: unknown) => {
        if (!current()) return;
        settle();
        job.onError(response);
      },
      complete: settle,
    });
    // A load that answered synchronously has already settled; only a pending one is in flight.
    if (current() && this.refreshing()) this.inFlight = subscription;
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
