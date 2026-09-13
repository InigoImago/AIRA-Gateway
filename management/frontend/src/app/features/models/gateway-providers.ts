import { Injectable, inject, signal } from '@angular/core';
import { Subject } from 'rxjs';
import { errorMessage } from '../../core/api/error-message';
import { GatewayProvider } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';

/**
 * What this gateway is configured with: its upstreams, and where it permits processing
 * (`FRD-507` stage C, `ADR-0012` §6).
 *
 * Provided by the catalog page and read by both the browse window and the editor, so it is asked
 * once. Held, never authored: the providers and the residency policy are the gateway's.
 */
@Injectable()
export class GatewayProviders {
  private readonly service = inject(UseCaseService);

  /** The upstreams, or `null` until asked. */
  readonly list = signal<GatewayProvider[] | null>(null);
  /** Where processing is permitted. Empty means *the gateway did not say*, never *nothing is
   *  allowed* — the console then has no opinion and the gateway refuses at request time. */
  readonly allowedRegions = signal<string[]>([]);
  readonly loading = signal(false);
  /**
   * Why the list could not be fetched. Not fatal: declaring a model before its credential exists
   * is the ordinary order of work, so the editor degrades to a typed provider rather than locking
   * anybody out of their own catalog.
   */
  readonly error = signal<string | null>(null);
  /** Every answer as it lands: the list, or `null` when the gateway could not be asked. */
  readonly settled = new Subject<GatewayProvider[] | null>();

  /** Ask once. `then` runs when the list is known — at once if it already is; a call made while
   *  the question is in flight adds nothing. */
  load(then?: () => void): void {
    if (this.list() !== null) {
      then?.();
      return;
    }
    if (this.loading()) return;
    this.loading.set(true);
    this.service.providers().subscribe({
      next: ({ providers, allowedRegions }) => {
        this.loading.set(false);
        this.list.set(providers);
        this.allowedRegions.set(allowedRegions);
        then?.();
        this.settled.next(providers);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.list.set([]);
        this.settled.next(null);
        this.error.set(errorMessage(response, 'Could not ask the gateway which providers it has.'));
      },
    });
  }

  /** The configured provider of that name, if there is one. */
  find(name: string): GatewayProvider | null {
    return (this.list() ?? []).find((provider) => provider.name === name) ?? null;
  }
}
