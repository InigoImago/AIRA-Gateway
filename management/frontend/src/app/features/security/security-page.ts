import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { Observable, catchError, forkJoin, map, of } from 'rxjs';
import { errorMessage } from '../../core/api/error-message';
import { AnomalyEvent, AnomalyPage, AnomalyRule, Me, Suspension } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { can } from '../../core/auth/roles';
import { Live } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';
import { WITHHELD_REASON, targetName } from '../../core/ui/withheld';
import { describeAction, describeEvent, describeRule, figureText } from './rule-language';
import { SecurityRulesPanel } from './security-rules-panel';
import { SuspensionsPanel } from './suspensions-panel';

/** How often the console refreshes itself. Findings change at human speed. */
const REFRESH_SECONDS = 15;

/** How many older findings one "load older" press asks for. */
const OLDER_EVENTS_PAGE = 50;

/** What a failed findings load says when the server gives no reason of its own. */
const FINDINGS_FAILED = 'Could not load the findings.';

/** The list of what is stopped, or why there is none to show. */
type SuspensionsRead = { rows: Suspension[] } | { failure: unknown };

/** One live refresh: the findings, and what is stopped right now — on the same tick, because a
 *  rule's `block` creates a suspension at the moment it produces a finding. */
interface Snapshot {
  page: AnomalyPage;
  suspensions: SuspensionsRead;
}

function hasExpired(row: Suspension, now: number): boolean {
  return !!row.expires_at && new Date(row.expires_at).getTime() <= now;
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
  /** False when `/me` could not be read: then the page cannot say who may stop traffic, and a
   *  sentence naming who does would be a claim about this reader nobody checked. */
  protected readonly permissionsKnown = signal(true);
  protected readonly events = signal<AnomalyEvent[]>([]);
  /** Set when there are older findings than the ones on screen — cursor, not offset. */
  protected readonly moreEvents = signal<string | null>(null);
  /** `use_cases` when this caller sees only the findings of their own use cases. */
  protected readonly eventsScope = signal<string>('all');
  protected readonly loadingMore = signal(false);
  protected readonly suspensions = signal<Suspension[]>([]);
  /**
   * When the suspensions were last read, which is the moment "in force" is judged at. A signal,
   * because `Date.now()` inside a `computed` is read once and never again: an expired stop stayed
   * "in force right now" for as long as the page was open.
   */
  protected readonly checkedAt = signal(Date.now());
  /** This caller may not list suspensions; a count of zero would be a claim, not a figure. */
  protected readonly suspensionsHidden = signal(false);
  protected readonly rules = signal<AnomalyRule[]>([]);
  protected readonly loading = signal(true);
  /** The banner a failed findings load raised, so the next good load can take it down again. */
  protected readonly findingsFailure = signal<string | null>(null);
  protected readonly tab = signal<'findings' | 'suspensions' | 'rules'>('findings');
  /** The finding whose detail row is open: why it fired is more than a table row holds. */
  protected readonly openEvent = signal<string | null>(null);
  /** Slugs for the kill switch's scope picker. */
  protected readonly useCases = signal<string[]>([]);
  /** Why the scope picker offers only "everywhere", when the use-case list could not be read. */
  protected readonly useCasesError = signal<string | null>(null);

  /** Whether this caller may stop and restore traffic — **not** whether they may see this page. */
  protected readonly canStop = computed(() => can(this.me(), 'incident.suspend'));

  /** Whether this caller may author and change a rule that applies to every use case. */
  protected readonly canWriteGlobalRules = computed(() =>
    can(this.me(), 'anomaly.rule.global.write'),
  );

  protected readonly active = computed(() =>
    this.suspensions().filter((row) => !row.lifted_at && !hasExpired(row, this.checkedAt())),
  );
  protected readonly past = computed(() =>
    this.suspensions().filter((row) => row.lifted_at || hasExpired(row, this.checkedAt())),
  );

  ngOnInit(): void {
    this.meService.get().subscribe({
      next: (me) => this.me.set(me),
      error: (response: unknown) => {
        this.permissionsKnown.set(false);
        this.feedback.fail(
          response,
          'Could not load your permissions, so stopping traffic and authoring rules are not offered.',
        );
      },
    });
    this.live.start(
      REFRESH_SECONDS,
      () => this.snapshot(),
      (snapshot) => this.show(snapshot),
      (response) => this.reportFindingsFailure(response),
    );
    this.loadRules();
    this.service.list().subscribe({
      next: (useCases) => {
        this.useCases.set(useCases.map((useCase) => useCase.slug).sort());
        this.useCasesError.set(null);
      },
      // Said at the picker, where the narrowing is chosen: a picker offering only "everywhere" with
      // nothing said reads as "nothing narrower exists", and everywhere is the broadest stop.
      error: (response: unknown) =>
        this.useCasesError.set(errorMessage(response, 'The use cases could not be loaded.')),
    });
  }

  /** What a rule does, in a sentence — see `rule-language.ts` for why this is not the raw kind. */
  protected explain(rule: AnomalyRule): string {
    return describeRule(rule);
  }

  /** Whom a finding is about, or what kind of thing when strict data mode withholds it. */
  protected readonly targetName = targetName;
  protected readonly withheldReason = WITHHELD_REASON;

  /** What a finding measured, against what, over how many requests. */
  protected explainEvent(event: AnomalyEvent): string {
    return describeEvent(event);
  }

  /** What was *done* about it — `ADR-0014` keeps that apart from what was detected. */
  protected explainAction(event: AnomalyEvent): string {
    return describeAction(event);
  }

  /** A measurement or a threshold short enough for a cell: a spike's 450 is "4.5×". */
  protected figure(event: AnomalyEvent, value: number): string {
    return figureText(event.kind, value);
  }

  /**
   * The rule a finding came from, when it is one this caller can also see.
   *
   * A global rule and a use-case rule may share a name, and the event carries no rule id, so the
   * name alone can explain a finding with the other rule. The event's use case tells them apart:
   * a use-case rule's findings carry its use case, and a global rule's carry none — unless it is
   * raised about whole use cases, when it carries the one it measured.
   */
  protected ruleOf(event: AnomalyEvent): AnomalyRule | undefined {
    const named = this.rules().filter(
      (rule) =>
        rule.name === event.rule && rule.kind === event.kind && rule.target === event.target,
    );
    if (event.use_case === null) return named.find((rule) => rule.is_global);
    return (
      named.find((rule) => !rule.is_global && rule.use_case === event.use_case) ??
      (event.target === 'use_case' ? named.find((rule) => rule.is_global) : undefined)
    );
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

  protected refreshNow(): void {
    this.live.refresh();
  }

  /**
   * Fetch the next page of findings and append it.
   *
   * Live is **paused** while paging, as in the trace view — which also drops a refresh already in
   * flight: landing after the older page, it would replace the list with the first page and throw
   * away everything the reader had scrolled to.
   */
  protected loadOlderEvents(): void {
    const cursor = this.moreEvents();
    if (!cursor || this.loadingMore()) return;
    this.live.pause();
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

  /** Reload what is stopped, after this page stopped or restored something. */
  protected loadSuspensions(): void {
    this.readSuspensions().subscribe((read) => this.applySuspensions(read));
  }

  private snapshot(): Observable<Snapshot> {
    return forkJoin({ page: this.service.anomalies(), suspensions: this.readSuspensions() });
  }

  private show(snapshot: Snapshot): void {
    this.events.set(snapshot.page.events);
    this.moreEvents.set(snapshot.page.next_cursor);
    this.eventsScope.set(snapshot.page.scope);
    this.loading.set(false);
    // A banner about a load that has since succeeded contradicts the table under it. Only this
    // load's own banner is taken down: a failed save reported since stays.
    const failure = this.findingsFailure();
    if (failure && this.feedback.error() === failure) this.feedback.error.set(null);
    this.findingsFailure.set(null);
    this.applySuspensions(snapshot.suspensions);
  }

  private reportFindingsFailure(response: unknown): void {
    this.feedback.fail(response, FINDINGS_FAILED);
    this.findingsFailure.set(this.feedback.error());
    this.loading.set(false);
  }

  /** The suspension list as a value either way, so its failure cannot hide the findings. */
  private readSuspensions(): Observable<SuspensionsRead> {
    return this.service.suspensions().pipe(
      map((page): SuspensionsRead => ({ rows: page.suspensions })),
      catchError((failure: unknown) => of<SuspensionsRead>({ failure })),
    );
  }

  private applySuspensions(read: SuspensionsRead): void {
    this.checkedAt.set(Date.now());
    if ('rows' in read) {
      this.suspensions.set(read.rows);
      return;
    }
    // A 403 is a real answer — this caller may see findings and not suspensions — so the list
    // stays empty and the panel says who may, rather than the page reporting a failure.
    if ((read.failure as { status?: number })?.status === 403) {
      this.suspensionsHidden.set(true);
    } else {
      this.feedback.fail(read.failure, 'Could not load the suspensions.');
    }
  }
}
