import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Report } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Preset, isoDay, windowFor } from '../../core/ui/periods';
import { PageFeedback } from '../../core/ui/page-feedback';
import { BreakdownTable } from './breakdown-table';
import { InstallationBudgetCard } from './installation-budget-card';

/** A breakdown of the report. The one on screen is also the one an export downloads. */
type Breakdown = 'use_case' | 'model' | 'member' | 'outcome';

/** Per breakdown: its first column, what one of its rows is, and what it says when empty. */
const BREAKDOWNS: Record<Breakdown, { label: string; noun: string; empty: string }> = {
  use_case: {
    label: 'Use case',
    noun: 'use cases',
    empty: 'No traffic from any use case in this period.',
  },
  model: { label: 'Model', noun: 'models', empty: 'No model was called in this period.' },
  member: { label: 'Member', noun: 'members', empty: 'Nobody made a request in this period.' },
  outcome: { label: 'Outcome', noun: 'outcomes', empty: 'Nothing happened in this period.' },
};

/**
 * What each headline figure counts, in one sentence, shown on its info button. A figure whose
 * definition has to be guessed at gets argued about instead of acted on.
 */
const FIGURE_HELP = {
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

/** One headline figure on the totals card. */
interface Stat {
  key: string;
  label: string;
  value: string;
  testid?: string;
  unit?: string;
  help: string;
}

/**
 * Spend and usage over a period (FRD-601).
 *
 * The page owns the period and the load; the breakdowns are one panel. A failed load never shows
 * zeroes: zero spend and unknown spend are different statements, so it says so, in the backend's
 * own words where there are any.
 */
@Component({
  selector: 'app-reporting-page',
  imports: [FormsModule, BreakdownTable, InfoHint, InstallationBudgetCard],
  templateUrl: './reporting-page.html',
  providers: [PageFeedback],
})
export class ReportingPage implements OnInit {
  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly report = signal<Report | null>(null);
  protected readonly loading = signal(true);
  protected readonly exporting = signal(false);
  protected readonly preset = signal<Preset>('this-month');
  protected readonly from = signal('');
  protected readonly to = signal('');

  /**
   * Which breakdown is on screen — and the one an export downloads, so the file and the screen
   * cannot disagree (`FRD-602`: CSV is a rendering of the report).
   */
  protected readonly exportBreakdown = signal<Breakdown>('use_case');

  /** Requests in the period whose cost is unknown because the model has no price on file. */
  protected readonly unpriced = computed(() => this.report()?.totals.unpriced_requests ?? 0);

  /**
   * Requests refused rather than served (FRD-122). Shown beside the totals because a use case
   * grinding against its limit otherwise looks like a quiet one.
   */
  protected readonly refused = computed(() =>
    (this.report()?.by_outcome ?? [])
      .filter((row) => row.key !== 'served')
      .reduce((sum, row) => sum + row.requests, 0),
  );

  /** Whether this caller is seeing the whole installation or only their own use cases. */
  protected readonly seesEverything = computed(() => this.report()?.scope === 'all');

  protected readonly breakdownLabel = computed(() => BREAKDOWNS[this.exportBreakdown()].label);
  protected readonly breakdownNoun = computed(() => BREAKDOWNS[this.exportBreakdown()].noun);
  protected readonly breakdownEmpty = computed(() => BREAKDOWNS[this.exportBreakdown()].empty);

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
   * Whether the chosen breakdown can be downloaded. The CSV renderer takes three breakdowns
   * (`FRD-602`); offering `by_outcome` would be a button that answers 400.
   */
  protected readonly exportable = computed(() => this.exportBreakdown() !== 'outcome');

  /** The headline figures, each with the sentence that defines it. */
  protected readonly stats = computed<Stat[]>(() => {
    const totals = this.report()?.totals;
    if (!totals) return [];
    const latency =
      totals.avg_latency_ms === null
        ? '—'
        : `${totals.avg_latency_ms} / ${totals.max_latency_ms} ms`;
    return [
      {
        key: 'spend',
        label: 'Spend ($)',
        value: totals.cost,
        testid: 'total-cost',
        help: FIGURE_HELP.spend,
      },
      {
        key: 'requests',
        label: 'Requests',
        value: `${totals.requests}`,
        testid: 'total-requests',
        help: FIGURE_HELP.requests,
      },
      {
        key: 'failed',
        label: 'Failed',
        value: `${totals.failed_requests}`,
        testid: 'total-failed',
        help: FIGURE_HELP.failed,
      },
      {
        key: 'refused',
        label: 'Refused',
        value: `${this.refused()}`,
        testid: 'total-refused',
        help: FIGURE_HELP.refused,
      },
      {
        key: 'tokens',
        label: 'Tokens',
        value: `${totals.prompt_tokens} / ${totals.completion_tokens}`,
        unit: 'prompt / completion',
        help: FIGURE_HELP.tokens,
      },
      {
        key: 'latency',
        label: 'Latency',
        value: latency,
        unit: totals.avg_latency_ms === null ? undefined : 'average / maximum',
        help: FIGURE_HELP.latency,
      },
    ];
  });

  ngOnInit(): void {
    this.applyPreset('this-month');
  }

  protected applyPreset(preset: Preset): void {
    this.preset.set(preset);
    if (preset === 'custom') {
      return; // keep the two date fields; the user is about to change them
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
        // The previous figures, if any, stay on screen under the banner.
        this.feedback.fail(
          response,
          'Could not load the report. The gateway may be unreachable, or reporting may need OIDC enabled on it.',
        );
        this.loading.set(false);
      },
    });
  }

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
        // Released at once: an object URL held open pins the blob for the life of the page.
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
