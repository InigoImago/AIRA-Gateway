import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ReportRow, UseCaseConsumption } from '../../core/api/models';
import { InfoHint } from '../../core/ui/info-hint';

/**
 * What a use case has consumed, whether or not a limit is set (`FRD-603`).
 *
 * On the **overview**, because that is where somebody goes to see where a use case stands, and
 * consumption is a fact about the use case rather than a fact about its budgets. It began life in
 * the budgets tab, which was the shape of the defect it fixes: consumption used to be rendered
 * only *inside* a budget card, as a fraction of a limit, so a use case with no limit showed
 * neither the tokens nor the money it had spent — while every request had been counted and priced
 * in the request log all along.
 *
 * A panel, not a block in the parent: the page owns the load, the panel owns the rendering
 * (`CLAUDE.md` §3). It has no mutations, so it reports through neither the page banner nor one of
 * its own — every state it can be in is a statement about the figures, and each one is said in
 * the card.
 *
 * The rule it exists to keep: **unknown is never rendered as zero.** A figure that did not arrive
 * is an em dash with a reason beside it; `0.00` is reserved for a use case that genuinely
 * consumed nothing.
 */
/** One figure in the card: what it is called, what it says, and what it counts. */
interface Stat {
  key: string;
  label: string;
  value: string;
  help: string;
}

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

  /**
   * What each figure counts, for the "i" beside it.
   *
   * The reporting screen carries the same explanations for the same numbers (`FRD-206`), and they
   * are worth repeating here rather than assuming: "Spend" that quietly excludes unpriced traffic
   * and "Requests" that includes the ones a control refused are exactly the two figures somebody
   * would otherwise reconcile against an invoice and give up on.
   */
  private readonly explain = {
    cost: `What this use case's traffic cost, priced per model from the catalog at the time of each
      request. Traffic on a model with no price on file is counted separately and is not in this
      figure — unknown is not zero.`,
    requests: `Every request the gateway handled for this use case, including the ones it refused —
      over budget, rate-limited, or blocked by a pipeline step. A refusal costs nothing and is
      still something that happened.`,
    tokens: `Prompt and completion tokens together. A token differs in price by more than ten times
      between models and output is billed several times higher than input, so this is a volume
      figure and not a cost one.`,
  };

  private statsFor(row: ReportRow | null): Stat[] {
    return [
      // Spend first: it is the figure anybody asking "what has this cost" came for, and the one a
      // token count cannot stand in for — the same model-to-model price spread that made
      // `FRD-403` reject token caps as a cost control.
      { key: 'cost', label: 'Spend', value: row ? row.cost : '—', help: this.explain.cost },
      {
        key: 'requests',
        label: 'Requests',
        value: row ? `${row.requests}` : '—',
        help: this.explain.requests,
      },
      {
        key: 'tokens',
        label: 'Tokens',
        value: row ? `${row.total_tokens}` : '—',
        help: this.explain.tokens,
      },
    ];
  }
}
