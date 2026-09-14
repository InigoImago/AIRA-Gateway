import { DatePipe } from '@angular/common';
import { Component, DestroyRef, OnInit, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { Trace, TracePayload } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { can } from '../../core/auth/roles';
import { InfoHint } from '../../core/ui/info-hint';
import { Live, agoLabel } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';

const REFRESH_SECONDS = 10;
const PAGE = 50;
/** One typed address is one question, not one round trip per letter (`FRD-208`). */
const TYPING_PAUSE_MS = 300;

/** The closed outcome vocabulary, offered as a filter (`FRD-122`). */
const OUTCOMES = [
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
  'internal_error',
  'client_gone',
];

/**
 * What actually happened, request by request (`FRD-502` FR-9–12, `FRD-131` FR-7).
 *
 * Metadata only in the list; the prompt and answer open per row, gated and recorded by the server
 * (`FRD-505`, `ADR-0009`). Live by polling and paged by **cursor**: an offset page under an
 * appending table silently shows some rows twice and skips others.
 */
@Component({
  selector: 'app-traces-tab',
  imports: [DatePipe, FormsModule, InfoHint, RouterLink],
  templateUrl: './traces-tab.html',
  // `Live` on the component so its timer stops with the panel. `PageFeedback` comes from the page:
  // one banner per page, not one per panel.
  providers: [Live],
})
export class TracesTab implements OnInit {
  /** The use case to show, or empty for every one this caller may see (`FRD-505` FR-2). */
  readonly slug = input<string>('');

  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected readonly feedback = inject(PageFeedback);
  protected readonly live = inject(Live);

  protected readonly traces = signal<Trace[]>([]);
  /**
   * False when the gateway says this caller's visibility does not cover this use case.
   *
   * The gateway learns membership from Keycloak groups (`FRD-102`), not this console's member list,
   * so "no requests match" would state the wrong reason for an empty table.
   */
  protected readonly inScope = signal(true);
  protected readonly cursor = signal<string | null>(null);
  protected readonly loading = signal(true);
  protected readonly loadingMore = signal(false);

  protected readonly refusalsOnly = signal(false);
  protected readonly outcome = signal('');
  /** Only turns where the model asked for a function (`FRD-131` FR-7). */
  protected readonly toolsOnly = signal(false);
  /** Only requests a pipeline step objected to — blocked, or flagged and let through. */
  protected readonly flaggedOnly = signal(false);
  /** Offered to every role, including those that see everything. */
  protected readonly mine = signal(false);
  /** The API key's prefix — what an audit row carries; the secret half is never shown. */
  protected readonly credential = signal('');
  /** The server refuses this filter without an incident role, so it is offered on that condition. */
  protected readonly sourceIp = signal('');
  /**
   * One request by its id, from the content-read log's link (`FRD-622` FR-6). The row is shown and
   * marked; its content is still opened by a click, because opening it is a recorded read.
   */
  protected readonly focus = signal<string | null>(null);

  /** The row whose content is open. One at a time: several open payloads is an unreadable screen
   *  and a disclosure nobody meant to make. */
  protected readonly openPayload = signal<string | null>(null);
  protected readonly payload = signal<TracePayload | null>(null);
  protected readonly payloadLoading = signal(false);

  /** The permission the gateway enforces the incident fields with, as `/me` lists it. */
  protected readonly mayInvestigate = computed(() => can(this.me(), 'incident.investigate'));
  private readonly me = signal<{ permissions?: string[] } | null>(null);
  private readonly typed = new Subject<void>();

  protected readonly outcomes = OUTCOMES;

  ngOnInit(): void {
    // The request to focus on, from the address (`FRD-622` FR-6). Followed rather than read once:
    // a second link into a page that is already open changes only the query. Distinct, so a change
    // of another parameter — the selected tab — does not restart the list.
    const focusing = this.route.queryParamMap
      .pipe(
        map((params) => params.get('request') || null),
        distinctUntilChanged(),
      )
      .subscribe((requested) => {
        this.focus.set(requested);
        this.startLive();
      });
    this.destroyRef.onDestroy(() => focusing.unsubscribe());
    // A failure leaves the incident fields hidden — the safe direction, since the server would
    // refuse them anyway.
    this.meService.get().subscribe({
      next: (me) => this.me.set(me),
      error: () => undefined,
    });
    const typing = this.typed
      .pipe(debounceTime(TYPING_PAUSE_MS), distinctUntilChanged())
      .subscribe(() => this.startLive());
    this.destroyRef.onDestroy(() => typing.unsubscribe());
  }

  protected setCredential(value: string): void {
    this.credential.set(value);
    this.typed.next();
  }

  protected setSourceIp(value: string): void {
    this.sourceIp.set(value);
    this.typed.next();
  }

  /** Back to the whole list. The address changes and the list follows it, so a reload agrees. */
  protected clearFocus(): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { request: null },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** Restart the live view from the top — also on a filter change, since a cursor from one filter
   *  means nothing under another. */
  protected startLive(): void {
    this.loading.set(true);
    this.live.start(
      REFRESH_SECONDS,
      () => this.service.traces(this.query()),
      (page) => {
        // Replaced wholesale; rows are tracked by id, so the DOM is reused and the reader's scroll
        // position survives each refresh.
        this.traces.set(page.traces);
        this.cursor.set(page.next_cursor);
        this.inScope.set(page.in_scope !== false);
        this.loading.set(false);
      },
    );
  }

  /** Every filter, in one place: the first page and the next must ask the same question, or paging
   *  appends rows the reader has just excluded. */
  private query(): Record<string, unknown> {
    return {
      useCase: this.slug(),
      outcome: this.outcome(),
      refusalsOnly: this.refusalsOnly(),
      toolsOnly: this.toolsOnly(),
      flaggedOnly: this.flaggedOnly(),
      mine: this.mine(),
      credential: this.credential().trim(),
      sourceIp: this.sourceIp().trim(),
      requestId: this.focus() ?? '',
      limit: PAGE,
    };
  }

  protected toggleFlagged(value: boolean): void {
    this.flaggedOnly.set(value);
    this.startLive();
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

  /** Fetch the next page and append it. Live goes off while paging, or a refresh would replace the
   *  first page and discard what the reader scrolled to. */
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
    // A row from before `FRD-122` has no outcome; a 2xx there is served, never a red "200".
    if (row.outcome) return row.outcome === 'served';
    return row.status >= 200 && row.status < 300;
  }

  protected togglePayload(row: Trace): void {
    if (this.openPayload() === row.id) {
      this.openPayload.set(null);
      this.payload.set(null);
      return;
    }
    this.openPayload.set(row.id);
    this.payload.set(null);
    this.payloadLoading.set(true);
    // Live goes off while content is open, or a refresh would leave the open panel pointing at a
    // request no longer on screen.
    this.live.enabled.set(false);
    this.service.tracePayload(row.id).subscribe({
      next: (body) => {
        this.payload.set(body);
        this.payloadLoading.set(false);
      },
      error: (response: unknown) => {
        this.payloadLoading.set(false);
        this.openPayload.set(null);
        this.feedback.fail(response, 'Could not open this request.');
      },
    });
  }

  /** Pretty-printed, because a prompt is read by a person. */
  protected asText(value: unknown): string {
    if (value === null || value === undefined) return '';
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  }

  /** Money for people; the exact integer stays in the API for scripts. */
  protected cost(row: Trace): string {
    if (row.cost_nanos === null) return '—';
    return (row.cost_nanos / 1_000_000_000).toFixed(4);
  }
}
