import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Budget, PersonRow } from '../../core/api/models';
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
    [month]="month()"
    [today]="today()"
    [budgets]="budgets()"
    [unavailable]="unavailable()"
    reason="The gateway could not be reached."
  />`,
})
class Host {
  readonly month = signal<PersonRow[]>([]);
  readonly today = signal<PersonRow[]>([]);
  readonly budgets = signal<Budget[]>([]);
  readonly unavailable = signal(false);
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
    // `FRD-603`'s rule, one level down: consumption used to be rendered only as a fraction of a
    // limit, so a use case without one showed neither the tokens nor the money it had spent.
    const harness = setup();
    harness.host.month.set([row({ key: 'erika', total_tokens: 900, cost: '12.34', requests: 7 })]);
    harness.render();

    expect(harness.text()).toContain('erika');
    expect(harness.text()).toContain('900');
    expect(harness.text()).toContain('12.34');
    expect(harness.text()).toContain('7');
  });

  it('says which half came from a sign-in and which from a key', () => {
    // The reported ask in one line: *"for both the API key and the Keycloak sign-in"*. One row,
    // two halves — because before this the same person was two rows under two keys, one of them a
    // Keycloak uuid nobody recognises.
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
    // Found on the live stack, not in this file: a pipeline step's model call is recorded with no
    // request against it (`FRD-125` FR-9), so a person whose only traffic that month went through
    // a classifier had a half with real money in it and no row saying so. The first version of
    // this hid it behind `requests > 0`.
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
    // Seen on the live stack: an allowance of 0.01 against a spend of 0.0003 reported
    // "0.01 of 0.01" — a remainder that says nothing was used. Two decimals is not a rounding
    // choice there, it is the whole answer disappearing.
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
