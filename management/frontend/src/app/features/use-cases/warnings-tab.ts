import {
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { Observable, Subscription, catchError, forkJoin, map, of } from 'rxjs';
import { AnomalyEvent, AnomalyPage, Suspension } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { Live } from '../../core/ui/live';
import { PageFeedback } from '../../core/ui/page-feedback';
import { WITHHELD_REASON, targetAtStart, targetName } from '../../core/ui/withheld';
import { ZonedPipe } from '../../core/ui/zoned.pipe';

const REFRESH_SECONDS = 20;

/** Findings per page. The listing is cursor-paged and all-time, so "load older" fetches the rest. */
export const FINDINGS_PAGE = 50;
/**
 * How far back a finding counts towards the tab's alert badge. A red badge is a call to look now;
 * a finding from last month is history, which the tab lists and the badge does not shout about.
 */
export const RECENT_HOURS = 24;

/** The alert badge's figure: findings of the last {@link RECENT_HOURS}, and whether there may be
 *  more than one page holds. */
export interface RecentFindings {
  count: number;
  more: boolean;
}

/**
 * Count the recent findings on one page, newest first. When every finding on a full page is recent
 * and the server has older ones, the page does not hold them all — said as "50+", never as 50.
 */
export function recentFindings(page: AnomalyPage, now: Date): RecentFindings {
  const since = now.getTime() - RECENT_HOURS * 60 * 60 * 1000;
  const count = page.events.filter((event) => new Date(event.created_at).getTime() >= since).length;
  return { count, more: count === page.events.length && page.next_cursor !== null };
}

/** What a poll brings back: the findings, and the stops — or why the stops could not be read. */
interface Poll {
  page: AnomalyPage;
  stops: { rows: Suspension[] } | { failure: unknown };
}

/**
 * The findings about this use case, for the people who run it (`FRD-502` FR-6–8).
 *
 * The members are who would change a prompt, a limit or a client, so they see the same numbers as
 * IT Security — scoped to their use case by the server, not by this component.
 */
@Component({
  selector: 'app-warnings-tab',
  imports: [ZonedPipe],
  templateUrl: './warnings-tab.html',
  // On the component, so the poll ends with the panel (see `traces-tab.ts`).
  providers: [Live],
})
export class WarningsTab implements OnInit {
  readonly slug = input.required<string>();
  /** Raised with each fresh first page, so the parent's tab badge stays honest. */
  readonly countChanged = output<RecentFindings>();

  private readonly service = inject(UseCaseService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly feedback = inject(PageFeedback);
  protected readonly live = inject(Live);

  protected readonly events = signal<AnomalyEvent[]>([]);
  /** Set when there are older findings than the ones on screen — cursor, not offset. */
  protected readonly cursor = signal<string | null>(null);
  protected readonly loadingMore = signal(false);
  /** False when the gateway says this caller cannot see this use case: membership comes from the
   *  identity provider's groups (`FRD-102`), and "nothing found" would be the wrong fact. */
  protected readonly inScope = signal(true);
  protected readonly suspensions = signal<Suspension[]>([]);
  /** The stops could not be read, so "nothing is stopped" would be a claim nobody checked. */
  protected readonly stopsUnknown = signal(false);
  protected readonly loading = signal(true);

  /**
   * What is stopped here right now — the fact a member most needs, first. The server has already
   * narrowed the list to this use case, and for a member to what applies to them. Measured against
   * the live clock, so a stop that runs out while the tab is open stops being shown as in force.
   */
  protected readonly stopped = computed(() => {
    const now = this.live.now().getTime();
    return this.suspensions().filter(
      (row) => !row.lifted_at && (!row.expires_at || new Date(row.expires_at).getTime() > now),
    );
  });

  private olderRequest: Subscription | null = null;
  /** The page banner's message for a failed poll, withdrawn when a later poll answers. */
  private pollFailure: string | null = null;
  private stopsFailure: string | null = null;

  /** Whom a stop is on, in words: the use case itself, a person, or one API key. */
  protected stoppedWhat(row: Suspension): string {
    if (row.target === 'use_case') return 'This use case is stopped.';
    // Strict data mode withholds whom: "A person is stopped here", never a blank name.
    if (row.target_withheld) return `${targetAtStart(row)} is stopped here.`;
    if (row.target === 'credential') return `API key ${row.target_value} is stopped here.`;
    return `${row.target_value} is stopped here.`;
  }

  protected readonly targetName = targetName;
  protected readonly withheldReason = WITHHELD_REASON;

  ngOnInit(): void {
    this.destroyRef.onDestroy(() => this.olderRequest?.unsubscribe());
    // The stops are read in the same poll as the findings: one read once at opening kept showing a
    // stop that had been lifted, and never showed one imposed while the tab was open.
    this.live.start(
      REFRESH_SECONDS,
      () => this.poll(),
      ({ page, stops }) => {
        this.events.set(page.events);
        this.cursor.set(page.next_cursor);
        this.countChanged.emit(recentFindings(page, new Date()));
        this.inScope.set(page.in_scope !== false);
        this.loading.set(false);
        this.pollFailure = this.withdraw(this.pollFailure);
        this.takeStops(stops);
      },
      (response) => {
        this.loading.set(false);
        this.pollFailure = this.feedback.fail(response, 'Could not load the warnings.');
      },
    );
  }

  /** Fetch the next page of findings and append it. Live is paused while paging, which also drops
   *  a refresh in flight: one landing now would replace what is appended. */
  protected loadOlder(): void {
    const next = this.cursor();
    if (!next || this.loadingMore()) return;
    this.live.pause();
    this.loadingMore.set(true);
    this.olderRequest?.unsubscribe();
    this.olderRequest = this.service.anomalies(FINDINGS_PAGE, this.slug(), next).subscribe({
      next: (page) => {
        this.events.update((rows) => [...rows, ...page.events]);
        this.cursor.set(page.next_cursor);
        this.loadingMore.set(false);
      },
      error: (response: unknown) => {
        this.loadingMore.set(false);
        this.feedback.fail(response, 'Could not load older warnings.');
      },
    });
  }

  private poll(): Observable<Poll> {
    // Asked for this use case, which a member may: whether it, or they, are stopped (`FRD-503`).
    // Its failure is caught here so that it costs the stops and not the findings beside them.
    const stops = this.service.suspensions(this.slug()).pipe(
      map((page): Poll['stops'] => ({ rows: page.suspensions })),
      catchError((failure: unknown) => of<Poll['stops']>({ failure })),
    );
    return forkJoin({ page: this.service.anomalies(FINDINGS_PAGE, this.slug()), stops });
  }

  private takeStops(stops: Poll['stops']): void {
    if ('rows' in stops) {
      this.suspensions.set(stops.rows);
      this.stopsUnknown.set(false);
      this.stopsFailure = this.withdraw(this.stopsFailure);
      return;
    }
    // A 403 is an answer — this caller may not list stops here — not a failure to report.
    if ((stops.failure as { status?: number } | null)?.status === 403) {
      this.suspensions.set([]);
      this.stopsUnknown.set(false);
      return;
    }
    this.suspensions.set([]);
    this.stopsUnknown.set(true);
    this.stopsFailure = this.feedback.fail(
      stops.failure,
      'Could not check whether anything is stopped here.',
    );
  }

  private withdraw(message: string | null): null {
    if (message) this.feedback.withdraw(message);
    return null;
  }
}
