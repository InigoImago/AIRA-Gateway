import { Component, computed, input } from '@angular/core';
import { Budget, PersonRow } from '../../core/api/models';
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
 * What each person consumed in one use case (`FRD-606`).
 *
 * Reported by the owner: *"a component I am missing is an overview of what a person in the use
 * case has used up, in tokens and in money, for both the API key and the Keycloak sign-in, even
 * when there is no budget limit; where there is one, it should also be visible how much is left."*
 *
 * Three things had to be true for that sentence and none of them were:
 *
 * 1. **One person, not two rows.** The two credentials answer "who is this" in different
 *    alphabets — an OIDC token's subject is the directory's user id, an API key's is its owner's
 *    username — so the same human appeared twice under two unrecognisable keys. The gateway now
 *    writes the name beside the subject and groups on it (`by_person`), while `by_member` keeps
 *    the subject, because that is what every counter and every budget is keyed on.
 * 2. **Figures without a limit.** `FRD-603` fixed exactly this shape one level up: consumption was
 *    rendered only as a fraction of a limit, so whatever had no limit had no figure at all.
 * 3. **What is left, where there is a limit.** An `each_member` budget names nobody: one row, one
 *    counter per head, so its allowance is the same for everybody and the remainder is arithmetic
 *    this panel can do — see `remainder`.
 *
 * A panel, not a block in the parent: the page owns the load, the panel owns the rendering
 * (`CLAUDE.md` §3). It has no mutations, so it reports nothing through the page banner; every
 * state it can be in is a statement about the figures and is said here.
 */
@Component({
  selector: 'app-people-panel',
  imports: [InfoHint],
  template: `
    <div class="card">
      <div class="spread">
        <h3 class="section-title" style="margin: 0">
          What each person used
          <app-info-hint label="What each person used" testid="people-consumption" [wide]="true">
            Recorded requests for this use case, grouped by person. Somebody who calls with an API
            key and also signs in to the console is one row here, with the two halves shown — the
            gateway records both under the name they were known by. A row with no name is a
            credential that carried none; it is shown as its own subject rather than folded into
            somebody else.
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
        <p class="empty" data-testid="people-empty">
          Nobody has called this use case in this period.
        </p>
      } @else {
        <div class="table-wrap" style="margin-top: 1rem">
          <table class="table">
            <thead>
              <tr>
                <th scope="col">Person</th>
                <th scope="col">Requests</th>
                <th scope="col">Tokens</th>
                <th scope="col">Spend ($)</th>
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
                    <!--
                      Said under the name rather than in two more columns: the split is the answer
                      to "where is this coming from", and a reader who does not have that question
                      should not have to scan past two columns to reach the spend.
                    -->
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
    </div>
  `,
  styles: `
    /* A column heading shouts, because a column heading should. A row header is a person's name,
       and a name in small uppercase letters is a name somebody has to decode. */
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
  readonly today = input<PersonRow[]>([]);
  readonly budgets = input<Budget[]>([]);
  readonly unavailable = input(false);
  readonly reason = input('');

  /** The per-head budget this use case has, if any. Only that scope binds one person. */
  private readonly perHead = computed(() =>
    this.budgets().find((budget) => budget.scope === 'each_member' && budget.enabled !== false),
  );

  protected readonly hasAllowance = computed(() => !!this.perHead()?.limit_cost);

  /**
   * The window whose figures are shown.
   *
   * The **budget's** period where there is one, so the remainder beside a daily allowance is
   * today's consumption rather than the month's — otherwise the column would say a person is over
   * an allowance that resets every night.
   */
  protected readonly people = computed(() =>
    this.perHead()?.period === 'day' ? this.today() : this.month(),
  );

  protected readonly windowLabel = computed(() =>
    this.perHead()?.period === 'day' ? 'today' : 'this month',
  );

  protected readonly view = computed<PersonView[]>(() =>
    this.people().map((person) => {
      const budget = this.perHead();
      const limit = budget?.limit_cost ?? null;
      // **In nano-units**, like every other money figure that is not being *shown*. Subtracting
      // two floating-point dollar amounts is how a remainder comes to read `0.009999999999`, and
      // `cost_nanos` is an integer precisely so this arithmetic stays exact.
      const limitNanos = limit === null ? null : Math.round(Number(limit) * 1_000_000_000);
      const left = limitNanos === null ? null : limitNanos - person.cost_nanos;
      return {
        name: person.key,
        requests: person.requests,
        tokens: person.total_tokens,
        cost: person.cost,
        signedIn: this.half(person, 'oidc'),
        viaKey: this.half(person, 'api_key'),
        allowance: limit === null ? null : this.trimmed(limit),
        // Clamped at zero for reading, and the fact that it ran out is carried by `over` instead.
        // A negative "left" is arithmetic showing through: nobody is owed minus three dollars.
        remaining: left === null ? null : this.money(Math.max(0, left), limit ?? '', person.cost),
        over: left !== null && left < 0,
      };
    }),
  );

  /** An amount without its trailing zeros: `0.010000` reads as `0.01`. */
  private trimmed(value: string): string {
    const places = this.decimals(value);
    return Number(value).toFixed(Math.max(2, places));
  }

  /**
   * Nano-units as a decimal string, in the precision the figures beside it already use.
   *
   * Two decimals hid the whole answer on the installation this was first looked at: an allowance
   * of `0.01` against a spend of `0.0003` reported `0.01 of 0.01` — a remainder that says nothing
   * was used. The precision is taken from the numbers on screen rather than fixed, because
   * inventing decimals is as wrong as dropping them: a figure shown to six places implies it was
   * measured to six.
   */
  private money(nanos: number, ...alongside: string[]): string {
    const places = Math.max(2, ...alongside.map((value) => this.decimals(value)));
    return (nanos / 1_000_000_000).toFixed(Math.min(places, 9));
  }

  /**
   * Significant decimals in an amount, ignoring trailing zeros.
   *
   * A limit is stored as a decimal with a fixed scale, so `0.01` arrives as `0.010000` — six
   * places of nothing. Counting them made the remainder six places wide beside a spend written to
   * four, which is precision the figure does not have.
   */
  private decimals(value: string): number {
    return (value.split('.')[1] ?? '').replace(/0+$/, '').length;
  }

  /**
   * One half of a person's spend, or `null` when that credential did not call at all.
   *
   * **Spend or tokens count as calling, not only the request counter.** Written as
   * `requests > 0` first, and the live stack showed what that hides: a pipeline step's model call
   * is recorded with no request against it (`FRD-125` FR-9), so somebody whose only traffic that
   * month went through a classifier had a half with real money in it and no row saying so. The
   * question this column answers is "where did this come from", and the answer is the spend.
   */
  private half(person: PersonRow, method: string): string | null {
    const part = person.by_method?.[method];
    if (!part || (part.requests === 0 && part.total_tokens === 0 && part.cost_nanos === 0)) {
      return null;
    }
    return `${part.cost} / ${part.requests} req`;
  }
}
