import { DatePipe } from '@angular/common';
import { Component, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Suspension } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';
import { TablePager } from '../../core/ui/table-pager';
import { TableView } from '../../core/ui/table-view';
import { WITHHELD_REASON, targetAtStart, targetName } from '../../core/ui/withheld';

/** Everything about a suspension a person might type to find it. */
function haystack(row: Suspension): string {
  return [row.target, targetName(row), row.author, row.reason].join(' ');
}

/** What a stop reaches — "subject · uc-a", or "· everywhere" for one that is not scoped. */
export function reachOf(row: Suspension): string {
  return `${row.target} · ${row.use_case ?? 'everywhere'}`;
}

/**
 * What is stopped, what was, and the kill switch (`FRD-503`).
 *
 * The page loads the suspensions, because its banner and tab count need them before this tab is
 * opened. This panel owns the form, the search and the two mutations, reports through the page's
 * `PageFeedback`, and raises `changed` so the page reloads.
 */
@Component({
  selector: 'app-suspensions-panel',
  imports: [DatePipe, FormsModule, InfoHint, TablePager],
  templateUrl: './suspensions-panel.html',
  host: { style: 'display: contents' },
})
export class SuspensionsPanel {
  /**
   * Whether this panel's tab is the open one. The panel stays on the page either way, so a
   * half-typed form survives a look at another tab.
   */
  readonly shown = input(false);
  /** In force now: neither lifted nor expired. */
  readonly active = input<Suspension[]>([]);
  /** Lifted or expired — kept, because a review asks who stopped what, when and why. */
  readonly past = input<Suspension[]>([]);
  /** Whether this caller may stop and restore traffic (`incident.suspend`), not only see it. */
  readonly canStop = input(false);
  /** The gateway would not list them to this caller (a `403`): say so instead of "nothing". */
  readonly hidden = input(false);
  /** False when `/me` could not be read, so who may stop traffic is unknown for this reader. */
  readonly permissionsKnown = input(true);
  /** Slugs for the scope picker — what this caller can see, which is what they can name. */
  readonly useCases = input<string[]>([]);
  /** Why the picker offers only "everywhere", when the use-case list could not be read. */
  readonly useCasesError = input<string | null>(null);
  /** Raised after a stop or a restore, so the page reloads the suspensions it owns. */
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  // The kill-switch form. Signals, because the app is zoneless (FRD-203 §4).
  protected readonly showStop = signal(false);
  protected readonly targetName = targetName;
  protected readonly withheldReason = WITHHELD_REASON;
  protected readonly target = signal<'subject' | 'credential' | 'use_case'>('subject');
  protected readonly targetValue = signal('');
  protected readonly reason = signal('');
  protected readonly minutes = signal<number | null>(null);
  /**
   * Where the stop applies (empty = everywhere), what it does, and the rate a throttle holds.
   *
   * `POST /v1beta/suspensions` and the matcher obey all three. Without them every manual decision
   * is a full block everywhere, and a credential bound to one use case is stopped in all of them.
   */
  protected readonly scope = signal('');
  protected readonly action = signal<'block' | 'throttle'>('block');
  protected readonly throttleRpm = signal<number | null>(null);

  /**
   * Paged in the browser (`FRD-505` FR-12). The endpoint returns the caller's whole visible set, so
   * the counts on the page stay counts over everything rather than over one page (`FRD-208`).
   */
  protected readonly activeView = new TableView<Suspension>(this.active, haystack);
  protected readonly pastView = new TableView<Suspension>(this.past, haystack);
  protected readonly reachOf = reachOf;

  /**
   * A whole use case is stopped wherever it is called, so it takes no "Where": the matcher needs
   * the request's use case to equal the scope **and** the stopped value, and a scope naming
   * another use case would leave a stop listed as in force that matches no request at all.
   */
  protected chooseTarget(target: 'subject' | 'credential' | 'use_case'): void {
    this.target.set(target);
    if (target === 'use_case') this.scope.set('');
  }

  /** When a past decision stopped applying: lifted, or run out. */
  protected endedAt(row: Suspension): string | null {
    return row.lifted_at ?? row.expires_at;
  }

  /**
   * One box, both lists: "has this caller ever been stopped?" is answered by what is stopped now
   * together with what was stopped before, and a search over only the first would answer it wrongly.
   */
  protected searchSuspensions(value: string): void {
    this.activeView.search(value);
    this.pastView.search(value);
  }

  protected canSubmit(): boolean {
    if (!this.targetValue().trim() || this.feedback.busy()) return false;
    // The server refuses a throttle without a rate; an incident's first minute is not spent on a 400.
    return this.action() !== 'throttle' || !!this.throttleRpm();
  }

  protected stop(): void {
    if (!this.canSubmit()) return;
    const value = this.targetValue().trim();
    // A throttle holds traffic to a rate rather than stopping it, and the notice says which.
    const outcome =
      this.action() === 'throttle'
        ? `is held to ${this.throttleRpm()} requests a minute`
        : 'is stopped';
    this.feedback.run(
      this.service.suspend({
        target: this.target(),
        target_value: value,
        reason: this.reason().trim(),
        minutes: this.minutes(),
        use_case: this.scope() || null,
        action: this.action(),
        throttle_rpm: this.action() === 'throttle' ? this.throttleRpm() : null,
      }),
      {
        failure: 'Could not stop this traffic.',
        success: () => {
          this.feedback.succeed(
            `${value} ${outcome}. It takes a few seconds to reach every gateway instance.`,
          );
          this.targetValue.set('');
          this.reason.set('');
          this.minutes.set(null);
          this.scope.set('');
          this.action.set('block');
          this.throttleRpm.set(null);
          this.showStop.set(false);
          this.changed.emit();
        },
      },
    );
  }

  protected lift(row: Suspension): void {
    const question = `Restore access for ${targetName(row)}? It was stopped by ${row.author}.`;
    if (!this.confirmService.ask(question)) return;
    this.feedback.run(this.service.liftSuspension(row.id), {
      failure: 'Could not restore access.',
      success: () => {
        this.feedback.succeed(
          `${targetAtStart(row)} is restored. It takes a few seconds to reach every instance.`,
        );
        this.changed.emit();
      },
    });
  }
}
