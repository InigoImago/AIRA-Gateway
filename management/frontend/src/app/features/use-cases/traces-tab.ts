import { DatePipe } from '@angular/common';
import { Component, DestroyRef, OnInit, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { Trace, TracePayload } from '../../core/api/models';
import { mayActOnIncidents } from '../../core/auth/roles';
import { UseCaseService } from '../../core/api/use-case.service';
import { RouterLink } from '@angular/router';
import { InfoHint } from '../../core/ui/info-hint';
import { Live, agoLabel } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';

const REFRESH_SECONDS = 10;
const PAGE = 50;
/** Long enough that a typed address is one question, short enough to feel immediate. Nine letters
 *  must not be nine round trips (`FRD-208`). */
const TYPING_PAUSE_MS = 300;

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
  imports: [DatePipe, FormsModule, InfoHint, RouterLink],
  templateUrl: './traces-tab.html',
  // `Live` is provided **here**, on the component, and not in the root injector: a timer that
  // outlives the screen that started it is a timer nobody stops. `PageFeedback` is deliberately
  // *not* listed — it comes from the page, because the rule is one banner per page, not one per
  // panel.
  providers: [Live],
})
export class TracesTab implements OnInit {
  /**
   * The use case to show, or **empty for every one this caller may see**.
   *
   * One component, two homes (`FRD-505` FR-2). IT Security works across use cases and had to open
   * one first — which means guessing which use case the incident is in, from a screen that exists
   * because nobody knows yet. The alternative was a second table; a rule restated at a second call
   * site is the defect `FRD-126`, `FRD-206` and `FRD-602` each paid for once.
   */
  readonly slug = input<string>('');

  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly destroyRef = inject(DestroyRef);
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
  /** Only the requests a pipeline step objected to — blocked, or flagged and let through.
   *  The owner's question in their own words: "show me the prompts that threw a warning". */
  protected readonly flaggedOnly = signal(false);
  /** "Only my own requests", offered to every role including those that see everything: somebody
   *  checking what *they* did should not have to read past everybody else. */
  protected readonly mine = signal(false);
  /** Which system: the API key's prefix, which is what an audit row carries and what a console
   *  can safely show — the secret half never leaves the moment it was issued. */
  protected readonly credential = signal('');
  /** Which machine. Refused by the server for anyone without an incident role, so the field is
   *  offered on the same condition rather than left to fail on use. */
  protected readonly sourceIp = signal('');

  /** Console-side, and deliberately the **same** predicate the gateway enforces with — a role list
   *  restated by hand is how `it-steuerung` came to stop traffic in one plane and not the other. */
  protected readonly mayInvestigate = computed(() => mayActOnIncidents(this.me()?.roles));
  private readonly me = signal<{ roles: string[] } | null>(null);
  private readonly typed = new Subject<void>();

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
    // Which controls to offer. A failure here leaves the incident fields hidden, which is the safe
    // direction: the server would refuse them anyway, and a field that 403s is worse than none.
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
      flaggedOnly: this.flaggedOnly(),
      mine: this.mine(),
      credential: this.credential().trim(),
      sourceIp: this.sourceIp().trim(),
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
    // A 2xx with **no recorded outcome** is a served request, not a failure. `outcome` arrived
    // with `FRD-122`; every row written before it is NULL, and the template's `outcome ?? status`
    // fallback then rendered a red badge reading "200" — a success marked as a problem, which is
    // the one thing a status column must never do. Reported from the running console.
    if (row.outcome) return row.outcome === 'served';
    return row.status >= 200 && row.status < 300;
  }

  // ---- the prompt and the answer (`FRD-505`) ----------------------------------------------

  /** The row whose content is open, if any. One at a time: a list of expanded payloads is a
   *  screen nobody can read and a disclosure nobody meant to make. */
  protected readonly openPayload = signal<string | null>(null);
  protected readonly payload = signal<TracePayload | null>(null);
  protected readonly payloadLoading = signal(false);

  protected togglePayload(row: Trace): void {
    if (this.openPayload() === row.id) {
      this.openPayload.set(null);
      this.payload.set(null);
      return;
    }
    this.openPayload.set(row.id);
    this.payload.set(null);
    this.payloadLoading.set(true);
    // Live is switched off while content is open: a refresh would replace the rows underneath
    // and leave an open panel pointing at a request that is no longer on screen.
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

  /** Money for people. The exact integer stays in the API, which is what a script should read. */
  protected cost(row: Trace): string {
    if (row.cost_nanos === null) return '—';
    return (row.cost_nanos / 1_000_000_000).toFixed(4);
  }
}
