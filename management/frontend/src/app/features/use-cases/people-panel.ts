import { Component, computed, inject, input } from '@angular/core';
import { MeService, unitSuffix } from '../../core/api/me.service';
import { Budget, BudgetUsage, PersonRow } from '../../core/api/models';
import { InfoHint } from '../../core/ui/info-hint';

/** One person's row, ready to render: their totals, the two halves, and what is left. */
interface PersonView {
  name: string;
  requests: number;
  tokens: number;
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

/**
 * Significant decimals in an amount, ignoring trailing zeros: a limit stored as `0.010000` has
 * two, not six.
 */
function decimals(value: string): number {
  return (value.split('.')[1] ?? '').replace(/0+$/, '').length;
}

/** An amount without its trailing zeros: `0.010000` reads as `0.01`. */
function trimmed(value: string): string {
  return Number(value).toFixed(Math.max(2, decimals(value)));
}

/**
 * Nano-units as a decimal string, in the precision the figures beside it use. Fixed at two places
 * a small remainder vanishes; more places than those shown would claim precision never measured.
 */
function money(nanos: number, ...alongside: string[]): string {
  const places = Math.max(2, ...alongside.map((value) => decimals(value)));
  return (nanos / 1_000_000_000).toFixed(Math.min(places, 9));
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
        <h3 class="section-title" style="margin: 0">
          {{ mine() ? 'What you used' : 'What each person used' }}
          <app-info-hint label="What each person used" testid="people-consumption" [wide]="true">
            Recorded requests for this use case. Somebody who calls with an API key and also signs
            in to the console is one row here, with the two halves shown — the gateway records both
            under the name they were known by. Traffic from before that name was recorded is listed
            under its own subject rather than folded into somebody it probably belonged to.
          </app-info-hint>
        </h3>
        <span class="muted" style="font-size: 0.85rem">{{ windowLabel() }}</span>
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
                      <div class="muted" style="font-size: 0.78rem">
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
        <p class="muted" style="margin: 0.75rem 0 0; font-size: 0.85rem" data-testid="shared-left">
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
      font-size: 0.9rem;
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
  `,
})
export class PeoplePanel {
  /** Consumption over the month, and over today — the budget's period decides which is used. */
  readonly month = input<PersonRow[]>([]);
  private readonly currency = inject(MeService).currency;
  protected readonly unit = computed(() => unitSuffix(this.currency()));
  readonly today = input<PersonRow[]>([]);
  readonly budgets = input<Budget[]>([]);
  readonly unavailable = input(false);
  readonly reason = input('');
  /**
   * Narrow the panel to one person — the signed-in reader, on the use-case overview. The same
   * component, so the remainder arithmetic exists once and the two views cannot disagree.
   */
  readonly only = input<string | null>(null);
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
    if (budget.limit_cost && used.used_cost_nanos != null) {
      const left = Math.round(Number(budget.limit_cost) * 1_000_000_000) - used.used_cost_nanos;
      out.push({
        label: `of ${trimmed(budget.limit_cost)} spend`,
        value: money(Math.max(0, left), budget.limit_cost, used.used_cost ?? ''),
      });
    }
    if (budget.limit_requests != null && used.used_requests != null) {
      out.push({
        label: `of ${budget.limit_requests} requests`,
        value: `${Math.max(0, budget.limit_requests - used.used_requests)}`,
      });
    }
    if (budget.limit_tokens != null && used.used_tokens != null) {
      out.push({
        label: `of ${budget.limit_tokens} tokens`,
        value: `${Math.max(0, budget.limit_tokens - used.used_tokens)}`,
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

  protected readonly people = computed(() => {
    const only = this.only();
    return only === null ? this.window() : this.window().filter((row) => row.key === only);
  });

  /** Whether this panel is about the reader rather than about everybody. */
  protected readonly mine = computed(() => this.only() !== null);

  protected readonly sharedPeriod = computed(() => this.shared()?.period ?? 'month');

  protected readonly windowLabel = computed(() =>
    this.perHead()?.period === 'day' ? 'today' : 'this month',
  );

  protected readonly view = computed<PersonView[]>(() =>
    this.people().map((person) => {
      const budget = this.perHead();
      const limit = budget?.limit_cost ?? null;
      // In integer nano-units, so the remainder stays exact.
      const limitNanos = limit === null ? null : Math.round(Number(limit) * 1_000_000_000);
      const left = limitNanos === null ? null : limitNanos - person.cost_nanos;
      return {
        name: person.key,
        requests: person.requests,
        tokens: person.total_tokens,
        cost: person.cost,
        signedIn: half(person, 'oidc'),
        viaKey: half(person, 'api_key'),
        allowance: limit === null ? null : trimmed(limit),
        // Clamped at zero for reading; running out is carried by `over`.
        remaining: left === null ? null : money(Math.max(0, left), limit ?? '', person.cost),
        over: left !== null && left < 0,
      };
    }),
  );
}
