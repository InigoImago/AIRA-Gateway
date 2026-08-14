import { Component, inject, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { signal } from '@angular/core';
import { RateLimit } from '../../core/api/models';
import { LimitScope } from '../../core/api/models';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * The rate-limit panel of a use case (FRD-405).
 *
 * The list is an input rather than something this panel fetches: the tab bar shows a count for
 * every tab, so the parent has to know all of them before any tab is opened. What lives here is
 * the form state, the validation and the mutations — the parts that only matter while this tab
 * is the one on screen, and the parts that made the parent component grow past reading.
 *
 * Outcomes are reported through {@link PageFeedback}, which the parent provides, so the page
 * keeps showing one banner rather than one per panel.
 */
@Component({
  selector: 'app-rate-limits-tab',
  imports: [FormsModule, InfoHint, Modal],
  templateUrl: './rate-limits-tab.html',
})
export class RateLimitsTab {
  readonly slug = input.required<string>();
  readonly limits = input.required<RateLimit[]>();
  /** Whether this caller may change anything here. Told by the page, which was told by the
   * server — an object-level permission is not in the token, and a panel that assumes yes offers
   * buttons that answer 403. */
  readonly canManage = input(false);
  /** Raised after a change lands, so the parent can reload what it owns. */
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  // Form state is signals: the app is zoneless, so a plain property changed from code — the
  // reset after a successful save, the field that appears when the scope changes — would
  // schedule no re-render at all (FRD-203 §4).
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
      // No scope names a person any more (2026-08-14); the field is kept on the wire while
      // both planes' migrations run and is ignored.
      subject: '',
      limit_rpm: this.rlRpm() ?? 0,
      burst: this.rlBurst() ?? 0,
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

  protected labelFor(limit: RateLimit): string {
    // Not "Whole use case" — a per-person row is the opposite of a shared one, and the row would
    // read as a limit forty people share while it bounds each of them separately.
    return limit.scope === 'each_member' ? 'Each member, individually' : 'Whole use case';
  }

  /** What the bucket actually allows at once — an unset burst means the per-minute figure. */
  protected effectiveBurst(limit: RateLimit): number {
    return limit.burst && limit.burst > 0 ? limit.burst : limit.limit_rpm;
  }
}
