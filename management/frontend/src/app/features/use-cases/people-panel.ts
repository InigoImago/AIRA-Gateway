import { Component, computed, input } from '@angular/core';
import { unitSuffix } from '../../core/api/me.service';
import { Budget, BudgetUsage, PersonRow } from '../../core/api/models';
import { InfoHint } from '../../core/ui/info-hint';
import { displayAmount, displayCount, nanosOf, storedAmount } from '../../core/ui/money';
import { PERIOD_CLOCK } from '../../core/ui/periods';

/** One person's row, ready to render: their totals, the two halves, and what is left. */
interface PersonView {
  name: string;
  requests: string;
  tokens: string;
  cost: string;
  /** What came from a Keycloak sign-in and what from a key. `null` where that half is nothing. */
  signedIn: string | null;
  viaKey: string | null;
  /** The per-head allowance, where one is configured, and what remains of it. */
  allowance: string | null;
  remaining: string | null;
  /** True once this person is over the allowance — a fact, not a warning to be softened. */
  over: boolean;
}

/** The most decimals an amount can have: one nano-unit. */
const MAX_PLACES = 9;

/**
 * Significant decimals in an amount, ignoring trailing zeros: a limit stored as `0.010000` has
 * two, not six.
 */
function decimals(value: string): number {
  return (value.split('.')[1] ?? '').replace(/0+$/, '').length;
}

/**
 * Nano-units as a decimal string, in the precision the figures beside it use. Fixed at two places
 * a small remainder vanishes; more places than those shown would claim precision never measured.
 * Half-up and never a non-zero amount as zero — the rule every other amount here follows.
 */
function money(nanos: number, ...alongside: string[]): string {
  const places = Math.max(2, ...alongside.map((value) => decimals(value)));
  return displayAmount(nanos, Math.min(places, MAX_PLACES));
}

/**
 * One half of a person's spend, or `null` when that credential did not call at all.
 *
 * Spend or tokens count as calling, not only requests: a pipeline step's model call is recorded
 * with no request against it (`FRD-125` FR-9), and its cost still came from somewhere.
 */
function half(person: PersonRow, method: string): string | null {
  const part = person.by_method?.[method];
  if (!part || (part.requests === 0 && part.total_tokens === 0 && part.cost_nanos === 0)) {
    return null;
  }
  return `${part.cost} / ${part.requests} req`;
}

/**
 * What each person consumed in one use case, with or without a limit (`FRD-606`).
 *
 * - **One person, one row.** A sign-in and an API key identify the same human differently; the
 *   gateway groups on the name (`by_person`) and shows the two halves.
 * - **Figures without a limit**, as `FRD-603` requires one level up.
 * - **What is left** of an `each_member` budget: one row, one counter per head, so the remainder
 *   is arithmetic this panel can do.
 *
 * The page owns the load and this panel the rendering (`CLAUDE.md` §3); it has no mutations.
 */
@Component({
  selector: 'app-people-panel',
  imports: [InfoHint],
  template: `
    <div class="card">
      <div class="spread">
        <h3 class="section-title">
          {{ mine() ? 'What you used' : 'What each person used' }}
          <app-info-hint label="What each person used" testid="people-consumption" [wide]="true">
            Recorded requests for this use case. Somebody who calls with an API key and also signs
            in to the console is one row here, with the two halves shown — the gateway records both
            under the name they were known by. Traffic from before that name was recorded is listed
            under its own subject rather than folded into somebody it probably belonged to.
          </app-info-hint>
        </h3>
        @if (whyOnly()) {
          <span class="muted text-sm" data-testid="people-why-only">
            {{ whyOnly() }}
          </span>
        }
        <span class="muted text-sm" data-testid="people-window">
          {{ windowLabel() }}
          <app-info-hint
            label="the period these figures cover"
            testid="people-window"
            [wide]="true"
          >
            {{ windowReason() }}
          </app-info-hint>
        </span>
      </div>

      @if (unavailable()) {
        <p class="callout" data-testid="people-unavailable">
          These figures did not arrive, so nothing is shown rather than zeroes.
          {{ reason() }}
        </p>
      } @else if (people().length === 0) {
        <!-- "Nobody called" and "you did not call" are different facts about the use case. -->
        <p class="empty" data-testid="people-empty">
          @if (mine()) {
            You have not called this use case in this period.
          } @else {
            Nobody has called this use case in this period.
          }
        </p>
      } @else {
        <div class="table-wrap" style="margin-top: 1rem">
          <table class="table">
            <thead>
              <tr>
                <th scope="col">{{ mine() ? 'You' : 'Person' }}</th>
                <th scope="col">Requests</th>
                <th scope="col">Tokens</th>
                <th scope="col">Spend{{ unit() }}</th>
                @if (hasAllowance()) {
                  <th scope="col">
                    Left of allowance
                    <app-info-hint label="Left of allowance" testid="people-left" [wide]="true">
                      From the per-person budget on this use case and what this person has recorded
                      against it in the current period. The gateway refuses on its own counter,
                      which is the authoritative one; this is what the request log shows.
                    </app-info-hint>
                  </th>
                }
              </tr>
            </thead>
            <tbody>
              @for (person of view(); track person.name) {
                <tr [attr.data-testid]="'person-' + person.name">
                  <th scope="row">
                    <code>{{ person.name }}</code>
                    <!-- The split under the name, not in two more columns between name and spend. -->
                    @if (person.signedIn || person.viaKey) {
                      <div class="muted text-sm">
                        @if (person.signedIn) {
                          <span>signed in: {{ person.signedIn }}</span>
                        }
                        @if (person.signedIn && person.viaKey) {
                          <span> · </span>
                        }
                        @if (person.viaKey) {
                          <span>API key: {{ person.viaKey }}</span>
                        }
                      </div>
                    }
                  </th>
                  <td>{{ person.requests }}</td>
                  <td>{{ person.tokens }}</td>
                  <td>{{ person.cost }}</td>
                  @if (hasAllowance()) {
                    <td>
                      @if (person.remaining === null) {
                        <span class="muted">—</span>
                      } @else {
                        <span [class.is-over]="person.over">{{ person.remaining }}</span>
                        <span class="muted"> of {{ person.allowance }}</span>
                      }
                    </td>
                  }
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      <!--
        A use-case budget is one shared pot, so what is left is the use case's, not the reader's;
        dividing it per head would invent an allowance nobody configured.
      -->
      @if (sharedLeft().length) {
        <p class="muted text-sm shared-left" data-testid="shared-left">
          Left of this use case's shared {{ sharedPeriod() }} budget:
          @for (figure of sharedLeft(); track figure.label) {
            <span
              ><strong>{{ figure.value }}</strong> {{ figure.label }}{{ $last ? '' : ' · ' }}</span
            >
          }
          — shared with everybody in it.
        </p>
      }
    </div>
  `,
  styles: `
    /* A row header is a person's name, not a column heading: no small uppercase. */
    tbody th {
      text-transform: none;
      letter-spacing: 0;
      font-size: var(--aira-text-base);
      color: var(--aira-text);
      text-align: left;
    }
    tbody th .muted {
      text-transform: none;
      letter-spacing: 0;
    }
    .is-over {
      color: var(--aira-danger);
      font-weight: 600;
    }
    .shared-left {
      margin: 0.75rem 0 0;
    }
  `,
})
export class PeoplePanel {
  /** Consumption over the month, and over today — the budget's period decides which is used. */
  readonly month = input<PersonRow[]>([]);
  readonly today = input<PersonRow[]>([]);
  readonly budgets = input<Budget[]>([]);
  /**
   * Which window did not arrive. Each on its own: the panel reads one window or the other, and a
   * window that failed must not read as "nobody called" because the *other* one arrived.
   */
  readonly monthUnavailable = input(false);
  readonly todayUnavailable = input(false);
  readonly reason = input('');
  /** The unit money is in, as the page has it (`AIRA_CURRENCY`). */
  readonly currency = input('');
  protected readonly unit = computed(() => unitSuffix(this.currency()));
  /**
   * Narrow the panel to one person — the signed-in reader, on the use-case overview. The same
   * component, so the remainder arithmetic exists once and the two views cannot disagree.
   */
  readonly only = input<string | null>(null);
  /**
   * Why only one row, where that is not obvious from the screen it is on.
   *
   * The overview is one person's page and needs no sentence; the members tab is a table of people,
   * and one row in it reads as a broken screen unless it says otherwise (`FRD-206`: what a reader
   * concludes from an absence is part of the design).
   */
  readonly whyOnly = input('');
  /** Current-period consumption per budget, so a shared pot can say what is left of it. */
  readonly usage = input<Record<number, BudgetUsage>>({});

  /** The shared pot, if there is one. Its remainder belongs to everybody, not to the reader. */
  private readonly shared = computed(() =>
    this.budgets().find((budget) => budget.scope === 'use_case' && budget.enabled !== false),
  );

  /** What is left of the shared pot, in the metrics it actually limits. */
  protected readonly sharedLeft = computed<{ label: string; value: string }[]>(() => {
    const budget = this.shared();
    const used = budget?.id === undefined ? undefined : this.usage()[budget.id];
    if (!budget || !used) return [];
    const out: { label: string; value: string }[] = [];
    const limitNanos = budget.limit_cost ? nanosOf(budget.limit_cost) : null;
    if (budget.limit_cost && limitNanos !== null && used.used_cost_nanos != null) {
      const left = limitNanos - used.used_cost_nanos;
      out.push({
        label: `of ${storedAmount(budget.limit_cost) ?? budget.limit_cost} spend`,
        value: money(Math.max(0, left), budget.limit_cost, used.used_cost ?? ''),
      });
    }
    if (budget.limit_requests != null && used.used_requests != null) {
      out.push({
        label: `of ${displayCount(budget.limit_requests)} requests`,
        value: displayCount(Math.max(0, budget.limit_requests - used.used_requests)),
      });
    }
    if (budget.limit_tokens != null && used.used_tokens != null) {
      out.push({
        label: `of ${displayCount(budget.limit_tokens)} tokens`,
        value: displayCount(Math.max(0, budget.limit_tokens - used.used_tokens)),
      });
    }
    return out;
  });

  /** The per-head budget this use case has, if any. Only that scope binds one person. */
  private readonly perHead = computed(() =>
    this.budgets().find((budget) => budget.scope === 'each_member' && budget.enabled !== false),
  );

  protected readonly hasAllowance = computed(() => !!this.perHead()?.limit_cost);

  /**
   * The window whose figures are shown: the budget's period where there is one, so a daily
   * allowance is compared with today rather than the month.
   */
  private readonly window = computed(() =>
    this.perHead()?.period === 'day' ? this.today() : this.month(),
  );

  /** Whether the window this panel reads arrived — the other one's fate is not this panel's. */
  protected readonly unavailable = computed(() =>
    this.perHead()?.period === 'day' ? this.todayUnavailable() : this.monthUnavailable(),
  );

  protected readonly people = computed(() => {
    const only = this.only();
    return only === null ? this.window() : this.window().filter((row) => row.key === only);
  });

  /** Whether this panel is about the reader rather than about everybody. */
  protected readonly mine = computed(() => this.only() !== null);

  protected readonly sharedPeriod = computed(() => this.shared()?.period ?? 'month');

  protected readonly windowLabel = computed(
    () => `${this.perHead()?.period === 'day' ? 'today' : 'this month'} (${PERIOD_CLOCK})`,
  );

  /**
   * **Why** this card covers the period it covers, which is not a free choice.
   *
   * The figures sit beside *Left of allowance*, and an allowance only means anything against the
   * period it resets in: comparing a month of spend with a daily cap would print a remainder nobody
   * has. So the window follows the budget — and a reader who finds a card showing one day on the
   * first of a month has met a rule, not a defect. Reported as exactly that, which is why the
   * sentence is on the screen now and not only in this file; the chart above carries the period
   * control for the longer view (`FRD-626` FR-17).
   */
  protected readonly windowReason = computed(() =>
    this.perHead()?.period === 'day'
      ? `Today in ${PERIOD_CLOCK}, because the per-person allowance on this use case resets at
         midnight ${PERIOD_CLOCK} — a remainder is only meaningful against the period it is measured
         in. For a longer view, use the period control on the usage chart above.`
      : `This month in ${PERIOD_CLOCK}, which is the period the per-person allowance on this use case
         is measured in. For a shorter or longer view, use the period control on the usage chart
         above.`,
  );

  protected readonly view = computed<PersonView[]>(() =>
    this.people().map((person) => {
      const budget = this.perHead();
      const limit = budget?.limit_cost ?? null;
      // In integer nano-units, read on the digits, so the remainder stays exact.
      const limitNanos = limit === null ? null : nanosOf(limit);
      const left = limitNanos === null ? null : limitNanos - person.cost_nanos;
      return {
        name: person.key,
        requests: displayCount(person.requests),
        tokens: displayCount(person.total_tokens),
        cost: person.cost,
        signedIn: half(person, 'oidc'),
        viaKey: half(person, 'api_key'),
        allowance: limit === null ? null : (storedAmount(limit) ?? limit),
        // Clamped at zero for reading; running out is carried by `over`.
        remaining: left === null ? null : money(Math.max(0, left), limit ?? '', person.cost),
        over: left !== null && left < 0,
      };
    }),
  );
}
