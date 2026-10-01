import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MeService, unitSuffix } from '../../api/me.service';
import { Granularity, SeriesSplit, UsageSeries } from '../../api/types/reporting';
import { InfoHint } from '../info-hint';
import { Preset } from '../periods';
import { CompositionBar } from './composition-bar';
import { METRIC_HELP, METRIC_LABELS, Metric, buildChart, buildDays } from './usage-series';
import { UsageDays } from './usage-days';
import { UsageHistogram } from './usage-histogram';

/** What a split is called on screen, and what one of its bands is. */
const SPLIT_LABELS: Record<SeriesSplit, { label: string; noun: string }> = {
  model: { label: 'Model', noun: 'model' },
  use_case: { label: 'Use case', noun: 'use case' },
  outcome: { label: 'Outcome', noun: 'outcome' },
};

/** What a period is called, for a card whose page has no picker of its own. */
const PERIOD_LABELS: Partial<Record<Preset, string>> = {
  today: 'Today',
  'this-month': 'This month',
  'last-month': 'Last month',
  'last-7-days': 'Last 7 days',
  'last-30-days': 'Last 30 days',
};

/** The granularities offered, and what each one means for the axis. */
const GRANULARITIES: { value: 'auto' | Granularity; label: string }[] = [
  { value: 'auto', label: 'Automatic' },
  { value: 'day', label: 'By day' },
  { value: 'hour', label: 'By hour' },
];

/**
 * Usage over time, and the shape of the whole (`FRD-626`).
 *
 * Two pictures of one series, in one card, under one set of controls, because they answer two halves
 * of the same question and reading them against each other is the point: the **histogram** says
 * *when* — which day, which model, what it cost — and the **blocks** say *what the whole is made of*,
 * which the histogram cannot, since each of its stacks is scaled against the tallest day.
 *
 * The parent owns the window and the load; this owns the measure. Which measure is drawn needs no
 * request — the gateway sends spend, requests and tokens for every bucket — and the two that do
 * (`granularity`, `split`) are reported upwards rather than fetched here, so one component keeps
 * owning the load (`CLAUDE.md` §3).
 *
 * **A figure that did not arrive is never a zero.** An unreachable gateway and a genuinely quiet
 * period produce different cards, in the backend's own words where there are any.
 */
@Component({
  selector: 'app-usage-chart',
  imports: [FormsModule, InfoHint, CompositionBar, UsageHistogram, UsageDays],
  templateUrl: './usage-chart.html',
})
export class UsageChart {
  /** The series, or `null` when it has not arrived — which is not the same as "nothing happened". */
  readonly series = input<UsageSeries | null>(null);
  readonly loading = input(false);
  /** Why the series is absent, in the backend's own words. Empty when nothing went wrong. */
  readonly reason = input('');
  /** The granularity currently asked for. `auto` lets the window decide. */
  readonly granularity = input<'auto' | Granularity>('auto');
  readonly split = input<SeriesSplit>('model');
  /**
   * Which splits to offer. A use case's own page offers two of the three: banding its own traffic by
   * use case would draw one band and call it a comparison.
   */
  readonly splits = input<SeriesSplit[]>(['model', 'use_case', 'outcome']);
  /**
   * What the card is about, in its own heading, and the subject its subtitle names.
   *
   * Not decoration. On a use case's own page this chart is narrowed to the **reader's own** traffic
   * (`FRD-626` FR-15) and sits above a card headed *What you used*; a chart headed "Usage over time"
   * there would be read as the whole use case's — which is a different figure, larger, and on the
   * same screen.
   */
  /**
   * Which periods to offer, or **none** where the page already has a picker of its own.
   *
   * Empty on the reporting screen, whose period control sits above everything and drives the tables
   * too — two pickers on one page are two answers to one question. Non-empty on a use case's own
   * overview, which has no period control at all: the consumption card there answers *this month*
   * and *today* by design (`FRD-603`), and the panel below answers whatever period the reader's
   * **budget** resets in — so on the first of a month the whole page could only talk about one day.
   */
  readonly periods = input<Preset[]>([]);
  readonly period = input<Preset>('last-30-days');
  readonly heading = input('Usage over time');
  readonly subject = input('What was used when');
  readonly periodChange = output<Preset>();
  readonly granularityChange = output<'auto' | Granularity>();
  readonly splitChange = output<SeriesSplit>();

  private readonly currency = inject(MeService).currency;

  /** Which measure is drawn. A signal, not a property: this console is zoneless (`FRD-203` §4). */
  protected readonly metric = signal<Metric>('cost');

  protected readonly granularities = GRANULARITIES;

  /** The periods on offer, each with the name a reader reads. */
  protected readonly periodOptions = computed(() =>
    this.periods().map((value) => ({ value, label: PERIOD_LABELS[value] ?? value })),
  );

  protected readonly chart = computed(() => {
    const series = this.series();
    return series ? buildChart(series, this.metric()) : null;
  });

  /**
   * The same period read day by day (`FRD-626` FR-16), with every measure on each row.
   *
   * The metric only decides the **order** within a day here, never which figures are shown: the
   * question this view answers — *what did I use that day and what did it cost* — has money in it
   * whatever the picture above is currently drawing.
   */
  protected readonly days = computed(() => {
    const series = this.series();
    return series ? buildDays(series, this.metric()) : [];
  });

  /**
   * What the fold says before it is opened.
   *
   * A count, because a summary that says only "day by day" gives a reader no reason to open it —
   * and the number is the one thing they can act on without opening anything.
   */
  protected readonly daysLabel = computed(() => {
    const days = this.days().length;
    return days === 1 ? '1 day with traffic' : `${days} days with traffic`;
  });

  /** The currency, for the one money heading in the day list. */
  protected readonly unit = computed(() => unitSuffix(this.currency()));

  /** The measures offered, with the currency in the spend label so a figure is never unit-less. */
  protected readonly metrics = computed(() =>
    (Object.keys(METRIC_LABELS) as Metric[]).map((metric) => ({
      value: metric,
      label:
        metric === 'cost'
          ? `${METRIC_LABELS.cost}${unitSuffix(this.currency())}`
          : METRIC_LABELS[metric],
    })),
  );

  protected readonly metricLabel = computed(
    () => this.metrics().find((entry) => entry.value === this.metric())?.label ?? '',
  );

  protected readonly metricHelp = computed(() => METRIC_HELP[this.metric()]);

  protected readonly splitOptions = computed(() =>
    this.splits().map((value) => ({ value, ...SPLIT_LABELS[value] })),
  );

  protected readonly noun = computed(() => SPLIT_LABELS[this.split()].noun);

  /** What the axis turned out to be, which `auto` only decides on the server. */
  protected readonly resolved = computed(() => this.series()?.granularity ?? null);

  protected setMetric(metric: string): void {
    this.metric.set(metric as Metric);
  }

  protected setPeriod(value: string): void {
    this.periodChange.emit(value as Preset);
  }

  protected setGranularity(value: string): void {
    this.granularityChange.emit(value as 'auto' | Granularity);
  }

  protected setSplit(value: string): void {
    this.splitChange.emit(value as SeriesSplit);
  }
}
