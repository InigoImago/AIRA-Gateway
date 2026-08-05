import { Injectable } from '@angular/core';

/**
 * Ask before doing something irreversible.
 *
 * Revoking a key, removing a member, or deleting a budget takes effect immediately and cannot
 * be undone from the UI, so each is one misplaced click away from an outage for whoever was
 * using it. Wrapping the prompt in a service (rather than calling `confirm` inline) keeps the
 * components testable and leaves one place to swap in a styled dialog later.
 */
@Injectable({ providedIn: 'root' })
export class ConfirmService {
  ask(message: string): boolean {
    return typeof window === 'undefined' || window.confirm(message);
  }
}
