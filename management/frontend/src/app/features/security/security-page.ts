import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { AnomalyEvent, AnomalyRule, Me, Suspension } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { can } from '../../core/auth/roles';
import { Live, agoLabel } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';
import { describeAction, describeEvent, describeRule } from './rule-language';
import { SecurityRulesPanel } from './security-rules-panel';
import { SuspensionsPanel } from './suspensions-panel';

/** How often the console refreshes itself. Findings change at human speed. */
const REFRESH_SECONDS = 15;

/** How many older findings one "load older" press asks for. */
const OLDER_EVENTS_PAGE = 50;

function hasExpired(row: Suspension): boolean {
  return !!row.expires_at && new Date(row.expires_at).getTime() <= Date.now();
}

/**
 * The IT Security console (`FRD-502`): findings, suspensions, and the rules that apply everywhere.
 *
 * Three permissions live on this page and it keeps them apart: **seeing** every finding, **stopping**
 * traffic (`incident.suspend`) and **authoring** the rules that apply everywhere
 * (`anomaly.rule.global.write`). A reader who only sees gets the whole view and no kill switch, and
 * the page says who has one rather than offering a button that answers 403 (`FRD-206`).
 *
 * The page loads what its tab strip counts and owns the live refresh and the findings; the
 * suspensions and rules tabs are panels owning their forms and mutations (`CLAUDE.md` §3).
 */
@Component({
  selector: 'app-security-page',
  imports: [DatePipe, SuspensionsPanel, SecurityRulesPanel],
  templateUrl: './security-page.html',
  providers: [PageFeedback, Live],
})
export class SecurityPage implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  protected readonly feedback = inject(PageFeedback);
  protected readonly live = inject(Live);

  protected readonly me = signal<Me | null>(null);
  protected readonly events = signal<AnomalyEvent[]>([]);
  /** Set when there are older findings than the ones on screen — cursor, not offset. */
  protected readonly moreEvents = signal<string | null>(null);
  protected readonly loadingMore = signal(false);
  protected readonly suspensions = signal<Suspension[]>([]);
  protected readonly rules = signal<AnomalyRule[]>([]);
  protected readonly loading = signal(true);
  protected readonly tab = signal<'findings' | 'suspensions' | 'rules'>('findings');
  /** The finding whose detail row is open: why it fired is more than a table row holds. */
  protected readonly openEvent = signal<string | null>(null);
  /** Slugs for the kill switch's scope picker. */
  protected readonly useCases = signal<string[]>([]);

  /** Whether this caller may stop and restore traffic — **not** whether they may see this page. */
  protected readonly canStop = computed(() => can(this.me(), 'incident.suspend'));

  /** Whether this caller may author and change a rule that applies to every use case. */
  protected readonly canWriteGlobalRules = computed(() =>
    can(this.me(), 'anomaly.rule.global.write'),
  );

  protected readonly active = computed(() =>
    this.suspensions().filter((row) => !row.lifted_at && !hasExpired(row)),
  );
  protected readonly past = computed(() =>
    this.suspensions().filter((row) => row.lifted_at || hasExpired(row)),
  );

  ngOnInit(): void {
    this.meService.get().subscribe({ next: (me) => this.me.set(me), error: () => undefined });
    this.live.start(
      REFRESH_SECONDS,
      () => this.service.anomalies(),
      (page) => {
        this.events.set(page.events);
        this.moreEvents.set(page.next_cursor);
        this.loading.set(false);
      },
    );
    this.loadSuspensions();
    this.loadRules();
    // A failure leaves the picker with "everywhere" only: the control degrades rather than vanishes.
    this.service.list().subscribe({
      next: (useCases) => this.useCases.set(useCases.map((useCase) => useCase.slug).sort()),
      error: () => undefined,
    });
  }

  /** What a rule does, in a sentence — see `rule-language.ts` for why this is not the raw kind. */
  protected explain(rule: AnomalyRule): string {
    return describeRule(rule);
  }

  /** What a finding measured, against what, over how many requests. */
  protected explainEvent(event: AnomalyEvent): string {
    return describeEvent(event);
  }

  /** What was *done* about it — `ADR-0014` keeps that apart from what was detected. */
  protected explainAction(event: AnomalyEvent): string {
    return describeAction(event);
  }

  /** The rule a finding came from, when it is one this caller can also see. */
  protected ruleOf(event: AnomalyEvent): AnomalyRule | undefined {
    return this.rules().find((rule) => rule.name === event.rule && rule.kind === event.kind);
  }

  protected toggleEvent(id: string): void {
    this.openEvent.update((open) => (open === id ? null : id));
  }

  protected loadRules(): void {
    this.service.globalRules().subscribe({
      next: (rules) => this.rules.set(rules),
      error: (response: unknown) =>
        this.feedback.fail(response, 'Could not load the anomaly rules.'),
    });
  }

  protected ago(): string {
    return agoLabel(this.live.lastUpdated());
  }

  protected refreshNow(): void {
    this.live.refresh(
      () => this.service.anomalies(),
      (page) => {
        this.events.set(page.events);
        this.moreEvents.set(page.next_cursor);
      },
    );
    this.loadSuspensions();
  }

  /**
   * Fetch the next page of findings and append it.
   *
   * Live is switched **off** while paging, as in the trace view: a refresh would replace the first
   * page and throw away everything the reader had scrolled to.
   */
  protected loadOlderEvents(): void {
    const cursor = this.moreEvents();
    if (!cursor || this.loadingMore()) return;
    this.live.enabled.set(false);
    this.loadingMore.set(true);
    this.service.anomalies(OLDER_EVENTS_PAGE, undefined, cursor).subscribe({
      next: (page) => {
        this.events.update((rows) => [...rows, ...page.events]);
        this.moreEvents.set(page.next_cursor);
        this.loadingMore.set(false);
      },
      error: (response: unknown) => {
        this.loadingMore.set(false);
        this.feedback.fail(response, 'Could not load older findings.');
      },
    });
  }

  protected loadSuspensions(): void {
    this.service.suspensions().subscribe({
      next: (page) => this.suspensions.set(page.suspensions),
      error: (response: unknown) => {
        // A 403 is a real answer — this caller may see findings and not suspensions — so the list
        // stays empty and the panel says who may, rather than the page reporting a failure.
        if ((response as { status?: number })?.status !== 403) {
          this.feedback.fail(response, 'Could not load the suspensions.');
        }
      },
    });
  }
}
