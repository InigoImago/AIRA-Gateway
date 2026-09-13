import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ReportRow, UseCaseConsumption } from '../../core/api/models';
import { InfoHint } from '../../core/ui/info-hint';

/** One figure in the card: what it is called, what it says, and what it counts. */
interface Stat {
  key: string;
  label: string;
  value: string;
  help: string;
}

/**
 * What each figure counts, for the "i" beside it — the same explanations the reporting screen
 * gives (`FRD-206`), because these are the figures somebody reconciles against an invoice.
 */
const FIGURE_HELP = {
  cost: `What this use case's traffic cost, priced per model from the catalog at the time of each
      request. Traffic on a model with no price on file is counted separately and is not in this
      figure — unknown is not zero.`,
  requests: `Every request the gateway handled for this use case, including the ones it refused —
      over budget, rate-limited, or blocked by a pipeline step. A refusal costs nothing and is
      still something that happened.`,
  tokens: `Prompt and completion tokens together. A token differs in price by more than ten times
      between models and output is billed several times higher than input, so this is a volume
      figure and not a cost one.`,
  cached: `How much of the input the provider served from its prompt cache instead of reading
      again — the share is what makes caching measurable. 0 % with caching switched on means the
      prefix is changing between turns, the gap between them is longer than the cache lifetime, or
      the model does not cache at all. A provider that reports nothing (a self-hosted one) also
      shows 0 %, and there it costs nothing either way.`,
};

/**
 * What a use case has consumed, whether or not a limit is set (`FRD-603`).
 *
 * The page owns the load and this panel the rendering (`CLAUDE.md` §3); it has no mutations, so
 * every state is said in the card. **Unknown is never rendered as zero**: a figure that did not
 * arrive is an em dash with a reason, and `0.00` means genuinely nothing consumed.
 */
@Component({
  selector: 'app-consumption-panel',
  imports: [RouterLink, InfoHint],
  templateUrl: './consumption-panel.html',
})
export class ConsumptionPanel {
  readonly slug = input.required<string>();
  readonly consumption = input.required<UseCaseConsumption>();

  protected readonly monthStats = computed(() => this.statsFor(this.consumption().month));
  protected readonly todayStats = computed(() => this.statsFor(this.consumption().today));

  /** Requests this month whose cost is unknown because their model has no price on file. */
  protected readonly unpriced = computed(() => this.consumption().month?.unpriced_requests ?? 0);

  private share(row: ReportRow): string {
    if (!row.prompt_tokens) return '—';
    return `${Math.round((100 * (row.cached_input_tokens ?? 0)) / row.prompt_tokens)}%`;
  }

  private statsFor(row: ReportRow | null): Stat[] {
    return [
      // Spend first: a token count cannot stand in for cost, given the price spread between
      // models (`FRD-403`).
      { key: 'cost', label: 'Spend ($)', value: row ? row.cost : '—', help: FIGURE_HELP.cost },
      {
        key: 'requests',
        label: 'Requests',
        value: row ? `${row.requests}` : '—',
        help: FIGURE_HELP.requests,
      },
      {
        key: 'tokens',
        label: 'Tokens',
        value: row ? `${row.total_tokens}` : '—',
        help: FIGURE_HELP.tokens,
      },
      {
        key: 'cached',
        label: 'Cached',
        // A share, not a count: what fraction the cache caught is the question.
        value: row ? this.share(row) : '—',
        help: FIGURE_HELP.cached,
      },
    ];
  }
}
