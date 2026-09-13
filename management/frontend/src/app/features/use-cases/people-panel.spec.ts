import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Budget, BudgetUsage, PersonRow } from '../../core/api/models';
import { PeoplePanel } from './people-panel';

function row(over: Partial<PersonRow> & { key: string }): PersonRow {
  return {
    requests: 1,
    prompt_tokens: 10,
    completion_tokens: 5,
    total_tokens: 15,
    cost_nanos: 1_000_000_000,
    cost: '1.00',
    cached_input_tokens: 0,
    unpriced_requests: 0,
    failed_requests: 0,
    avg_latency_ms: null,
    max_latency_ms: null,
    ...over,
  } as PersonRow;
}

@Component({
  imports: [PeoplePanel],
  template: `<app-people-panel
    [only]="only()"
    [month]="month()"
    [today]="today()"
    [budgets]="budgets()"
    [usage]="usage()"
    [unavailable]="unavailable()"
    reason="The gateway could not be reached."
  />`,
})
class Host {
  readonly month = signal<PersonRow[]>([]);
  readonly today = signal<PersonRow[]>([]);
  readonly budgets = signal<Budget[]>([]);
  readonly unavailable = signal(false);
  readonly only = signal<string | null>(null);
  readonly usage = signal<Record<number, BudgetUsage>>({});
}

function setup() {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  return {
    fixture,
    host: fixture.componentInstance,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    render: () => fixture.detectChanges(),
  };
}

describe('PeoplePanel', () => {
  it('shows tokens and money per person without any budget at all', () => {
    // `FRD-603`'s rule, one level down: figures exist whether or not a limit does.
    const harness = setup();
    harness.host.month.set([row({ key: 'erika', total_tokens: 900, cost: '12.34', requests: 7 })]);
    harness.render();

    expect(harness.text()).toContain('erika');
    expect(harness.text()).toContain('900');
    expect(harness.text()).toContain('12.34');
    expect(harness.text()).toContain('7');
  });

  it('says which half came from a sign-in and which from a key', () => {
    // One row per person, with the sign-in and the key as two halves.
    const harness = setup();
    harness.host.month.set([
      row({
        key: 'erika',
        by_method: {
          oidc: row({ key: 'erika', cost: '4.00', requests: 2 }),
          api_key: row({ key: 'erika', cost: '8.34', requests: 5 }),
        },
      }),
    ]);
    harness.render();

    expect(harness.text()).toContain('signed in: 4.00 / 2 req');
    expect(harness.text()).toContain('API key: 8.34 / 5 req');
  });

  it('leaves out a half that never called', () => {
    // An empty "API key: 0.00 / 0 req" reads as a credential that exists and did nothing, which
    // is a different fact from a person who has no key.
    const harness = setup();
    harness.host.month.set([
      row({ key: 'erika', by_method: { oidc: row({ key: 'erika', cost: '4.00', requests: 2 }) } }),
    ]);
    harness.render();

    expect(harness.text()).toContain('signed in:');
    expect(harness.text()).not.toContain('API key:');
  });

  it('shows a half that spent money without making a request', () => {
    // A pipeline step's model call is recorded with no request against it (`FRD-125` FR-9), so
    // spend alone counts as calling.
    const harness = setup();
    harness.host.month.set([
      row({
        key: 'erika',
        by_method: {
          oidc: row({ key: 'erika', requests: 0, cost: '0.0002', cost_nanos: 200_000 }),
        },
      }),
    ]);
    harness.render();

    expect(harness.text()).toContain('signed in: 0.0002 / 0 req');
  });

  it('shows what is left of a per-head allowance', () => {
    const harness = setup();
    harness.host.budgets.set([{ scope: 'each_member', period: 'month', limit_cost: '20.00' }]);
    harness.host.month.set([row({ key: 'erika', cost_nanos: 12_000_000_000, cost: '12.00' })]);
    harness.render();

    expect(harness.text()).toContain('8.00');
    expect(harness.text()).toContain('of 20.00');
  });

  it('reads a daily allowance against today, not against the month', () => {
    // Otherwise the column reports somebody as over an allowance that resets every night — a
    // confident statement about a limit that is not the one being enforced.
    const harness = setup();
    harness.host.budgets.set([{ scope: 'each_member', period: 'day', limit_cost: '5.00' }]);
    harness.host.month.set([row({ key: 'erika', cost_nanos: 90_000_000_000, cost: '90.00' })]);
    harness.host.today.set([row({ key: 'erika', cost_nanos: 1_000_000_000, cost: '1.00' })]);
    harness.render();

    expect(harness.text()).toContain('today');
    expect(harness.text()).toContain('4.00');
    expect(harness.text()).not.toContain('90.00');
  });

  it('keeps the precision the figures beside it use', () => {
    // At a fixed two decimals, 0.01 minus 0.0003 would read "0.01 of 0.01" — nothing used.
    const harness = setup();
    // The limit arrives as a fixed-scale decimal — `0.010000` — so the width comes from the
    // significant places on both sides, not from the storage format.
    harness.host.budgets.set([{ scope: 'each_member', period: 'month', limit_cost: '0.010000' }]);
    harness.host.month.set([row({ key: 'erika', cost_nanos: 300_000, cost: '0.0003' })]);
    harness.render();

    expect(harness.text()).toContain('0.0097');
    expect(harness.text()).toContain('of 0.01');
    expect(harness.text()).not.toContain('0.010000');
  });

  it('never reports a negative remainder', () => {
    // Nobody is owed minus three dollars. That it ran out is carried by the marking instead.
    const harness = setup();
    harness.host.budgets.set([{ scope: 'each_member', period: 'month', limit_cost: '5.00' }]);
    harness.host.month.set([row({ key: 'erika', cost_nanos: 8_000_000_000, cost: '8.00' })]);
    harness.render();

    expect(harness.text()).toContain('0.00');
    expect(harness.text()).not.toContain('-3.00');
    expect((harness.fixture.nativeElement as HTMLElement).querySelector('.is-over')).not.toBeNull();
  });

  it('shows no allowance column when only the whole use case is budgeted', () => {
    // A `use_case` budget is a shared pot; dividing it by head would invent an allowance nobody
    // configured — and the first caller to arrive can legitimately spend all of it.
    const harness = setup();
    harness.host.budgets.set([{ scope: 'use_case', period: 'month', limit_cost: '20.00' }]);
    harness.host.month.set([row({ key: 'erika' })]);
    harness.render();

    expect(harness.text()).not.toContain('Left of allowance');
  });

  it('says nothing rather than zeroes when the figures did not arrive', () => {
    // `FRD-603`: unknown is never rendered as zero. A table of zeroes states that nobody used
    // anything, which is not what a failed request said.
    const harness = setup();
    harness.host.unavailable.set(true);
    harness.render();

    expect(harness.text()).toContain('did not arrive');
    expect(harness.text()).toContain('The gateway could not be reached.');
  });

  it('tells an empty period apart from a missing one', () => {
    const harness = setup();
    harness.render();

    expect(harness.text()).toContain('Nobody has called this use case');
  });
});

describe('PeoplePanel — narrowed to the reader', () => {
  it('shows only my row, and says so', () => {
    // The same panel as the members tab, so the remainder arithmetic exists once.
    const harness = setup();
    harness.host.only.set('erika');
    harness.host.month.set([
      row({ key: 'erika', cost: '3.00' }),
      row({ key: 'ahmed', cost: '9.00' }),
    ]);
    harness.render();

    expect(harness.text()).toContain('What you used');
    expect(harness.text()).toContain('3.00');
    expect(harness.text()).not.toContain('ahmed');
    expect(harness.text()).not.toContain('9.00');
  });

  it('tells "you did not call" apart from "nobody did"', () => {
    // "Nobody has called" would be a statement about everybody, made from one person's row.
    const harness = setup();
    harness.host.only.set('erika');
    harness.host.month.set([row({ key: 'ahmed' })]);
    harness.render();

    expect(harness.text()).toContain('You have not called this use case');
    expect(harness.text()).not.toContain('Nobody has called');
  });

  it("computes my remaining allowance exactly as it does everybody else's", () => {
    const harness = setup();
    harness.host.only.set('erika');
    harness.host.budgets.set([{ scope: 'each_member', period: 'month', limit_cost: '20.00' }]);
    harness.host.month.set([row({ key: 'erika', cost_nanos: 12_000_000_000, cost: '12.00' })]);
    harness.render();

    expect(harness.text()).toContain('8.00');
    expect(harness.text()).toContain('of 20.00');
  });
});

describe('PeoplePanel — the shared pot', () => {
  it('says what is left of a use-case budget, as shared', () => {
    // A `use_case` budget is one shared pot, so its remainder is the use case's, not the reader's.
    const harness = setup();
    harness.host.only.set('erika');
    harness.host.budgets.set([
      { id: 7, scope: 'use_case', period: 'day', limit_requests: 500, limit_cost: '2.00' },
    ]);
    harness.host.usage.set({
      7: {
        id: 7,
        used_tokens: 800,
        used_requests: 6,
        used_cost_nanos: 500_000_000,
        used_cost: '0.50',
        unpriced_requests: 0,
      },
    });
    harness.host.month.set([row({ key: 'erika' })]);
    harness.render();

    expect(harness.text()).toContain('494 of 500 requests');
    expect(harness.text()).toContain('1.50 of 2.00 spend');
    expect(harness.text()).toContain('shared with everybody');
  });

  it('says nothing about a pot whose figures have not arrived', () => {
    // Unknown is not zero (`FRD-603`). Asserted on the line's absence, not its wording, so a made-up
    // remainder under another label cannot pass.
    const harness = setup();
    harness.host.only.set('erika');
    harness.host.budgets.set([{ id: 7, scope: 'use_case', period: 'day', limit_requests: 500 }]);
    harness.host.month.set([row({ key: 'erika' })]);
    harness.render();

    const line = (harness.fixture.nativeElement as HTMLElement).querySelector(
      '[data-testid="shared-left"]',
    );
    expect(line).toBeNull();
  });

  it('does not offer a shared remainder where the budget is per head', () => {
    // With its usage present, so the line is absent because of the scope, not for lack of figures.
    const harness = setup();
    harness.host.only.set('erika');
    harness.host.budgets.set([
      { id: 8, scope: 'each_member', period: 'month', limit_cost: '20.00' },
    ]);
    harness.host.usage.set({
      8: {
        id: 8,
        used_tokens: 10,
        used_requests: 1,
        used_cost_nanos: 1_000_000_000,
        used_cost: '1.00',
        unpriced_requests: 0,
      },
    });
    harness.host.month.set([row({ key: 'erika' })]);
    harness.render();

    const line = (harness.fixture.nativeElement as HTMLElement).querySelector(
      '[data-testid="shared-left"]',
    );
    expect(line).toBeNull();
    // …and the per-head column is the one that answers here.
    expect(harness.text()).toContain('Left of allowance');
  });
});
