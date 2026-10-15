import { DatePipe } from '@angular/common';
import { Component, DestroyRef, OnInit, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Subject, Subscription, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { Trace, TracePayload, UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { can, peopleWithheld, strictData } from '../../core/auth/roles';
import { InfoHint } from '../../core/ui/info-hint';
import { Live } from '../../core/ui/live';
import { displayAmount, displayCount } from '../../core/ui/money';
import { PageFeedback } from '../../core/ui/page-feedback';
import { ANOTHER_MEMBER, WITHHELD_REASON } from '../../core/ui/withheld';
import { modeOf } from './data-protection-panel';
import { readableJson } from '../../core/ui/readable-json';

const REFRESH_SECONDS = 10;
const PAGE = 50;
/** One typed address is one question, not one round trip per letter (`FRD-208`). */
const TYPING_PAUSE_MS = 300;
/** Decimals a request's cost is shown to: requests are cheap, and a cent would read as free. */
const COST_PLACES = 4;

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
  /**
   * The use case itself, from the page that has it — what decides whether anybody but the content
   * roles may read its prompts. `null` on the Requests page, which spans every use case.
   */
  readonly useCase = input<UseCase | null>(null);

  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected readonly feedback = inject(PageFeedback);
  protected readonly live = inject(Live);

  /** The unit costs are in (`AIRA_CURRENCY`), or nothing until `/me` has said. */
  protected readonly currency = this.meService.currency;

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

  /** The row whose detail is open. One at a time: several open rows is an unreadable screen. */
  protected readonly openRow = signal<string | null>(null);
  /**
   * The prompt and answer read for the open row. Only ever set from an answer whose id is the open
   * row's: content shown under another request would attribute one person's prompt to another.
   */
  protected readonly payload = signal<TracePayload | null>(null);
  protected readonly payloadLoading = signal(false);
  /**
   * The open row's details, as the server answered when it recorded the opening. Rendered from
   * this answer and not from the list row: the screen shows what was opened, and only once the
   * opening is on record. Set only from an answer whose id is the open row's.
   */
  protected readonly detail = signal<Trace | null>(null);
  protected readonly detailLoading = signal(false);
  /** The opening did not answer; the reason is in the page's banner. */
  protected readonly detailFailed = signal(false);

  /** The permission the gateway enforces the incident fields with, as `/me` lists it. */
  protected readonly mayInvestigate = computed(() => can(this.me(), 'incident.investigate'));
  /**
   * Whether the key filter is offered. In strict data mode a reader who may not see people gets
   * `403` for it, since a key leads back to the person it was issued to; *Only my requests* stays.
   * Not before `/me` has answered, nor when it failed: until the mode is known the filter could be
   * the one the server refuses, so it is held back — as the incident fields are.
   */
  protected readonly mayFilterByKey = computed(() => {
    const me = this.me();
    return me !== null && !peopleWithheld(me);
  });
  /**
   * Whether *Show the prompt and response* is offered. The server lets `payload.read_any` read any
   * request; anybody else only inside a use case that stores content and leaves it to its people —
   * not `oversight`, which every use case is under strict data mode. Without the use case (the
   * Requests page) there is nothing to judge that by, so only `payload.read_any` is offered it. A
   * button that can only answer with a refusal is a control the reader cannot use.
   */
  protected readonly mayReadContent = computed(() => {
    const me = this.me();
    if (can(me, 'payload.read_any')) return true;
    const useCase = this.useCase();
    if (!useCase) return false;
    const mode = modeOf(useCase, strictData(me));
    return mode !== 'not-stored' && mode !== 'oversight';
  });
  private readonly me = signal<{ permissions?: string[]; strict_data?: boolean } | null>(null);
  protected readonly anotherMember = ANOTHER_MEMBER;
  protected readonly withheldReason = WITHHELD_REASON;
  /** What the two typed filters say, so a pause that changed nothing asks nothing. */
  private readonly typed = new Subject<string>();
  /** The content read in flight; dropped when another row is opened, so it can never land there. */
  private payloadRequest: Subscription | null = null;
  /** The opening in flight; dropped when another row is opened, for the same reason. */
  private detailRequest: Subscription | null = null;
  /** The older page in flight; dropped when the question changes, or it appends excluded rows. */
  private moreRequest: Subscription | null = null;
  /** The page banner's message for a failed live load, withdrawn when a later load answers. */
  private liveFailure: string | null = null;

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
    // The values themselves are compared, not the fact of a keystroke: every keystroke is the same
    // event, so a distinct-check over events lets the first pause through and no other.
    const typing = this.typed
      .pipe(debounceTime(TYPING_PAUSE_MS), distinctUntilChanged())
      .subscribe(() => this.startLive());
    this.destroyRef.onDestroy(() => {
      typing.unsubscribe();
      this.payloadRequest?.unsubscribe();
      this.detailRequest?.unsubscribe();
      this.moreRequest?.unsubscribe();
    });
  }

  protected setCredential(value: string): void {
    this.credential.set(value);
    this.typed.next(this.typedFilters());
  }

  protected setSourceIp(value: string): void {
    this.sourceIp.set(value);
    this.typed.next(this.typedFilters());
  }

  /** The typed filters as they are sent — trimmed, so a trailing space asks nothing new. */
  private typedFilters(): string {
    return `${this.credential().trim()}\n${this.sourceIp().trim()}`;
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
   *  means nothing under another. A load or a page of the previous question still on its way is
   *  dropped, so its rows can never appear under this one. */
  protected startLive(): void {
    this.moreRequest?.unsubscribe();
    this.moreRequest = null;
    this.loadingMore.set(false);
    this.closeRow();
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
        if (this.liveFailure) this.feedback.withdraw(this.liveFailure);
        this.liveFailure = null;
      },
      (response) => {
        this.loading.set(false);
        this.liveFailure = this.feedback.fail(response, 'Could not load the requests.');
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
      // Never sent where the field is not offered: a prefix typed before `/me` answered would
      // otherwise be refused on every refresh.
      credential: this.mayFilterByKey() ? this.credential().trim() : '',
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
   *  first page and discard what the reader scrolled to — including one already in flight. */
  protected loadMore(): void {
    const next = this.cursor();
    if (!next || this.loadingMore()) return;
    this.live.pause();
    this.loadingMore.set(true);
    this.moreRequest = this.service.traces({ ...this.query(), cursor: next }).subscribe({
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

  protected served(row: Trace): boolean {
    // A row from before `FRD-122` has no outcome; a 2xx there is served, never a red "200".
    if (row.outcome) return row.outcome === 'served';
    return row.status >= 200 && row.status < 300;
  }

  /**
   * Open or close a row's details — its metadata, never its content.
   *
   * The whole row is the target because a small button at the far left of a wide table is a small
   * target. Opening is recorded under the reader's name, so a click that ends a text selection —
   * somebody copying a cell — opens nothing. The prompt is never read here: that is a second,
   * deliberate click (`FRD-622`).
   */
  protected toggleRow(row: Trace): void {
    // A click that ends a text selection is somebody copying a cell, not opening the row.
    if (window.getSelection()?.toString()) return;
    this.flip(row);
  }

  /** The row's own button, for a keyboard and a screen reader: it opens the details, as the row
   *  does, and reads no content. */
  protected toggleDetail(row: Trace, event: Event): void {
    event.stopPropagation();
    this.flip(row);
  }

  /** Close the row if it is the open one, or open its details. Never reads the content. */
  private flip(row: Trace): void {
    if (this.openRow() === row.id) {
      this.closeRow();
    } else {
      this.openDetail(row);
    }
  }

  /**
   * The prompt and the answer for the open row — and, on the server, a record that they were read.
   *
   * A read still in flight for another row is dropped first, and an answer for any row but the open
   * one is never placed: opening A and then B must not show A's prompt under B's caller.
   */
  protected readPayload(row: Trace): void {
    this.dropPayload();
    this.payloadLoading.set(true);
    this.payloadRequest = this.service.tracePayload(row.id).subscribe({
      next: (body) => {
        this.payloadLoading.set(false);
        if (body.id === row.id && this.openRow() === row.id) this.payload.set(body);
      },
      error: (response: unknown) => {
        this.payloadLoading.set(false);
        this.feedback.fail(response, 'Could not open this request.');
      },
    });
  }

  /**
   * Open a row: ask the server for its details, which it records as opened. A failure is reported
   * through the page's banner, as a failed content read is; the row stays open and says so.
   */
  private openDetail(row: Trace): void {
    this.dropPayload();
    this.dropDetail();
    this.openRow.set(row.id);
    // Paused while a row is open: a refresh replaces the list, and the open row may then be off
    // the first page — the detail would point at a request no longer on screen.
    this.live.pause();
    this.detailLoading.set(true);
    this.detailRequest = this.service.traceDetail(row.id).subscribe({
      next: (body) => {
        this.detailLoading.set(false);
        if (body.trace.id === row.id && this.openRow() === row.id) this.detail.set(body.trace);
      },
      error: (response: unknown) => {
        this.detailLoading.set(false);
        this.detailFailed.set(true);
        this.feedback.fail(response, 'Could not open the details of this request.');
      },
    });
  }

  private closeRow(): void {
    this.dropPayload();
    this.dropDetail();
    this.openRow.set(null);
  }

  private dropDetail(): void {
    this.detailRequest?.unsubscribe();
    this.detailRequest = null;
    this.detail.set(null);
    this.detailLoading.set(false);
    this.detailFailed.set(false);
  }

  private dropPayload(): void {
    this.payloadRequest?.unsubscribe();
    this.payloadRequest = null;
    this.payload.set(null);
    this.payloadLoading.set(false);
  }

  /** Pretty-printed, with a string's line breaks shown as breaks: a prompt is read by a person. */
  protected asText(value: unknown): string {
    return readableJson(value);
  }

  /** Money for people, never through a float; the exact integer stays in the API for scripts. */
  protected cost(row: Trace): string {
    if (row.cost_nanos === null) return '—';
    const unit = this.currency();
    const amount = displayAmount(row.cost_nanos, COST_PLACES);
    return unit ? `${amount} ${unit}` : amount;
  }

  /** A token count, grouped like every other count here; unknown is a dash, never `0`. */
  protected tokens(count: number | null): string {
    return count === null ? '—' : displayCount(count);
  }
}
