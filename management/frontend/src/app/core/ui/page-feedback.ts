import { Injectable, signal } from '@angular/core';
import { Observable } from 'rxjs';
import { errorMessage } from '../api/error-message';

/**
 * The outcome of the last thing the user asked for, and whether something is still in flight.
 *
 * One page shows **one** banner, so the state lives here, provided once per page, and every panel
 * reports into it — which is what lets a page be split into panels without splitting its banner
 * (`CLAUDE.md` §3).
 */
@Injectable()
export class PageFeedback {
  /** The last failure, already unwrapped from the backend's error envelope. */
  readonly error = signal<string | null>(null);
  /** The last success, in the user's terms. */
  readonly notice = signal<string | null>(null);
  /**
   * Set while a mutation is in flight, so the triggering control can disable itself. True until
   * **every** run has settled: two panels share one page, and the first to finish must not
   * re-enable the other's controls while its request is still on its way.
   */
  readonly busy = signal(false);

  private running = 0;

  /**
   * Report a failure that did not come through `run` — a failed load, typically. Returns what is
   * now shown, so a load that later succeeds can {@link withdraw} exactly that.
   */
  fail(response: unknown, fallback: string): string {
    const message = errorMessage(response, fallback);
    this.error.set(message);
    return message;
  }

  /**
   * Take back a failure this caller reported, once it no longer holds — a live view whose next
   * poll answered. Someone else's failure, reported since, stays.
   */
  withdraw(message: string): void {
    if (this.error() === message) this.error.set(null);
  }

  /** Report a success in the user's terms. */
  succeed(message: string): void {
    this.notice.set(message);
  }

  clear(): void {
    this.error.set(null);
    this.notice.set(null);
  }

  /**
   * Run a mutation, reporting its outcome either way — the error branch is the one that gets
   * forgotten. `failure` is the fallback wording; the backend's own message is preferred.
   *
   * Settled by its answer, its failure **or its completion**: a request that completes without
   * answering would otherwise leave every control on the page disabled.
   */
  run<T>(request: Observable<T>, handlers: { failure: string; success: (value: T) => void }): void {
    this.running += 1;
    this.busy.set(true);
    this.clear();
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      this.running -= 1;
      this.busy.set(this.running > 0);
    };
    request.subscribe({
      next: (value) => {
        settle();
        handlers.success(value);
      },
      error: (response: unknown) => {
        settle();
        this.fail(response, handlers.failure);
      },
      complete: settle,
    });
  }
}
