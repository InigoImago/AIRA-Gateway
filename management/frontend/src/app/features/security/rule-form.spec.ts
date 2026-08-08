import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { AnomalyRule } from '../../core/api/models';
import { NEW_RULE, RuleForm } from './rule-form';

const EXISTING: AnomalyRule = {
  id: 7,
  use_case: 'uc-a',
  is_global: false,
  name: 'too many refusals',
  kind: 'refusal_rate',
  window_minutes: 15,
  threshold: 50,
  parameter: null,
  min_sample: 20,
  action: 'alert',
  target: 'subject',
  action_minutes: null,
  throttle_rpm: null,
  enabled: true,
};

@Component({
  selector: 'app-rule-form-host',
  imports: [RuleForm],
  template: `<app-rule-form
    [rule]="rule()"
    [busy]="busy()"
    formId="f"
    (saved)="saved.set($event)"
    (cancelled)="cancelled.set(true)"
  />`,
})
class Host {
  readonly rule = signal<AnomalyRule>(EXISTING);
  readonly busy = signal(false);
  readonly saved = signal<Partial<AnomalyRule> | null>(null);
  readonly cancelled = signal(false);
}

/**
 * `ngModel` writes its value in a microtask, not during change detection — so a synchronous
 * `detectChanges()` reads the input before the rule has reached it, and would happily "prove" that
 * an edit form opens empty. Everything here awaits.
 */
async function setup(rule: AnomalyRule = EXISTING) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.rule.set(rule);
  // Twice: the form fills its fields from an `effect`, which runs *after* the first render. One
  // pass would read the inputs before the rule had reached them — and would have "proved" that a
  // form opens empty.
  fixture.detectChanges();
  await fixture.whenStable();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    element,
    host: fixture.componentInstance,
    field: (name: string) =>
      element.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-testid="f-${name}"]`),
    set: async (name: string, value: string) => {
      const input = element.querySelector<HTMLInputElement>(`[data-testid="f-${name}"]`)!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
      input.dispatchEvent(new Event('change'));
      fixture.detectChanges();
      await fixture.whenStable();
    },
    submit: () => {
      element.querySelector<HTMLFormElement>('form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();
    },
  };
}

describe('RuleForm — what it offers', () => {
  it('opens with the rule as it is, not with an empty form', async () => {
    const harness = await setup({ ...EXISTING, threshold: 42, window_minutes: 90, min_sample: 7 });

    expect(harness.field('threshold')?.value).toBe('42');
    expect(harness.field('window')?.value).toBe('90');
    expect(harness.field('sample')?.value).toBe('7');
  });

  it('never offers to change the kind of a rule that exists', async () => {
    // A rule's kind decides what its threshold *means* — 50 is half the requests under
    // `refusal_rate` and half a multiple under `spend_spike`. Changing it in place silently
    // reinterprets a number somebody chose deliberately; a different kind is a different rule.
    expect((await setup()).field('kind')).toBeNull();
    expect((await setup()).field('name')).toBeNull();
  });

  it('asks for a name and a kind when the rule does not exist yet', async () => {
    const harness = await setup(NEW_RULE);

    expect(harness.field('name')).not.toBeNull();
    expect(harness.field('kind')).not.toBeNull();
  });

  it('shows the byte figure only for the kind that has one', async () => {
    // `payload_size` needs two numbers and every other kind needs one. Showing the second
    // everywhere would invite a value the engine never reads.
    expect((await setup({ ...EXISTING, kind: 'payload_size' })).field('parameter')).not.toBeNull();
    expect((await setup()).field('parameter')).toBeNull();
  });

  it('asks for a duration and a rate only when the action needs them', async () => {
    const harness = await setup();
    expect(harness.field('minutes')).toBeNull();

    await harness.set('action', 'block');
    expect(harness.field('minutes')).not.toBeNull();
    expect(harness.field('rpm')).toBeNull();

    await harness.set('action', 'throttle');
    // "An enum member is not a specification": a throttle without a rate is not a decision.
    expect(harness.field('rpm')).not.toBeNull();
  });

  it('reloads its fields when it is pointed at another rule', async () => {
    // Otherwise opening a second rule shows the first one's numbers — the zoneless form-state
    // bug this project has already fixed once.
    const harness = await setup();
    harness.host.rule.set({ ...EXISTING, id: 9, threshold: 99, window_minutes: 5 });
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();

    expect(harness.field('threshold')?.value).toBe('99');
    expect(harness.field('window')?.value).toBe('5');
  });
});

describe('RuleForm — what it sends', () => {
  it('sends the kind even though it is not editable', async () => {
    // The server validates the threshold **against the kind**: a request that omitted it would be
    // checked against the default rather than against this rule.
    const harness = await setup();
    await harness.set('threshold', '65');
    harness.submit();

    expect(harness.host.saved()?.kind).toBe('refusal_rate');
    expect(harness.host.saved()?.threshold).toBe(65);
  });

  it('does not carry a duration or a rate an alerting rule cannot use', async () => {
    const harness = await setup({
      ...EXISTING,
      action: 'throttle',
      throttle_rpm: 12,
      action_minutes: 30,
    });
    await harness.set('action', 'alert');
    harness.submit();

    expect(harness.host.saved()?.throttle_rpm).toBeNull();
    expect(harness.host.saved()?.action_minutes).toBeNull();
  });

  it('does not carry a byte figure for a kind that has none', async () => {
    const harness = await setup({ ...EXISTING, parameter: 1000 });
    harness.submit();

    expect(harness.host.saved()?.parameter).toBeNull();
  });

  it('can switch a rule off without deleting it', async () => {
    // Deleting stops the watching *and* loses the intent; switching off keeps the rule for
    // whoever asks later why it is not firing.
    const harness = await setup();
    const box = harness.element.querySelector<HTMLInputElement>('[data-testid="f-enabled"]')!;
    box.click();
    harness.fixture.detectChanges();
    harness.submit();

    expect(harness.host.saved()?.enabled).toBe(false);
  });

  it('will not submit a new rule with no name', async () => {
    const harness = await setup(NEW_RULE);
    harness.submit();

    expect(harness.host.saved()).toBeNull();
  });

  it('will not submit without a threshold or a window', async () => {
    const harness = await setup();
    await harness.set('threshold', '');
    harness.submit();

    expect(harness.host.saved()).toBeNull();
  });

  it('says it is saving, and refuses a second press', async () => {
    // Two saves from one intent is how a form that looks stuck gets pressed twice.
    const harness = await setup();
    harness.host.busy.set(true);
    harness.fixture.detectChanges();

    const save = harness.element.querySelector<HTMLButtonElement>('[data-testid="f-save"]')!;
    expect(save.textContent).toContain('Saving…');
    expect(save.disabled).toBe(true);
  });

  it('reports a cancel rather than swallowing it', async () => {
    const harness = await setup();
    harness.element.querySelector<HTMLButtonElement>('button.btn:not(.btn--primary)')!.click();
    harness.fixture.detectChanges();

    expect(harness.host.cancelled()).toBe(true);
  });
});

describe('RuleForm — a new rule', () => {
  it('starts at the safe end of every axis', async () => {
    // `alert` is the default and that is a safety property (`FRD-500` §3): a system whose first
    // setting is `block` blocks wrongly once and is switched off forever.
    const harness = await setup(NEW_RULE);

    expect((harness.field('action') as HTMLSelectElement).value).toBe('alert');
    expect(harness.field('minutes')).toBeNull();
  });

  it('carries the chosen kind, target and name into what it sends', async () => {
    const harness = await setup(NEW_RULE);
    await harness.set('name', 'runaway spend');
    await harness.set('kind', 'spend_spike');
    await harness.set('threshold', '300');

    harness.submit();

    expect(harness.host.saved()).toMatchObject({
      name: 'runaway spend',
      kind: 'spend_spike',
      threshold: 300,
    });
  });

  it('offers the byte figure as soon as the kind that needs it is chosen', async () => {
    const harness = await setup(NEW_RULE);
    expect(harness.field('parameter')).toBeNull();

    await harness.set('kind', 'payload_size');
    expect(harness.field('parameter')).not.toBeNull();
  });

  it('sends a throttle rate only with the action that uses it', async () => {
    const harness = await setup(NEW_RULE);
    await harness.set('name', 'slow them down');
    await harness.set('action', 'throttle');
    await harness.set('rpm', '12');
    harness.submit();

    expect(harness.host.saved()?.throttle_rpm).toBe(12);
    expect(harness.host.saved()?.action).toBe('throttle');
  });

  it('trims a name rather than saving one with edges nobody can see', async () => {
    const harness = await setup(NEW_RULE);
    await harness.set('name', '  spaced  ');
    harness.submit();

    expect(harness.host.saved()?.name).toBe('spaced');
  });
});

describe('RuleForm — the fields a kind does not have', () => {
  it('offers no byte figure once the kind changes away from the one that needs it', async () => {
    const harness = await setup(NEW_RULE);
    await harness.set('kind', 'payload_size');
    expect(harness.field('parameter')).not.toBeNull();

    await harness.set('kind', 'refusal_rate');
    expect(harness.field('parameter')).toBeNull();
  });

  it('keeps a smallest sample of zero rather than inventing one', async () => {
    // Zero is a real answer: "judge this on any number of requests". Replacing it with a default
    // would quietly stop a rule firing on the small samples somebody chose to include.
    const harness = await setup({ ...EXISTING, min_sample: 0 });
    harness.submit();

    expect(harness.host.saved()?.min_sample).toBe(0);
  });

  it('does not lose a block duration when the action stays a block', async () => {
    const harness = await setup({ ...EXISTING, action: 'block', action_minutes: 30 });
    harness.submit();

    expect(harness.host.saved()?.action_minutes).toBe(30);
  });
});
