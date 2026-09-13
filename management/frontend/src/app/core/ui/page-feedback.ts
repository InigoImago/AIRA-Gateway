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
  /** Set while a mutation is in flight, so the triggering control can disable itself. */
  readonly busy = signal(false);

  /** Report a failure that did not come through `run` — a failed load, typically. */
  fail(response: unknown, fallback: string): void {
    this.error.set(errorMessage(response, fallback));
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
   */
  run<T>(request: Observable<T>, handlers: { failure: string; success: (value: T) => void }): void {
    this.busy.set(true);
    this.clear();
    request.subscribe({
      next: (value) => {
        this.busy.set(false);
        handlers.success(value);
      },
      error: (response: unknown) => {
        this.busy.set(false);
        this.fail(response, handlers.failure);
      },
    });
  }
}
