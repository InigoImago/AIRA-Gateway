import { Component, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * What this use case is for, and how its data is handled.
 *
 * Both fields have existed since the use case did: the API accepts them, they travel to the
 * gateway's read-model on the config event, and the overview **prints them** — *"No description."*
 * where there is none, and a *Processing:* line where there is. No screen ever offered a way to
 * write either, so every installation's overview said "No description." forever and the processing
 * line was reachable only through the API or a seed.
 *
 * The same shape as the KIRA id: displayed, unsettable. Unlike `addressing` on a model — which
 * nothing reads and is therefore off the panel — these two exist **to be read by people**, which
 * is an argument for the control rather than against the display. `processing_notes` in particular
 * is the sentence somebody writes for a data-protection review, and a governance record nobody can
 * author is a governance record nobody has.
 *
 * Its own panel rather than a form in the parent: the page is a parent plus panels, the parent owns
 * loading and the tab bar, and a child owns its form state and its mutation.
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

  protected readonly description = signal('');
  protected readonly processingNotes = signal('');

  /** Whether the form has been touched since it was last filled from the server. Without it, the
   *  effect below would overwrite what somebody is typing every time the parent reloads. */
  private readonly touched = signal(false);

  constructor() {
    effect(() => {
      const loaded = this.useCase();
      if (!loaded || this.touched()) return;
      this.description.set(loaded.description ?? '');
      this.processingNotes.set(loaded.processing_notes ?? '');
    });
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
          this.saved.emit(useCase);
          this.feedback.succeed('Saved. The overview and the gateway both read this.');
        },
      },
    );
  }
}
