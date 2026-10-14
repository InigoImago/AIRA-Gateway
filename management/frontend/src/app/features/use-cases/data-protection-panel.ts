import { Component, OnInit, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/** The longest retention period the server accepts, in days. */
const MAX_RETENTION_DAYS = 3650;

/**
 * What happens to prompts and responses (`FRD-622` FR-1). Four modes over the two fields the
 * gateway enforces: `store_payloads` — whether anything is written — and `payload_visibility`,
 * which is who may read it (`FRD-505` FR-4).
 */
export type ContentMode = 'not-stored' | 'oversight' | 'admins-and-own' | 'members';

/** The modes as offered, in order, with what each means for the people in the use case. */
export const CONTENT_MODES: readonly { value: ContentMode; label: string; explains: string }[] = [
  {
    value: 'not-stored',
    label: 'Not stored',
    explains:
      'Nothing a caller sends or receives is written. Who, when, which model and what it cost ' +
      'are still kept.',
  },
  {
    value: 'oversight',
    label: 'Oversight roles only',
    explains:
      'Kept for oversight and read by nobody in this use case — not by its administrators ' +
      'either. Only a role that may read any content can open a request — Global Administrators ' +
      'and IT Security, and any role given that permission — and every such read is recorded. ' +
      'The use case’s totals stay visible here; figures per person show each reader their own.',
  },
  {
    value: 'admins-and-own',
    label: "Administrators, and each person's own requests",
    explains:
      'Administrators of this use case read every request. Everybody else sees only the requests ' +
      'they made themselves — not even the rows of anybody else’s — and only their own figures.',
  },
  {
    value: 'members',
    label: 'Every member of the use case',
    explains: "Everybody in the use case sees the whole use case's requests.",
  },
];

/** The mode a use case is in. Not stored wins: with nothing written, who may read it is moot. */
export function modeOf(
  useCase: Pick<UseCase, 'store_payloads' | 'payload_visibility'> | null,
): ContentMode {
  if (useCase?.store_payloads === false) return 'not-stored';
  if (useCase?.payload_visibility === 'oversight') return 'oversight';
  return useCase?.payload_visibility === 'own' ? 'admins-and-own' : 'members';
}

/**
 * The fields a mode is saved as. Not stored leaves the visibility as it is: it decides nothing
 * while nothing is written, and the next stored mode sets it explicitly.
 */
export function fieldsFor(mode: ContentMode): {
  store_payloads: boolean;
  payload_visibility?: UseCase['payload_visibility'];
} {
  if (mode === 'not-stored') return { store_payloads: false };
  if (mode === 'oversight') return { store_payloads: true, payload_visibility: 'oversight' };
  return {
    store_payloads: true,
    payload_visibility: mode === 'admins-and-own' ? 'own' : 'members',
  };
}

/**
 * Whether payloads are stored, for how long (`FRD-404`), and who inside the use case may read them
 * (`FRD-622` FR-1). One form and one save: together they answer what is kept and who may read it.
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
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly modes = CONTENT_MODES;
  /** This panel's save is in flight — its own, so the form beside it keeps its label. */
  protected readonly saving = signal(false);
  protected readonly retentionDays = signal<number | null>(null);
  protected readonly mode = signal<ContentMode>('members');

  ngOnInit(): void {
    this.fill(this.useCase());
  }

  /** Whether the chosen mode writes anything, which is when a retention period is asked for. */
  protected stored(): boolean {
    return this.mode() !== 'not-stored';
  }

  /** The saved mode in words, for a reader who may not change it. */
  protected currentLabel(): string {
    const current = modeOf(this.useCase());
    return CONTENT_MODES.find((option) => option.value === current)?.label ?? '';
  }

  protected retentionError(): string | null {
    // With storage off there is nothing to keep, so the period is not asked for.
    if (!this.stored()) return null;
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
      this.mode() !== modeOf(this.useCase())
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
    const fields = fieldsFor(this.mode());
    const deletes = this.whatIsDeleted(fields.store_payloads, days);
    if (deletes && !this.confirmService.ask(deletes)) {
      return;
    }
    this.saving.set(true);
    this.feedback.run(
      this.service
        .update(this.slug(), {
          // Named, not spread: every field this form writes is visible where it is sent. Not stored
          // leaves the restriction `undefined`, which is not sent at all.
          store_payloads: fields.store_payloads,
          payload_visibility: fields.payload_visibility,
          ...(fields.store_payloads && days != null ? { retention_days: days } : {}),
        })
        .pipe(finalize(() => this.saving.set(false))),
      {
        failure: 'Could not change the data-protection settings.',
        success: (useCase: UseCase) => {
          this.fill(useCase);
          this.saved.emit(useCase);
          this.feedback.succeed(
            fields.store_payloads
              ? `Prompts and responses are now kept for ${days} day(s). Anything already past that is removed on the next run.`
              : 'Prompts and responses are no longer stored for this use case. Anything already stored is removed on the next run.',
          );
        },
      },
    );
  }

  /**
   * The question to ask before saving, when the save deletes stored content: switching storage
   * off, or shortening the period. Deleting is the one thing here that cannot be undone, so it is
   * asked before — as revoking a key or removing a budget is — not only announced after.
   */
  private whatIsDeleted(store: boolean, days: number | null): string | null {
    const saved = this.useCase();
    if (saved?.store_payloads === false) return null;
    if (!store) {
      return (
        'Stop storing prompts and responses for this use case? Everything already stored for it ' +
        'is deleted on the next retention run, and cannot be brought back.'
      );
    }
    const before = saved?.retention_days ?? null;
    if (days != null && before != null && days < before) {
      return (
        `Keep prompts and responses for ${days} day(s) instead of ${before}? Everything stored ` +
        `longer ago than that is deleted on the next retention run, and cannot be brought back.`
      );
    }
    return null;
  }

  private fill(useCase: UseCase | null): void {
    this.retentionDays.set(useCase?.retention_days ?? null);
    this.mode.set(modeOf(useCase));
  }
}
