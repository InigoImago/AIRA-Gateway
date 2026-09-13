import { Component, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { errorMessage } from '../../core/api/error-message';
import { DryRunResult, PipelineConfig, PipelineStep } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { actionClass, notReachedOf, previewOf, traceCardsOf } from './pipeline-steps';

/**
 * The builder's test panel: a sample prompt, the live preview, and a dry run through the gateway.
 *
 * It sits outside the builder's read-only guard: running the pipeline changes nothing, so a reader
 * who may not edit it may still try it.
 */
@Component({
  selector: 'app-pipeline-test-panel',
  imports: [FormsModule],
  templateUrl: './pipeline-test-panel.html',
  styleUrl: './pipeline-test-panel.scss',
})
export class PipelineTestPanel {
  private readonly service = inject(UseCaseService);

  readonly slug = input.required<string>();
  /** The pipeline as it stands on screen, saved or not. */
  readonly config = input.required<PipelineConfig>();
  /** The models released to this use case (`FRD-308`); the only ones a dry run may enter at. */
  readonly released = input<string[]>([]);
  /**
   * Whether the gateway would accept this reader's token for this use case — what a dry run needs,
   * and **not** the console's membership answer. A missing answer means "no opinion", not "no".
   */
  readonly mayCall = input(true);

  protected readonly nothingReleased = computed(() => this.released().length === 0);
  protected readonly actionClass = actionClass;

  protected readonly sampleSystem = signal('');
  protected readonly sampleUser = signal('');
  protected readonly dryRunning = signal(false);
  protected readonly dryRun = signal<DryRunResult | null>(null);
  protected readonly dryRunError = signal<string | null>(null);
  /**
   * Keep evaluating after a step refuses. Off by default: the default answer has to be the one
   * production would give, and each step run past a block spends real tokens.
   */
  protected readonly pastBlocks = signal(false);
  /**
   * The model a dry run **enters the pipeline at** — chosen, not inferred: the gateway's inference
   * is reported back as `effective_model`, which reads as a decision somebody made. Blank lets the
   * gateway choose.
   */
  protected readonly dryRunModel = signal('');

  /** What the displayed trace was about; any change to the pipeline or the sample makes it stale. */
  private readonly dryRunOf = signal('');
  /**
   * What the last **failed** attempt was about. Held apart from `dryRunOf`: one signal for both
   * would let a failed run stamp the new configuration onto the old trace and mark it fresh.
   */
  private readonly dryRunErrorOf = signal('');
  /** The steps as they were when the run was made, so cards are never labelled from later edits. */
  private readonly dryRunSteps = signal<PipelineStep[]>([]);

  protected readonly dryRunStale = computed(
    () => this.dryRun() !== null && this.dryRunOf() !== this.dryRunSubject(),
  );
  /** The error, while it is still about what is on screen. */
  protected readonly currentError = computed(() =>
    this.dryRunErrorOf() === this.dryRunSubject() ? this.dryRunError() : null,
  );
  protected readonly traceCards = computed(() => traceCardsOf(this.dryRun()));
  protected readonly notReached = computed(() => notReachedOf(this.dryRun(), this.dryRunSteps()));
  protected readonly preview = computed(() =>
    previewOf(this.config().steps, this.sampleSystem(), this.sampleUser()),
  );

  /** A run is about the pipeline *and* its input, so both sample texts and the options count. */
  private dryRunSubject(): string {
    return JSON.stringify([
      this.config(),
      this.sampleSystem(),
      this.sampleUser(),
      this.pastBlocks(),
    ]);
  }

  protected runDryRun(): void {
    // The previous result stays on screen while the next one runs: clearing it collapses the panel
    // and the page jumps, which reads as a reload.
    this.dryRunError.set(null);
    this.dryRunning.set(true);
    // Resolved now: the reader can keep editing while it runs, and the answer is about what was sent.
    const subject = this.dryRunSubject();
    const steps = this.config().steps;
    this.service
      .dryRunPipeline({
        // A dry run spends real tokens: the gateway charges this use case and refuses a model it
        // may not call (`FRD-308`).
        use_case: this.slug(),
        system: this.sampleSystem(),
        user: this.sampleUser(),
        // Omitted rather than empty when nobody chose: an empty string is a model name to refuse.
        ...(this.dryRunModel() ? { model: this.dryRunModel() } : {}),
        pipeline: this.config(),
        past_blocks: this.pastBlocks(),
      })
      .subscribe({
        next: (result) => {
          this.dryRun.set(result);
          this.dryRunSteps.set(steps);
          this.dryRunOf.set(subject);
          this.dryRunning.set(false);
        },
        error: (response: { status?: number }) => {
          this.dryRunning.set(false);
          this.dryRunErrorOf.set(subject);
          this.dryRunError.set(
            // 403 and 401 are different problems. A dry run follows the membership rule a request
            // does (`ADR-0007`), and a Global Administrator is a member of nothing — so a 403 must
            // not send them to OIDC configuration that is working.
            response?.status === 403
              ? 'Dry-run refused — a dry run calls a real model and is charged to this use case, so it follows the same membership rule a request does. Grant yourself access to this use case to test its pipeline.'
              : response?.status === 401
                ? 'Dry-run rejected — the gateway did not accept your login. Enable OIDC on the gateway (AIRA_OIDC_ENABLED) so it can verify the same Keycloak token.'
                : errorMessage(
                    response,
                    'Dry-run failed — is the gateway running and reachable on /gw?',
                  ),
          );
        },
      });
  }
}
