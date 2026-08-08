import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AnomalyEvent, AnomalyRule, Me, Suspension } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Live, agoLabel } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';
import { describeAction, describeEvent, describeRule, unitOf } from './rule-language';

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
  imports: [DatePipe, FormsModule, InfoHint],
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

  // The rule editor. One rule at a time, and the fields a person actually changes: what counts as
  // too much, over how long, how many requests it takes to be worth judging, and what to do.
  protected readonly editing = signal<number | null>(null);
  protected readonly editThreshold = signal<number | null>(null);
  protected readonly editWindow = signal<number | null>(null);
  protected readonly editSample = signal<number | null>(null);
  protected readonly editParameter = signal<number | null>(null);
  protected readonly editAction = signal<string>('alert');
  protected readonly editMinutes = signal<number | null>(null);
  protected readonly editRpm = signal<number | null>(null);
  protected readonly editEnabled = signal(true);

  // The kill-switch form. Signals, because the app is zoneless.
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
  protected readonly canStop = computed(() => {
    const roles = this.me()?.roles ?? [];
    return roles.includes('it-security') || roles.includes('global-admin');
  });

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
      () => this.service.anomalies(200),
      (page) => {
        this.events.set(page.events);
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

  protected startEdit(rule: AnomalyRule): void {
    this.editing.set(rule.id);
    this.editThreshold.set(rule.threshold);
    this.editWindow.set(rule.window_minutes);
    this.editSample.set(rule.min_sample);
    this.editParameter.set(rule.parameter);
    this.editAction.set(rule.action);
    this.editMinutes.set(rule.action_minutes);
    this.editRpm.set(rule.throttle_rpm);
    this.editEnabled.set(rule.enabled);
  }

  protected cancelEdit(): void {
    this.editing.set(null);
  }

  protected saveRule(rule: AnomalyRule): void {
    this.feedback.run(
      this.service.updateRule(rule.id, {
        threshold: this.editThreshold() ?? rule.threshold,
        window_minutes: this.editWindow() ?? rule.window_minutes,
        min_sample: this.editSample() ?? 0,
        parameter: this.editParameter(),
        action: this.editAction(),
        action_minutes: this.editMinutes(),
        throttle_rpm: this.editRpm(),
        enabled: this.editEnabled(),
        // Sent unchanged because the server validates the pair — a threshold is only meaningful
        // against its kind, and a PATCH that omitted the kind would be validated against the
        // default rather than against this rule.
        kind: rule.kind,
        name: rule.name,
      }),
      {
        failure: 'Could not save this rule.',
        success: () => {
          this.feedback.succeed(
            `"${rule.name}" saved. It reaches the gateway within a few seconds.`,
          );
          this.editing.set(null);
          this.loadRules();
        },
      },
    );
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
      () => this.service.anomalies(200),
      (page) => this.events.set(page.events),
    );
    this.loadSuspensions();
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
