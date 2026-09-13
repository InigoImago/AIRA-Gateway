import { NgTemplateOutlet } from '@angular/common';
import { Component, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LimitScope, RateLimit } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * The rate-limit panel of a use case (`FRD-405`).
 *
 * The list is an input: the parent's tab bar shows its count before any tab is opened. This panel
 * owns the form state, validation and mutations, and reports through the page's `PageFeedback`.
 */
@Component({
  selector: 'app-rate-limits-tab',
  imports: [NgTemplateOutlet, FormsModule, InfoHint, Modal],
  templateUrl: './rate-limits-tab.html',
})
export class RateLimitsTab {
  readonly slug = input.required<string>();
  readonly limits = input.required<RateLimit[]>();
  /** The server's object-level answer, passed down by the page — it is not in the token. */
  readonly canManage = input(false);
  /** Raised after a change lands, so the parent can reload what it owns. */
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly showForm = signal(false);
  protected readonly rlScope = signal<LimitScope>('use_case');
  protected readonly rlRpm = signal<number | null>(null);
  protected readonly rlBurst = signal<number | null>(null);

  protected validationError(): string | null {
    const rpm = this.rlRpm();
    if (rpm == null) return 'Set how many requests per minute are allowed.';
    if (!Number.isInteger(rpm) || rpm < 1) return 'At least 1 request per minute.';
    const burst = this.rlBurst();
    if (burst != null && (!Number.isInteger(burst) || burst < 1)) {
      return 'A burst must be at least 1, or left empty.';
    }
    return null;
  }

  protected canAdd(): boolean {
    return !this.validationError() && !this.feedback.busy();
  }

  protected add(): void {
    if (!this.canAdd()) {
      return;
    }
    const limit: RateLimit = {
      scope: this.rlScope(),
      // No scope names a person any more; the field stays on the wire and is ignored.
      subject: '',
      limit_rpm: this.rlRpm() ?? 0,
      burst: this.rlBurst() ?? 0,
      // Stated, not defaulted: the endpoint upserts on (scope, subject), so a body silent about
      // `enabled` would re-enable a lifted limit.
      enabled: true,
    };
    this.feedback.run(this.service.createRateLimit(this.slug(), limit), {
      failure: 'Could not save the rate limit.',
      success: () => {
        this.feedback.succeed('Rate limit saved.');
        this.rlRpm.set(null);
        this.rlBurst.set(null);
        this.showForm.set(false);
        this.changed.emit();
      },
    });
  }

  /**
   * Lift a limit without losing it, or put it back.
   *
   * The gateway obeys the flag, so a limit lifted for an incident stays on record rather than
   * being deleted. The whole row is sent because the endpoint upserts: a body carrying only the
   * switch would blank the figures beside it.
   */
  protected setEnabled(limit: RateLimit, enabled: boolean): void {
    if (!this.canManage() || this.feedback.busy()) return;
    this.feedback.run(this.service.createRateLimit(this.slug(), { ...limit, enabled }), {
      failure: enabled ? 'Could not enable the rate limit.' : 'Could not disable the rate limit.',
      success: () => {
        this.feedback.succeed(
          enabled
            ? 'Rate limit enabled. Requests are throttled again.'
            : 'Rate limit disabled. It is kept on record and stops binding.',
        );
        this.changed.emit();
      },
    });
  }

  protected remove(id: number | undefined): void {
    if (
      id == null ||
      !this.confirmService.ask('Remove this rate limit? Requests stop being throttled.')
    ) {
      return;
    }
    this.feedback.run(this.service.deleteRateLimit(this.slug(), id), {
      failure: 'Could not remove the rate limit.',
      success: () => {
        this.feedback.succeed('Rate limit removed.');
        this.changed.emit();
      },
    });
  }

  /** True where a per-head row exists, which is what makes the two-allowance note relevant. */
  protected hasPerHead(): boolean {
    return this.limits().some((row) => row.scope === 'each_member');
  }

  protected labelFor(limit: RateLimit): string {
    // A per-person row bounds each member separately; "Whole use case" would read as shared.
    return limit.scope === 'each_member' ? 'Each member, individually' : 'Whole use case';
  }

  /** What the bucket allows at once — an unset burst means the per-minute figure. */
  protected effectiveBurst(limit: RateLimit): number {
    return limit.burst && limit.burst > 0 ? limit.burst : limit.limit_rpm;
  }
}
