import { Injectable, signal } from '@angular/core';
import { Observable } from 'rxjs';
import { errorMessage } from '../api/error-message';

/**
 * The outcome of the last thing the user asked for, and whether something is still in flight.
 *
 * One page shows **one** banner: several screens each announcing their own outcome is how a
 * user ends up reading three messages and acting on the wrong one. So the state lives here,
 * provided once per page, and every panel on that page reports into it rather than keeping its
 * own copy — which is what lets a page be split into panels without splitting its banner.
 *
 * `run` exists because every mutation on every panel needs the same four steps, and the one that
 * gets forgotten is always the error branch. A load or a save whose failure is silent is worse
 * than one that fails loudly: the user believes it worked.
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
   * Run a mutation, reporting its outcome either way.
   *
   * `failure` is the fallback wording: where the backend sends its error envelope that message
   * is preferred, because it says what actually went wrong rather than what the caller guessed.
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
