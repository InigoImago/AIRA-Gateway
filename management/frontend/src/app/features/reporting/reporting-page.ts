import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Report } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Preset, isoDay, windowFor } from '../../core/ui/periods';
import { PageFeedback } from '../../core/ui/page-feedback';
import { BreakdownTable } from './breakdown-table';

/**
 * Spend and usage over a period (FRD-601).
 *
 * The page owns the period and the load; the three breakdowns are one panel used three times.
 *
 * What it may **not** do is show zeroes when the gateway could not be reached. Zero spend and
 * unknown spend are different statements, and only one of them is ever true — so a failed load
 * says so, in the backend's own words where there are any.
 */
@Component({
  selector: 'app-reporting-page',
  imports: [FormsModule, BreakdownTable, InfoHint],
  templateUrl: './reporting-page.html',
  // One banner for the page, the same rule as the use-case detail.
  providers: [PageFeedback],
})
export class ReportingPage implements OnInit {
  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly report = signal<Report | null>(null);
  protected readonly loading = signal(true);
  protected readonly preset = signal<Preset>('this-month');

  // Zoneless: every piece of form state is a signal, or changing it from code renders nothing.
  protected readonly from = signal('');
  protected readonly to = signal('');

  /** Requests in the period whose cost is unknown because the model has no price on file. */
  protected readonly unpriced = computed(() => this.report()?.totals.unpriced_requests ?? 0);

  /**
   * Requests that were refused rather than served (FRD-122). Shown beside the totals because a
   * use case grinding against its limit all day otherwise looks like a quiet one: the refusals
   * were 429s, and until the outcome was recorded nothing said which control produced them.
   */
  protected readonly refused = computed(() =>
    (this.report()?.by_outcome ?? [])
      .filter((row) => row.key !== 'served')
      .reduce((sum, row) => sum + row.requests, 0),
  );

  /** Whether this caller is seeing the whole installation or only their own use cases. */
  protected readonly seesEverything = computed(() => this.report()?.scope === 'all');

  ngOnInit(): void {
    this.applyPreset('this-month');
  }

  protected applyPreset(preset: Preset): void {
    this.preset.set(preset);
    if (preset === 'custom') {
      return; // keep whatever is in the two date fields; the user is about to change them
    }
    const { from, to } = windowFor(preset, new Date());
    this.from.set(from);
    this.to.set(to);
    this.load();
  }

  protected validationError(): string | null {
    if (!this.from() || !this.to()) return 'Choose a start and an end date.';
    if (this.to() <= this.from()) return 'The end date must be after the start date.';
    return null;
  }

  protected canLoad(): boolean {
    return !this.validationError() && !this.loading();
  }

  protected load(): void {
    if (this.validationError()) return;
    this.loading.set(true);
    this.feedback.clear();
    this.service.report(this.from(), this.to()).subscribe({
      next: (report) => {
        this.report.set(report);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        // Not `report.set(null)` with an empty screen: the previous figures, if any, stay on
        // screen under an explicit banner rather than being replaced by a silent nothing.
        this.feedback.fail(
          response,
          'Could not load the report. The gateway may be unreachable, or reporting may need OIDC enabled on it.',
        );
        this.loading.set(false);
      },
    });
  }

  /** Which table the spreadsheet contains. A CSV is one table, so it is chosen, not guessed. */
  /**
   * Which breakdown is on screen — **and** the one an export downloads.
   *
   * It used to be four tables stacked, plus a fourth selector that only governed the download. A
   * page long enough that its own export control scrolls out of sight is a page nobody reads to
   * the bottom of, and two ideas of "which table" is one more than there should be: the file and
   * the screen now cannot disagree, which is the same argument `FRD-602` makes for CSV being a
   * *rendering* of the report rather than its own endpoint.
   */
  protected readonly exportBreakdown = signal<'use_case' | 'model' | 'member' | 'outcome'>(
    'use_case',
  );

  /** What the chosen breakdown is called, and what one of its rows is. */
  protected readonly breakdownLabel = computed(
    () =>
      ({
        use_case: 'Use case',
        model: 'Model',
        member: 'Member',
        outcome: 'Outcome',
      })[this.exportBreakdown()],
  );
  protected readonly breakdownNoun = computed(
    () =>
      ({
        use_case: 'use cases',
        model: 'models',
        member: 'members',
        outcome: 'outcomes',
      })[this.exportBreakdown()],
  );
  protected readonly breakdownEmpty = computed(
    () =>
      ({
        use_case: 'No traffic from any use case in this period.',
        model: 'No model was called in this period.',
        member: 'Nobody made a request in this period.',
        outcome: 'Nothing happened in this period.',
      })[this.exportBreakdown()],
  );

  /** The rows of the chosen breakdown. */
  protected readonly breakdownRows = computed(() => {
    const report = this.report();
    if (!report) return [];
    switch (this.exportBreakdown()) {
      case 'model':
        return report.by_model;
      case 'member':
        return report.by_member;
      case 'outcome':
        return report.by_outcome;
      default:
        return report.by_use_case;
    }
  });

  /**
   * Whether the chosen breakdown can be downloaded.
   *
   * `by_outcome` is on screen but **not** an export: the CSV renderer takes three breakdowns
   * (`FRD-602`), and offering a fourth would produce a 400 from a button that looked ready. An
   * action that cannot be carried out is worse than an absent one.
   */
  protected readonly exportable = computed(() => this.exportBreakdown() !== 'outcome');
  /**
   * What each figure counts, in one sentence.
   *
   * A governance console is read by people deciding whether a control is working, and a number
   * whose definition is guessed at is a number that gets argued about instead of acted on. These
   * used to live in the headings, where "Refused by a control" wrapped to two lines and broke the
   * card row — the answer is not a shorter truth but a heading plus a place to put the rest.
   */
  protected readonly explain = {
    spend:
      'Money for the period, priced per model from the prompt/completion split. Traffic on a model with no price on file is counted apart, never as zero — see the caveat below when it appears.',
    requests:
      'Requests that reached a model. An embedding batch counts as the many texts it carries, not as one.',
    failed:
      'Requests that reached a model and came back with an error — an upstream failure, not a decision of ours.',
    refused:
      'Requests this gateway itself declined: over budget, over a rate limit, blocked by the pipeline, or asking for something no model could serve. These never reached a model.',
    tokens:
      'Prompt tokens (what was sent, including any attachment) and completion tokens (what came back, including reasoning the model did not show you). Both are billed, at different rates.',
    latency:
      'Average and maximum, end to end, for the period. Not a percentile: that would need a Postgres-only function, and the hermetic tests run on SQLite.',
  };

  // The hover/focus/pin behaviour that used to live here is now `core/ui/info-hint`. It was
  // written twice within a week — here and on a table header — which is one time more than a
  // three-way interaction should be got right. Each hint owns its own state now, so "one at a
  // time" is no longer enforced; six open at once is a reader deliberately opening six.

  /**
   * The headline figures, as data rather than as six near-identical blocks of markup.
   *
   * Written as a loop because the six differed only in their label, their value and their
   * sentence — and because the sentence *is* part of the figure. A number in a governance console
   * whose definition has to be guessed at gets argued about instead of acted on.
   */
  protected readonly stats = computed(() => {
    const totals = this.report()?.totals;
    if (!totals) return [];
    const latency =
      totals.avg_latency_ms === null
        ? '—'
        : `${totals.avg_latency_ms} / ${totals.max_latency_ms} ms`;
    return [
      {
        key: 'spend',
        label: 'Spend',
        value: totals.cost,
        testid: 'total-cost',
        help: this.explain.spend,
      },
      {
        key: 'requests',
        label: 'Requests',
        value: `${totals.requests}`,
        testid: 'total-requests',
        help: this.explain.requests,
      },
      {
        key: 'failed',
        label: 'Failed',
        value: `${totals.failed_requests}`,
        testid: 'total-failed',
        help: this.explain.failed,
      },
      {
        key: 'refused',
        label: 'Refused',
        value: `${this.refused()}`,
        testid: 'total-refused',
        help: this.explain.refused,
      },
      {
        key: 'tokens',
        label: 'Tokens',
        value: `${totals.prompt_tokens} / ${totals.completion_tokens}`,
        unit: 'prompt / completion',
        help: this.explain.tokens,
      },
      {
        key: 'latency',
        label: 'Latency',
        value: latency,
        unit: totals.avg_latency_ms === null ? undefined : 'average / maximum',
        help: this.explain.latency,
      },
    ] as {
      key: string;
      label: string;
      value: string;
      testid?: string;
      unit?: string;
      help: string;
    }[];
  });

  protected readonly exporting = signal(false);

  protected download(): void {
    if (this.validationError() || this.exporting()) return;
    this.exporting.set(true);
    this.feedback.clear();
    this.service.reportCsv(this.from(), this.to(), this.exportBreakdown()).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `aira-usage_${this.exportBreakdown()}_${this.from()}_${this.to()}.csv`;
        link.click();
        // Released immediately: an object URL held open pins the blob in memory for the life of
        // the page, and somebody exporting a dozen periods would keep every one of them.
        URL.revokeObjectURL(url);
        this.exporting.set(false);
      },
      error: (response: unknown) => {
        this.feedback.fail(response, 'Could not export the report.');
        this.exporting.set(false);
      },
    });
  }

  /** The end date as a person reads it: the last day included, not the exclusive bound. */
  protected inclusiveEnd(): string {
    if (!this.to()) return '';
    const [year, month, day] = this.to().split('-').map(Number);
    return isoDay(new Date(year, month - 1, day - 1));
  }
}
