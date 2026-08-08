import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AnomalyEvent, AnomalyRule, Me, Suspension } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { Live, agoLabel } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';

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
  imports: [DatePipe, FormsModule],
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
