import { Component, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * What this use case is for, and how its data is handled (`FRD-206`).
 *
 * Both fields are printed on the overview and carried to the gateway, so they need a way to be
 * written: `processing_notes` is the sentence a data-protection review asks for, and a governance
 * record nobody can author is one nobody has.
 */
@Component({
  selector: 'app-about-panel',
  imports: [FormsModule],
  templateUrl: './about-panel.html',
})
export class AboutPanel {
  readonly slug = input.required<string>();
  readonly canManage = input(false);
  /** The loaded use case, owned by the parent. */
  readonly useCase = input<UseCase | null>(null);
  /** Raised after a save, so the parent takes the new use case as its own. */
  readonly saved = output<UseCase>();

  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  /** Whether the two fields show as text or as inputs. Text until somebody asks. */
  protected readonly editing = signal(false);

  protected readonly description = signal('');
  protected readonly processingNotes = signal('');

  /** Touched since last filled from the server — so a parent reload does not overwrite typing. */
  private readonly touched = signal(false);

  constructor() {
    effect(() => {
      const loaded = this.useCase();
      if (!loaded || this.touched()) return;
      this.description.set(loaded.description ?? '');
      this.processingNotes.set(loaded.processing_notes ?? '');
    });
  }

  protected startEditing(): void {
    this.editing.set(true);
  }

  /**
   * Leave the form and forget what was typed, restoring the parent's values: a kept draft would
   * make the read view show text that is not stored.
   */
  protected cancelEditing(): void {
    this.editing.set(false);
    this.touched.set(false);
    const loaded = this.useCase();
    this.description.set(loaded?.description ?? '');
    this.processingNotes.set(loaded?.processing_notes ?? '');
  }

  protected edit(field: 'description' | 'processingNotes', value: string): void {
    this.touched.set(true);
    (field === 'description' ? this.description : this.processingNotes).set(value);
  }

  protected save(): void {
    if (!this.canManage() || this.feedback.busy()) return;
    this.feedback.run(
      this.service.update(this.slug(), {
        description: this.description().trim(),
        processing_notes: this.processingNotes().trim(),
      }),
      {
        failure: 'Could not change what this use case says about itself.',
        success: (useCase: UseCase) => {
          this.touched.set(false);
          this.editing.set(false);
          this.saved.emit(useCase);
          this.feedback.succeed('Saved. The overview and the gateway both read this.');
        },
      },
    );
  }
}
