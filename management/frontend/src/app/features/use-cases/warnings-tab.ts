import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, input, output, signal } from '@angular/core';
import { AnomalyEvent, Suspension } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { Live, agoLabel } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';

const REFRESH_SECONDS = 20;

/**
 * The findings about this use case, for the people who run it (`FRD-502` FR-6–8).
 *
 * The members are who would change a prompt, a limit or a client, so they see the same numbers as
 * IT Security — scoped to their use case by the server, not by this component.
 */
@Component({
  selector: 'app-warnings-tab',
  imports: [DatePipe],
  templateUrl: './warnings-tab.html',
  // On the component, so the poll ends with the panel (see `traces-tab.ts`).
  providers: [Live],
})
export class WarningsTab implements OnInit {
  readonly slug = input.required<string>();
  /** Raised when the count changes, so the parent's tab badge stays honest. */
  readonly countChanged = output<number>();

  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);
  protected readonly live = inject(Live);

  protected readonly events = signal<AnomalyEvent[]>([]);
  /** False when the gateway says this caller cannot see this use case: membership comes from the
   *  identity provider's groups (`FRD-102`), and "nothing found" would be the wrong fact. */
  protected readonly inScope = signal(true);
  protected readonly suspensions = signal<Suspension[]>([]);
  protected readonly loading = signal(true);

  /** Whether this use case is stopped right now — the fact a member most needs, first. */
  protected readonly stopped = computed(() =>
    this.suspensions().filter(
      (row) =>
        !row.lifted_at &&
        (!row.expires_at || new Date(row.expires_at).getTime() > Date.now()) &&
        (row.use_case === this.slug() || row.target_value === this.slug()),
    ),
  );

  ngOnInit(): void {
    this.live.start(
      REFRESH_SECONDS,
      () => this.service.anomalies(100, this.slug()),
      (page) => {
        this.events.set(page.events);
        this.countChanged.emit(page.events.length);
        this.inScope.set(page.in_scope !== false);
        this.loading.set(false);
      },
    );
    // Listing suspensions needs an incident role. A member's 403 is a real answer: no banner.
    this.service.suspensions().subscribe({
      next: (page) => this.suspensions.set(page.suspensions),
      error: () => undefined,
    });
  }

  protected ago(): string {
    return agoLabel(this.live.lastUpdated());
  }
}
