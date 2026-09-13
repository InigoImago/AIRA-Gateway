import { Component, OnInit, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/** The longest retention period the server accepts, in days. */
const MAX_RETENTION_DAYS = 3650;

/**
 * Whether payloads are stored, for how long (`FRD-404`), and whether members see only their own
 * requests (`FRD-505` FR-4).
 *
 * One form and one save: together they answer what is kept and who may read it.
 */
@Component({
  selector: 'app-data-protection-panel',
  imports: [FormsModule],
  templateUrl: './data-protection-panel.html',
  host: { style: 'display: contents' },
})
export class DataProtectionPanel implements OnInit {
  readonly slug = input.required<string>();
  /** The loaded use case, owned by the parent. */
  readonly useCase = input<UseCase | null>(null);
  readonly canManage = input(false);
  /**
   * Whether the Overview tab is open. The panel stays on the page either way, so an unsaved
   * change survives a look at another tab.
   */
  readonly shown = input(false);
  /** Raised after a save, so the parent takes the new use case as its own. */
  readonly saved = output<UseCase>();

  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly retentionDays = signal<number | null>(null);
  protected readonly storePayloads = signal(true);
  protected readonly restrictMembers = signal(false);

  ngOnInit(): void {
    this.fill(this.useCase());
  }

  protected retentionError(): string | null {
    // With storage off there is nothing to keep, so the period is not asked for.
    if (!this.storePayloads()) return null;
    const days = this.retentionDays();
    if (days == null) return 'Set how many days payloads are kept.';
    if (!Number.isInteger(days) || days < 1 || days > MAX_RETENTION_DAYS) {
      return 'Between 1 and 3650 days.';
    }
    return null;
  }

  protected retentionChanged(): boolean {
    return (
      this.retentionDays() !== (this.useCase()?.retention_days ?? null) ||
      this.storePayloads() !== (this.useCase()?.store_payloads ?? true) ||
      this.restrictMembers() !== (this.useCase()?.restrict_members_to_own_requests ?? false)
    );
  }

  protected canSaveRetention(): boolean {
    return !this.retentionError() && this.retentionChanged() && !this.feedback.busy();
  }

  protected saveRetention(): void {
    if (!this.canSaveRetention()) {
      return;
    }
    const days = this.retentionDays();
    const store = this.storePayloads();
    this.feedback.run(
      this.service.update(this.slug(), {
        store_payloads: store,
        restrict_members_to_own_requests: this.restrictMembers(),
        ...(store && days != null ? { retention_days: days } : {}),
      }),
      {
        failure: 'Could not change the data-protection settings.',
        success: (useCase: UseCase) => {
          this.fill(useCase);
          this.saved.emit(useCase);
          this.feedback.succeed(
            store
              ? `Prompts and responses are now kept for ${days} day(s). Anything already past that is removed on the next run.`
              : 'Prompts and responses are no longer stored for this use case. Anything already stored is removed on the next run.',
          );
        },
      },
    );
  }

  private fill(useCase: UseCase | null): void {
    this.retentionDays.set(useCase?.retention_days ?? null);
    this.storePayloads.set(useCase?.store_payloads ?? true);
    this.restrictMembers.set(useCase?.restrict_members_to_own_requests ?? false);
  }
}
