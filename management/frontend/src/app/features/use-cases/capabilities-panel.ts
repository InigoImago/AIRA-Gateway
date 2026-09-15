import { Component, OnInit, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * What this use case may do: function calling (`FRD-131`), reasoning (`FRD-135`) and prompt
 * caching with its lifetime (`FRD-133`).
 *
 * A form and a save of its own, apart from data protection: the two answer different questions,
 * and one form made a caching change report success in a sentence about retention.
 */
@Component({
  selector: 'app-capabilities-panel',
  imports: [FormsModule, InfoHint],
  templateUrl: './capabilities-panel.html',
  host: { style: 'display: contents' },
})
export class CapabilitiesPanel implements OnInit {
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

  protected readonly toolsEnabled = signal(false);
  /** On unless an administrator turns it off (`FRD-135`). */
  protected readonly includeReasoning = signal(true);
  protected readonly promptCaching = signal(false);
  protected readonly cacheTtl = signal('5m');

  ngOnInit(): void {
    this.fill(this.useCase());
  }

  protected capabilitiesChanged(): boolean {
    return (
      this.toolsEnabled() !== (this.useCase()?.tools_enabled ?? false) ||
      this.includeReasoning() !== (this.useCase()?.include_reasoning ?? true) ||
      this.promptCaching() !== (this.useCase()?.prompt_caching_enabled ?? false) ||
      this.cacheTtl() !== (this.useCase()?.prompt_cache_ttl ?? '5m')
    );
  }

  protected canSaveCapabilities(): boolean {
    return this.capabilitiesChanged() && !this.feedback.busy();
  }

  protected saveCapabilities(): void {
    if (!this.canSaveCapabilities()) {
      return;
    }
    const caching = this.promptCaching();
    const ttl = this.cacheTtl();
    this.feedback.run(
      this.service.update(this.slug(), {
        tools_enabled: this.toolsEnabled(),
        include_reasoning: this.includeReasoning(),
        prompt_caching_enabled: caching,
        prompt_cache_ttl: ttl,
      }),
      {
        failure: 'Could not change what this use case may do.',
        success: (useCase: UseCase) => {
          this.fill(useCase);
          this.saved.emit(useCase);
          // Says what changed and where the consequence shows up (`FRD-133` FR-10).
          this.feedback.succeed(
            caching
              ? `Caching is on, keeping the prefix for ${ttl === '1h' ? 'an hour' : 'five minutes'}. The Cached share on this page shows how much of the input it catches.`
              : 'Saved. Prompt caching is off, so every request is charged in full.',
          );
        },
      },
    );
  }

  /**
   * A field the server omits reads as its default. Function calling and caching read as off and the
   * cheap lifetime, because absence is not permission; reasoning reads as on, the use-case default.
   */
  private fill(useCase: UseCase | null): void {
    this.toolsEnabled.set(useCase?.tools_enabled ?? false);
    this.includeReasoning.set(useCase?.include_reasoning ?? true);
    this.promptCaching.set(useCase?.prompt_caching_enabled ?? false);
    this.cacheTtl.set(useCase?.prompt_cache_ttl ?? '5m');
  }
}
