import { Component, OnInit, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';
import { MeService, unitSuffix } from '../../core/api/me.service';
import { Report } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { displayCount } from '../../core/ui/money';
import {
  DayWindow,
  Preset,
  lastDayOf,
  periodText,
  windowError,
  windowFor,
} from '../../core/ui/periods';
import { PageFeedback } from '../../core/ui/page-feedback';
import { saveBlob } from '../../core/ui/save-blob';
import { BreakdownTable } from './breakdown-table';
import { InstallationBudgetCard } from './installation-budget-card';

/** A breakdown of the report. The one on screen is also the one an export downloads. */
type Breakdown = 'use_case' | 'model' | 'member' | 'outcome';

/**
 * Per breakdown: its first column, one of its rows with its article, what its rows are, and what
 * it says when empty. The article is written, not derived: "a outcome" is what a rule of thumb
 * about the first letter produces.
 */
const BREAKDOWNS: Record<Breakdown, { label: string; item: string; noun: string; empty: string }> =
  {
    use_case: {
      label: 'Use case',
      item: 'a use case',
      noun: 'use cases',
      empty: 'No traffic from any use case in this period.',
    },
    model: {
      label: 'Model',
      item: 'a model',
      noun: 'models',
      empty: 'No model was called in this period.',
    },
    member: {
      label: 'Member',
      item: 'a member',
      noun: 'members',
      empty: 'Nobody made a request in this period.',
    },
    outcome: {
      label: 'Outcome',
      item: 'an outcome',
      noun: 'outcomes',
      empty: 'Nothing happened in this period.',
    },
  };

/**
 * The outcomes in which this gateway declined a request on purpose (`aira_gateway.audit.Outcome`).
 *
 * An allow-list, never "everything but served", which would count an administrator's model check,
 * an upstream outage and a caller who hung up as refusals. An outcome the gateway adds later
 * belongs to no figure until somebody decides which one it means.
 */
const REFUSALS: ReadonlySet<string> = new Set([
  'rate_limited',
  'budget_exceeded',
  'suspended',
  'blocked_by_pipeline',
  'model_not_found',
  'invalid_request',
  'request_too_large',
  'no_capable_model',
]);

/**
 * The outcomes in which a request broke while being served: the provider's error, or this
 * gateway's. Not the server's `failed_requests`, which is every status of 400 or more and so
 * counts every refusal a second time.
 */
const FAILURES: ReadonlySet<string> = new Set(['upstream_error', 'internal_error']);

/** The outcome of a request that ran — the only kind that can have a cost to be unknown. */
const SERVED: ReadonlySet<string> = new Set(['served']);

/**
 * What each headline figure counts, in one sentence, shown on its info button. A figure whose
 * definition has to be guessed at gets argued about instead of acted on.
 */
const FIGURE_HELP = {
  spend:
    'Money for the period, priced per model from the prompt/completion split. Traffic on a model with no price on file is counted apart, never as zero — see the caveat below when it appears.',
  requests:
    // **Corrected on 2026-10-01**, and the correction is the point: this said an embedding batch
    // "counts as the many texts it carries, not as one". That is true of a *budget* and false of
    // this figure. The audit writes one row per call (`test_serving_options.py` asserts exactly one
    // for a batch of two) and this counts rows; a request budget weighs the same batch as one per
    // text (`FRD-113` FR-6). Both are deliberate, and somebody reconciling one against the other
    // has to be told, or the report is the half they stop believing.
    'Requests the gateway handled, one per call — including the ones it refused and the ones a pipeline step made on the caller’s behalf. An embedding batch is one call however many texts it carries: that is where this figure and a request budget differ, because a budget weighs such a batch as one request per text.',
  failed:
    'Requests that broke while being served: the model’s provider answered with an error, or this gateway did. A request declined on purpose is counted under Refused, never here.',
  refused:
    'Requests this gateway itself declined: over a budget or a rate limit, stopped by a rule or in an incident, blocked by the pipeline, malformed or too large, or asking for a model it does not have or that cannot serve them. These never reached a model to be answered. A caller who hung up and a model check an administrator ran are neither refused nor failed — the outcome breakdown lists them.',
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
  imports: [FormsModule, RouterLink, BreakdownTable, InfoHint, InstallationBudgetCard],
  templateUrl: './reporting-page.html',
  providers: [PageFeedback],
})
export class ReportingPage implements OnInit {
  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);
  private readonly currency = inject(MeService).currency;
  private readonly budgetCard = viewChild(InstallationBudgetCard);
  /** A count as a person reads it, grouped (`12,840`) like every other figure in the console. */
  protected readonly count = displayCount;

  protected readonly report = signal<Report | null>(null);
  protected readonly loading = signal(true);
  protected readonly exporting = signal(false);
  protected readonly preset = signal<Preset>('this-month');
  protected readonly from = signal('');
  protected readonly to = signal('');

  /**
   * The window the figures on screen were asked for — what a download exports and what the totals
   * say they cover. Not the date fields: those hold whatever was typed into Custom… since, and a
   * file for a period nobody has looked at is not "exactly this table".
   */
  protected readonly shown = signal<DayWindow | null>(null);

  /** The report request in flight. The next load cancels it, so the period asked for last wins. */
  private request: Subscription | null = null;

  /**
   * Which breakdown is on screen — and the one an export downloads, so the file and the screen
   * cannot disagree (`FRD-602`: CSV is a rendering of the report).
   */
  protected readonly exportBreakdown = signal<Breakdown>('use_case');

  /** Requests in the period that ran and whose cost is unknown, not zero (`FRD-403`). */
  protected readonly unpriced = computed(() => this.report()?.totals.unpriced_requests ?? 0);

  /**
   * Of those, the ones whose upstream reported **no token usage** (`FRD-626` FR-18). A different
   * cause from a model with no price on file, with a different remedy: a price fixes the second
   * and can do nothing for the first, so the two are counted and worded apart.
   */
  protected readonly unmetered = computed(() => this.report()?.totals.unmetered_requests ?? 0);

  /**
   * Requests booked at a floor because their caller left before the provider said what they used
   * (`FRD-128` FR-6). Each one makes the spend above a lower bound, which the screen says.
   */
  protected readonly partialUsage = computed(
    () => this.report()?.totals.partial_usage_requests ?? 0,
  );

  /**
   * Requests refused rather than served (FRD-122). Shown beside the totals because a use case
   * grinding against its limit otherwise looks like a quiet one.
   */
  protected readonly refused = computed(() => this.outcomes(REFUSALS));

  /** Requests that broke while being served — never one that was refused. */
  protected readonly failed = computed(() => this.outcomes(FAILURES));

  /**
   * Whether the period's spend is unknown outright (`FRD-626` FR-18): every request that ran was
   * unpriced and nothing else carried a cost, so `0.00` would be a measurement nobody made.
   *
   * Against the requests that **ran**, not all of them: a refusal ran nothing and has no cost to be
   * unknown, so with one refusal in the period "every request was unpriced" could never hold.
   */
  protected readonly spendUnknown = computed(() => {
    const totals = this.report()?.totals;
    if (!totals || totals.unpriced_requests === 0 || totals.cost_nanos !== 0) return false;
    return totals.unpriced_requests >= this.outcomes(SERVED);
  });

  /** The period the figures are for, as a person reads it: first and last day included. */
  protected readonly shownPeriod = computed(() => periodText(this.shown()));

  /** Whether this caller is seeing the whole installation or only their own use cases. */
  protected readonly seesEverything = computed(() => this.report()?.scope === 'all');

  protected readonly breakdownLabel = computed(() => BREAKDOWNS[this.exportBreakdown()].label);
  protected readonly breakdownItem = computed(() => BREAKDOWNS[this.exportBreakdown()].item);
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
        label: `Spend${unitSuffix(this.currency())}`,
        // An em dash is a measurement nobody made; the caveat below says which kind.
        value: this.spendUnknown() ? '—' : totals.cost,
        testid: 'total-cost',
        help: FIGURE_HELP.spend,
      },
      {
        key: 'requests',
        label: 'Requests',
        value: displayCount(totals.requests),
        testid: 'total-requests',
        help: FIGURE_HELP.requests,
      },
      {
        key: 'failed',
        label: 'Failed',
        value: displayCount(this.failed()),
        testid: 'total-failed',
        help: FIGURE_HELP.failed,
      },
      {
        key: 'refused',
        label: 'Refused',
        value: displayCount(this.refused()),
        testid: 'total-refused',
        help: FIGURE_HELP.refused,
      },
      {
        key: 'tokens',
        label: 'Tokens',
        value: `${displayCount(totals.prompt_tokens)} / ${displayCount(totals.completion_tokens)}`,
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
    return windowError({ from: this.from(), to: this.to() });
  }

  protected canLoad(): boolean {
    return !this.validationError() && !this.loading();
  }

  protected load(): void {
    if (this.validationError()) return;
    const window: DayWindow = { from: this.from(), to: this.to() };
    this.loading.set(true);
    this.feedback.clear();
    // The banner just cleared may have been the budget card's only word on why it is empty.
    this.budgetCard()?.retryIfFailed();
    this.request?.unsubscribe();
    this.request = this.service.report(window.from, window.to).subscribe({
      next: (report) => {
        this.report.set(report);
        this.shown.set(window);
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
    const shown = this.shown();
    if (!shown || this.exporting()) return;
    const breakdown = this.exportBreakdown();
    this.exporting.set(true);
    this.feedback.clear();
    this.service.reportCsv(shown.from, shown.to, breakdown).subscribe({
      next: (blob) => {
        // Named by the last day included, as the screen says it: by the exclusive bound, a file of
        // October's figures would carry the first of November.
        saveBlob(blob, `aira-usage_${breakdown}_${shown.from}_${lastDayOf(shown.to)}.csv`);
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
    return lastDayOf(this.to());
  }

  /** Requests in the period whose outcome is one of `keys`. */
  private outcomes(keys: ReadonlySet<string>): number {
    return (this.report()?.by_outcome ?? [])
      .filter((row) => keys.has(row.key))
      .reduce((sum, row) => sum + row.requests, 0);
  }
}
