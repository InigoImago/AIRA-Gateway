import { Component, inject, input, output } from '@angular/core';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * Whether this use case's administrators are mailed what its anomaly rules find (`FRD-409` FR-6,
 * FR-11). Off by default. The mail names the rule and what it measured, never a person, key or
 * address; the Warnings tab shows each reader what they may see.
 */
@Component({
  selector: 'app-anomaly-mail-panel',
  templateUrl: './anomaly-mail-panel.html',
})
export class AnomalyMailPanel {
  readonly slug = input.required<string>();
  readonly useCase = input<UseCase | null>(null);
  readonly canManage = input(false);
  /** Whether this installation sends mail at all (`/v1/me`). */
  readonly mailEnabled = input(false);
  readonly saved = output<UseCase>();

  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected on(): boolean {
    return this.useCase()?.mail_admins_on_anomalies === true;
  }

  protected set(on: boolean): void {
    if (!this.canManage() || this.feedback.busy() || on === this.on()) {
      return;
    }
    this.feedback.run(this.service.update(this.slug(), { mail_admins_on_anomalies: on }), {
      failure: 'Could not change the mail about anomaly findings.',
      success: (useCase: UseCase) => {
        this.saved.emit(useCase);
        this.feedback.succeed(
          on
            ? 'The administrators of this use case are now mailed what its anomaly rules find.'
            : 'No mail goes out about this use case’s anomaly findings any more.',
        );
      },
    });
  }
}
