import { Component, computed, inject, input, linkedSignal, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/** How many thresholds a use case may name: the server's limit (`aira_common.mail`). */
export const MAX_THRESHOLDS = 5;

/** What a use case mails at when nobody set anything: the moment a budget is used up. */
export const DEFAULT_THRESHOLDS: readonly number[] = [100];

/**
 * Thresholds as an administrator types them — `80, 100`, with or without `%` — or what is wrong
 * with them, in the server's words. Checked before anything is sent, so Save never produces a 400.
 */
export function parseThresholds(typed: string): { thresholds: number[] } | { error: string } {
  const parts = typed
    .split(/[\s,;]+/)
    .map((part) => part.replace(/%$/, ''))
    .filter((part) => part !== '');
  const seen = new Set<number>();
  for (const part of parts) {
    const share = /^\d+$/.test(part) ? Number(part) : NaN;
    if (!Number.isInteger(share) || share < 1 || share > 100) {
      return { error: 'Each threshold is a whole percentage from 1 to 100.' };
    }
    if (seen.has(share)) {
      return { error: `${share} % is named twice.` };
    }
    seen.add(share);
  }
  if (seen.size > MAX_THRESHOLDS) {
    return { error: `At most ${MAX_THRESHOLDS} thresholds.` };
  }
  return { thresholds: [...seen].sort((a, b) => a - b) };
}

/** `[80, 100]` as a sentence says it: "80% and 100%". */
export function thresholdsText(thresholds: readonly number[]): string {
  const shares = thresholds.map((share) => `${share}%`);
  return shares.length < 2
    ? (shares[0] ?? '')
    : `${shares.slice(0, -1).join(', ')} and ${shares[shares.length - 1]}`;
}

/** What the use case mails at now: the stored list, or the default where it never said. */
function storedThresholds(useCase: UseCase | null): readonly number[] {
  return useCase?.budget_mail_thresholds ?? DEFAULT_THRESHOLDS;
}

/**
 * At which shares of a budget a mail goes out (`FRD-409` FR-1, FR-11): the person about their own
 * allowance, the administrators about the use case's shared budget.
 *
 * A child of the budgets tab's page, owning its form; the page holds the use case and takes the
 * saved one back. It says so plainly when the installation sends no mail at all, because a setting
 * that does nothing must not look as though it works.
 */
@Component({
  selector: 'app-budget-mail-panel',
  imports: [FormsModule],
  templateUrl: './budget-mail-panel.html',
})
export class BudgetMailPanel {
  readonly slug = input.required<string>();
  readonly useCase = input<UseCase | null>(null);
  readonly canManage = input(false);
  /** Whether this installation sends mail at all (`/v1/me`). */
  readonly mailEnabled = input(false);
  readonly saved = output<UseCase>();

  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly current = computed(() => storedThresholds(this.useCase()));
  /** The field, refilled whenever the saved use case changes. */
  protected readonly typed = linkedSignal(() => this.current().join(', '));
  protected readonly parsed = computed(() => parseThresholds(this.typed()));
  protected readonly error = computed(() => {
    const parsed = this.parsed();
    return 'error' in parsed ? parsed.error : null;
  });
  protected readonly changed = computed(() => {
    const parsed = this.parsed();
    return 'thresholds' in parsed && parsed.thresholds.join(',') !== this.current().join(',');
  });
  protected readonly currentText = computed(() => thresholdsText(this.current()));

  protected canSave(): boolean {
    return this.canManage() && this.changed() && !this.feedback.busy();
  }

  protected save(): void {
    const parsed = this.parsed();
    if (!this.canSave() || !('thresholds' in parsed)) {
      return;
    }
    const thresholds = parsed.thresholds;
    this.feedback.run(this.service.update(this.slug(), { budget_mail_thresholds: thresholds }), {
      failure: 'Could not change when budget mail goes out.',
      success: (useCase: UseCase) => {
        this.saved.emit(useCase);
        this.feedback.succeed(
          thresholds.length
            ? `A mail now goes out at ${thresholdsText(thresholds)} of a budget.`
            : 'No mail goes out about this use case’s budgets any more.',
        );
      },
    });
  }
}
