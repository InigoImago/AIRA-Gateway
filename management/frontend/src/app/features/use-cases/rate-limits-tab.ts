import { Component, inject, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { signal } from '@angular/core';
import { Membership, RateLimit } from '../../core/api/models';
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
  /**
   * The people this use case already has, for the "one named person" field.
   *
   * A **suggestion, never a restriction**, and that distinction is one this project has already
   * paid for: a rule names a *subject*, and access can come through a Keycloak group, so a
   * group-granted service account belongs to **no membership row at all** (`FRD-209`). A picker
   * over this list is therefore narrower than the rule it fills in — `FRD-604` recorded the same
   * conclusion for a key's owner and typed it rather than picking. So the list assists and the
   * field still accepts anything.
   */
  readonly members = input<Membership[]>([]);
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
  protected readonly rlSubject = signal('');
  protected readonly rlRpm = signal<number | null>(null);
  protected readonly rlBurst = signal<number | null>(null);

  protected validationError(): string | null {
    if (this.rlScope() === 'member' && !this.rlSubject().trim()) {
      return 'A member limit needs a username.';
    }
    const rpm = this.rlRpm();
    if (rpm == null) return 'Set how many requests per minute are allowed.';
    if (!Number.isInteger(rpm) || rpm < 1) return 'At least 1 request per minute.';
    const burst = this.rlBurst();
    if (burst != null && (!Number.isInteger(burst) || burst < 1)) {
      return 'A burst must be at least 1, or left empty.';
    }
    return null;
  }

  /**
   * A name no member of this use case carries, typed into a rule that names one.
   *
   * **The field accepts anything on purpose** — a rule names a *subject*, access can come through a
   * Keycloak group, and somebody granted that way belongs to no membership row (`FRD-209`). But a
   * free field means a typo produces a rule that binds **nobody**, saves cleanly, and appears in
   * the list looking exactly like a working one. That is this project's most repeated defect
   * dressed as a feature: a control that is configured, displayed as active, and applies to
   * nothing.
   *
   * So it is neither refused nor accepted silently. The console says what it **knows** — that no
   * member of this use case has that name — and is careful to say *knows*, because who is in a
   * group is the identity provider's answer, not ours. The same wording the access panel uses for
   * a grant that reaches nobody.
   */
  protected knownMember(subject: string): boolean {
    const name = subject.trim().toLowerCase();
    return this.members().some((member) => member.username.trim().toLowerCase() === name);
  }

  protected unmatchedSubject(): boolean {
    return (
      this.rlScope() === 'member' &&
      this.rlSubject().trim().length > 0 &&
      !this.knownMember(this.rlSubject())
    );
  }

  /** True for a saved rule that names somebody this use case has no member row for. */
  protected reachesNobodyKnown(scope: string, subject: string | null | undefined): boolean {
    return scope === 'member' && !!subject && !this.knownMember(subject);
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
      subject: this.rlScope() === 'member' ? this.rlSubject().trim() : '',
      limit_rpm: this.rlRpm() ?? 0,
      burst: this.rlBurst() ?? 0,
    };
    this.feedback.run(this.service.createRateLimit(this.slug(), limit), {
      failure: 'Could not save the rate limit.',
      success: () => {
        this.feedback.succeed('Rate limit saved.');
        this.rlSubject.set('');
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
    if (limit.scope === 'member') {
      return limit.subject || 'member';
    }
    // Not "Whole use case" — a per-person row is the opposite of a shared one, and the row would
    // read as a limit forty people share while it bounds each of them separately.
    return limit.scope === 'each_member' ? 'Each member, individually' : 'Whole use case';
  }

  /** What the bucket actually allows at once — an unset burst means the per-minute figure. */
  protected effectiveBurst(limit: RateLimit): number {
    return limit.burst && limit.burst > 0 ? limit.burst : limit.limit_rpm;
  }
}
