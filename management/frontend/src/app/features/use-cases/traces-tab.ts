import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Trace } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Live, agoLabel } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';

const REFRESH_SECONDS = 10;
const PAGE = 50;

/**
 * What actually happened, request by request (`FRD-502` FR-9–12, `FRD-131` FR-7).
 *
 * **Metadata only — never a payload.** That is not the same thing as the per-request browsing
 * `ADR-0009` deferred: that reasoning is about showing *stored prompts* to *non-members*, and this
 * shows neither prompts nor anything to a non-member. `FRD-406` still blocks what it always
 * blocked.
 *
 * Live by polling, and paged by **cursor**: rows arrive while somebody reads, and an offset page
 * under an appending table shows some rows twice and skips others — invisibly, so the reader just
 * gets a wrong list.
 */
@Component({
  selector: 'app-traces-tab',
  imports: [DatePipe, FormsModule, InfoHint],
  templateUrl: './traces-tab.html',
  // `Live` is provided **here**, on the component, and not in the root injector: a timer that
  // outlives the screen that started it is a timer nobody stops. `PageFeedback` is deliberately
  // *not* listed — it comes from the page, because the rule is one banner per page, not one per
  // panel.
  providers: [Live],
})
export class TracesTab implements OnInit {
  readonly slug = input.required<string>();

  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);
  protected readonly live = inject(Live);

  protected readonly traces = signal<Trace[]>([]);
  /**
   * False when the gateway says this caller's visibility does not cover this use case.
   *
   * The gateway learns membership from Keycloak **groups** (`FRD-102`), and a use case created in
   * this console does not create one — so its own administrator can open this tab and see nothing
   * while traffic is flowing. "No requests match" would be the wrong reason, and an empty state
   * that states the wrong reason is worse than one that states none: the reader concludes the
   * recording is broken and then distrusts every figure on the page.
   */
  protected readonly inScope = signal(true);
  protected readonly cursor = signal<string | null>(null);
  protected readonly loading = signal(true);
  protected readonly loadingMore = signal(false);

  // Filters. The ones an investigation actually asks for.
  protected readonly refusalsOnly = signal(false);
  protected readonly outcome = signal('');
  /** Only the turns where the model asked for a function — the fastest way to what an agent has
   *  been trying to do (`FRD-131` FR-7). */
  protected readonly toolsOnly = signal(false);
  /** "Only my own requests", offered to every role including those that see everything: somebody
   *  checking what *they* did should not have to read past everybody else. */
  protected readonly mine = signal(false);

  /** The closed vocabulary, so nobody has to remember it (`FRD-122`). */
  protected readonly outcomes = [
    'served',
    'rate_limited',
    'budget_exceeded',
    'suspended',
    'blocked_by_pipeline',
    'no_capable_model',
    'model_not_found',
    'invalid_request',
    'request_too_large',
    'upstream_error',
    'client_gone',
  ];

  ngOnInit(): void {
    this.startLive();
  }

  /**
   * Restart the live view from the top.
   *
   * Called on a filter change as well: a cursor from one filter means nothing under another, and
   * paging on with it would silently mix two result sets.
   */
  protected startLive(): void {
    this.loading.set(true);
    this.live.start(
      REFRESH_SECONDS,
      () => this.service.traces(this.query()),
      (page) => {
        // Replaced wholesale, and the list is keyed by row id, so Angular reuses the DOM it
        // already has: a refresh that rebuilt the table would scroll the reader to the top every
        // ten seconds, which is what makes people switch a live view off.
        this.traces.set(page.traces);
        this.cursor.set(page.next_cursor);
        this.inScope.set(page.in_scope !== false);
        this.loading.set(false);
      },
    );
  }

  /**
   * Every filter, in one place.
   *
   * Written once because the first page and the next one must ask the **same** question: a cursor
   * came from a particular filter set, and paging with a different one appends rows the reader has
   * just excluded — one list, two meanings, and no error anywhere. Two hand-written call sites is
   * how that starts.
   */
  private query(): Record<string, unknown> {
    return {
      useCase: this.slug(),
      outcome: this.outcome(),
      refusalsOnly: this.refusalsOnly(),
      toolsOnly: this.toolsOnly(),
      mine: this.mine(),
      limit: PAGE,
    };
  }

  protected toggleTools(value: boolean): void {
    this.toolsOnly.set(value);
    this.startLive();
  }

  protected toggleMine(value: boolean): void {
    this.mine.set(value);
    this.startLive();
  }

  protected setOutcome(value: string): void {
    this.outcome.set(value);
    if (value) this.refusalsOnly.set(false);
    this.startLive();
  }

  protected toggleRefusals(on: boolean): void {
    this.refusalsOnly.set(on);
    if (on) this.outcome.set('');
    this.startLive();
  }

  /**
   * Fetch the next page and append it.
   *
   * Live is switched **off** while paging: a refresh would replace the first page and throw away
   * everything the reader had scrolled to.
   */
  protected loadMore(): void {
    const next = this.cursor();
    if (!next || this.loadingMore()) return;
    this.live.enabled.set(false);
    this.loadingMore.set(true);
    this.service.traces({ ...this.query(), cursor: next }).subscribe({
      next: (page) => {
        this.traces.update((rows) => [...rows, ...page.traces]);
        this.cursor.set(page.next_cursor);
        this.loadingMore.set(false);
      },
      error: (response: unknown) => {
        this.loadingMore.set(false);
        this.feedback.fail(response, 'Could not load more requests.');
      },
    });
  }

  protected ago(): string {
    return agoLabel(this.live.lastUpdated());
  }

  protected served(row: Trace): boolean {
    return row.outcome === 'served';
  }

  /** Money for people. The exact integer stays in the API, which is what a script should read. */
  protected cost(row: Trace): string {
    if (row.cost_nanos === null) return '—';
    return (row.cost_nanos / 1_000_000_000).toFixed(4);
  }
}
