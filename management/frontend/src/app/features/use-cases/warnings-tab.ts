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

  /** What is stopped here right now — the fact a member most needs, first. The server has already
   *  narrowed the list to this use case, and for a member to what applies to them. */
  protected readonly stopped = computed(() =>
    this.suspensions().filter(
      (row) =>
        !row.lifted_at && (!row.expires_at || new Date(row.expires_at).getTime() > Date.now()),
    ),
  );

  /** Whom a stop is on, in words: the use case itself, a person, or one API key. */
  protected stoppedWhat(row: Suspension): string {
    if (row.target === 'use_case') return 'This use case is stopped.';
    if (row.target === 'credential') return `API key ${row.target_value} is stopped here.`;
    return `${row.target_value} is stopped here.`;
  }

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
    // Asked for this use case, which a member may: whether it, or they, are stopped (`FRD-503`).
    // A caller the gateway scopes out gets an empty list, and the findings say why.
    this.service.suspensions(this.slug()).subscribe({
      next: (page) => this.suspensions.set(page.suspensions),
      error: () => undefined,
    });
  }

  protected ago(): string {
    return agoLabel(this.live.lastUpdated());
  }
}
