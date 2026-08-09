import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AnomalyEvent, AnomalyRule, Me, Suspension } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { TablePager } from '../../core/ui/table-pager';
import { Live, agoLabel } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';
import { TableView } from '../../core/ui/table-view';
import { NEW_RULE, RuleForm } from './rule-form';
import { describeAction, describeEvent, describeRule, unitOf } from './rule-language';
import { mayActOnIncidents } from '../../core/auth/roles';

/** How often the console refreshes itself. Findings change at human speed. */
const REFRESH_SECONDS = 15;

/**
 * The IT Security console (`FRD-502`).
 *
 * Phase 5 built the rules, the engine and the enforcement, and none of it had a screen — which put
 * IT Security in exactly the position `FRD-206` was written about: a role whose console shows it
 * nothing.
 *
 * Two different permissions live on this page and the screen keeps them apart, because conflating
 * them is a defect this project has already made once: **seeing** every use case is an oversight
 * role, **stopping** traffic is an incident role. A read-only governance role gets the whole view
 * and no kill switch, and the page says who does rather than offering a button that answers 403.
 */
@Component({
  selector: 'app-security-page',
  imports: [DatePipe, FormsModule, InfoHint, TablePager, RouterLink, RuleForm],
  templateUrl: './security-page.html',
  providers: [PageFeedback, Live],
})
export class SecurityPage implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly confirmService = inject(ConfirmService);
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

  /**
   * Which row is open, by id. A table row is a summary; the answer to "why did this fire" is four
   * more fields, and a table wide enough to hold them is a table nobody can read.
   */
  protected readonly openEvent = signal<string | null>(null);
  protected readonly openRule = signal<number | null>(null);

  /** Which rule's form is open. The form itself owns the field values (`rule-form.ts`). */
  protected readonly editing = signal<number | null>(null);

  // The kill-switch form. Signals, because the app is zoneless: a plain property changed from a
  // callback schedules no re-render, and the inputs would keep the submitted text.
  protected readonly showStop = signal(false);
  protected readonly target = signal<'subject' | 'credential' | 'use_case'>('subject');
  protected readonly targetValue = signal('');
  protected readonly reason = signal('');
  protected readonly minutes = signal<number | null>(null);

  /**
   * Whether this caller may stop traffic — **not** whether they may see this page.
   *
   * `it-steuerung` sees every use case and every figure and writes nothing anywhere (PRD §154).
   * The gateway guarded its kill switch with a visibility predicate once, and a live round found
   * it by asking both planes the same question and getting different answers.
   */
  protected readonly canStop = computed(() => mayActOnIncidents(this.me()?.roles));

  protected readonly active = computed(() =>
    this.suspensions().filter((row) => !row.lifted_at && !this.hasExpired(row)),
  );
  protected readonly past = computed(() =>
    this.suspensions().filter((row) => row.lifted_at || this.hasExpired(row)),
  );
  /** Findings that were acted on rather than merely recorded — the ones worth reading first. */
  protected readonly enforced = computed(() =>
    this.events().filter((event) => event.action_taken !== 'alert'),
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
  }

  /** What a rule does, in a sentence — see `rule-language.ts` for why this is not the raw kind. */
  protected explain(rule: AnomalyRule): string {
    return describeRule(rule);
  }

  /** What a threshold is counted in — "% of requests" reads very differently from "× as much". */
  protected unit(rule: AnomalyRule): string {
    return unitOf(rule.kind);
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

  protected toggleRule(id: number): void {
    this.openRule.update((open) => (open === id ? null : id));
    // Closing a rule abandons an edit in progress rather than leaving a hidden form that would
    // save fields the reader can no longer see.
    if (this.openRule() !== id) this.editing.set(null);
  }

  /**
   * Whether this caller may change this rule.
   *
   * A **global** rule needs an incident role, which is the same predicate the server enforces
   * with. A **use-case** rule needs to manage that use case, and object-level permission is not in
   * the token — so rather than guess, the console says where that rule is edited. `FRD-206`: an
   * action nobody can carry out is worse than an absent one.
   */
  protected mayEdit(rule: AnomalyRule): boolean {
    return rule.is_global && this.canStop();
  }

  /**
   * Paging for the three lists that grow without bound (`FRD-505` FR-12).
   *
   * All three are append-only in practice — rules accumulate, and a suspension is *kept* after it
   * is lifted because "blocked for two hours last Tuesday" is what a review asks. Client-side,
   * deliberately: the endpoints return the caller's whole visible set and the counts on the tabs
   * are over that whole set, so paging in the browser keeps "12 rules" meaning twelve rules
   * rather than "twelve on this page" — the same reasoning that kept the model catalog local
   * (`FRD-208`).
   */
  protected readonly ruleView = new TableView<AnomalyRule>(this.rules, (rule) =>
    [rule.name, rule.kind, rule.use_case ?? 'everywhere', rule.action].join(' '),
  );
  protected readonly activeView = new TableView<Suspension>(this.active, (row) =>
    [row.target, row.target_value, row.author, row.reason].join(' '),
  );
  protected readonly pastView = new TableView<Suspension>(this.past, (row) =>
    [row.target, row.target_value, row.author, row.reason].join(' '),
  );

  /**
   * One box, both suspension lists.
   *
   * "Has this caller ever been stopped?" is one question, and it is answered by what is stopped
   * *now* together with what was stopped *before* — a search that covered only the first would
   * answer it wrongly and look like it had answered it.
   */
  protected searchSuspensions(value: string): void {
    this.activeView.search(value);
    this.pastView.search(value);
  }

  /** A global rule being authored, if any. `null` when nothing is being created. */
  protected readonly draft = signal<AnomalyRule | null>(null);

  protected startCreate(): void {
    this.editing.set(null);
    this.draft.set(NEW_RULE);
  }

  protected cancelCreate(): void {
    this.draft.set(null);
  }

  protected createRule(changes: Partial<AnomalyRule>): void {
    this.feedback.run(this.service.createGlobalRule(changes), {
      failure: 'Could not create this rule.',
      success: () => {
        this.feedback.succeed(
          `"${changes.name}" now applies to every use case. It reaches the gateway within a few ` +
            `seconds.`,
        );
        this.draft.set(null);
        this.loadRules();
      },
    });
  }

  protected startEdit(rule: AnomalyRule): void {
    this.editing.set(rule.id);
  }

  protected cancelEdit(): void {
    this.editing.set(null);
  }

  protected saveRule(rule: AnomalyRule, changes: Partial<AnomalyRule>): void {
    this.feedback.run(this.service.updateRule(rule.id, changes), {
      failure: 'Could not save this rule.',
      success: () => {
        this.feedback.succeed(`"${rule.name}" saved. It reaches the gateway within a few seconds.`);
        this.editing.set(null);
        this.loadRules();
      },
    });
  }

  protected removeRule(rule: AnomalyRule): void {
    const question =
      `Delete the rule "${rule.name}"? Nothing will be watched for it afterwards, and ` +
      `findings it already produced are kept.`;
    if (!this.confirmService.ask(question)) return;
    this.feedback.run(this.service.deleteRule(rule.id), {
      failure: 'Could not delete this rule.',
      success: () => {
        this.feedback.succeed(`"${rule.name}" deleted.`);
        this.openRule.set(null);
        this.editing.set(null);
        this.loadRules();
      },
    });
  }

  protected loadRules(): void {
    this.service.globalRules().subscribe({
      next: (rules) => this.rules.set(rules),
      error: (response: unknown) =>
        this.feedback.fail(response, 'Could not load the anomaly rules.'),
    });
  }

  protected hasExpired(row: Suspension): boolean {
    return !!row.expires_at && new Date(row.expires_at).getTime() <= Date.now();
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
   * Live is switched **off** while paging, exactly as the trace view does it: a refresh would
   * replace the first page and throw away everything the reader had scrolled to.
   */
  protected loadOlderEvents(): void {
    const cursor = this.moreEvents();
    if (!cursor || this.loadingMore()) return;
    this.live.enabled.set(false);
    this.loadingMore.set(true);
    this.service.anomalies(50, undefined, cursor).subscribe({
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
        // A caller who may see findings but not suspensions gets a 403 here. That is a real
        // answer, not a failure of the page: the list stays empty and the page says who may.
        if ((response as { status?: number })?.status !== 403) {
          this.feedback.fail(response, 'Could not load the suspensions.');
        }
      },
    });
  }

  protected canSubmit(): boolean {
    return !!this.targetValue().trim() && !this.feedback.busy();
  }

  protected stop(): void {
    if (!this.canSubmit()) return;
    const value = this.targetValue().trim();
    this.feedback.run(
      this.service.suspend({
        target: this.target(),
        target_value: value,
        reason: this.reason().trim(),
        minutes: this.minutes(),
      }),
      {
        failure: 'Could not stop this traffic.',
        success: () => {
          this.feedback.succeed(
            `${value} is stopped. It takes a few seconds to reach every gateway instance.`,
          );
          this.targetValue.set('');
          this.reason.set('');
          this.minutes.set(null);
          this.showStop.set(false);
          this.loadSuspensions();
        },
      },
    );
  }

  protected lift(row: Suspension): void {
    const question = `Restore access for ${row.target_value}? It was stopped by ${row.author}.`;
    if (!this.confirmService.ask(question)) return;
    this.feedback.run(this.service.liftSuspension(row.id), {
      failure: 'Could not restore access.',
      success: () => {
        this.feedback.succeed(
          `${row.target_value} is restored. It takes a few seconds to reach every instance.`,
        );
        this.loadSuspensions();
      },
    });
  }
}
