import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { AnomalyRule } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { RulesTab } from './rules-tab';

const RULE: AnomalyRule = {
  id: 4,
  use_case: 'uc-a',
  is_global: false,
  name: 'too many refusals',
  kind: 'refusal_rate',
  window_minutes: 15,
  threshold: 50,
  parameter: null,
  min_sample: 20,
  action: 'block',
  target: 'subject',
  action_minutes: 30,
  throttle_rpm: null,
  enabled: true,
};

/** The page around the tab: it loads the list and re-reads it when the tab says so. */
@Component({
  selector: 'app-rules-host',
  imports: [RulesTab],
  template: `<app-rules-tab
    [slug]="slug()"
    [canManage]="canManage()"
    [rules]="rules()"
    [failed]="failed()"
    (changed)="changes.set(changes() + 1)"
  />`,
  providers: [PageFeedback],
})
class Host {
  readonly slug = signal('uc-a');
  readonly canManage = signal(true);
  readonly rules = signal<AnomalyRule[] | null>([RULE]);
  readonly failed = signal(false);
  readonly changes = signal(0);
}

interface Options {
  rules?: AnomalyRule[] | null;
  failed?: boolean;
  canManage?: boolean;
  confirmAnswer?: boolean;
  save?: Observable<AnomalyRule>;
}

async function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const asked: string[] = [];
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [
      {
        provide: UseCaseService,
        useValue: {
          saveUseCaseRule: (slug: string, rule: Partial<AnomalyRule>) => {
            calls.push(`save:${slug}:${JSON.stringify(rule)}`);
            return options.save ?? of(RULE);
          },
          createGlobalRule: (rule: Partial<AnomalyRule>) => {
            calls.push(`global:${JSON.stringify(rule)}`);
            return of(RULE);
          },
          deleteUseCaseRule: (slug: string, id: number) => {
            calls.push(`delete:${slug}:${id}`);
            return of(undefined);
          },
        },
      },
      {
        provide: ConfirmService,
        useValue: {
          ask: (question: string) => {
            asked.push(question);
            return options.confirmAnswer ?? true;
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.canManage.set(options.canManage ?? true);
  if (options.rules !== undefined) fixture.componentInstance.rules.set(options.rules);
  fixture.componentInstance.failed.set(options.failed ?? false);
  fixture.detectChanges();
  await fixture.whenStable();
  const element = fixture.nativeElement as HTMLElement;
  const settle = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
  };
  return {
    fixture,
    calls,
    asked,
    element,
    host: fixture.componentInstance,
    feedback: () => fixture.debugElement.injector.get(PageFeedback),
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: async (selector: string) => {
      element.querySelector<HTMLElement>(selector)?.click();
      await settle();
    },
    /** Type into a field the way a person does: the value, then the input event. */
    type: async (testid: string, value: string) => {
      const field = element.querySelector<HTMLInputElement>(`[data-testid="${testid}"]`)!;
      field.value = value;
      field.dispatchEvent(new Event('input'));
      await settle();
    },
  };
}

/** Open "+ New rule", name it, and press its Save. */
async function createNamed(harness: Awaited<ReturnType<typeof setup>>, name: string) {
  await harness.click('[data-testid="rule-add"]');
  await harness.type('new-rule-name', name);
  await harness.click('[data-testid="new-rule-save"]');
}

describe('RulesTab', () => {
  it('exists at all — the console pointed here before this did', async () => {
    // The security console said a use-case rule "is changed on that use case", and there was no
    // such screen. An instruction with no destination is the `FRD-206` defect one level of
    // indirection further out.
    const harness = await setup();

    expect(harness.text()).toContain('too many refusals');
  });

  it('says what each rule does, not just its kind', async () => {
    const harness = await setup();
    expect(harness.text()).toContain('refused');
    expect(harness.text()).toContain('per caller');
  });

  it('offers editing to whoever manages the use case, and explains the absence', async () => {
    const allowed = await setup({ canManage: true });
    expect(allowed.testid('uc-rule-edit-4')).not.toBeNull();
    expect(allowed.testid('rule-add')).not.toBeNull();

    const readOnly = await setup({ canManage: false });
    expect(readOnly.testid('uc-rule-edit-4')).toBeNull();
    expect(readOnly.testid('rule-add')).toBeNull();
    expect(readOnly.testid('rules-readonly')?.textContent).toContain('administers this use case');
  });

  it('creates a rule through the use case, not through the global endpoint', async () => {
    // The two endpoints differ in who may write to them. Posting a use-case rule to the global
    // one would be refused for exactly the people this screen exists for. Submitted through the
    // form, because a form that is only opened cannot tell the two endpoints apart.
    const harness = await setup();
    await createNamed(harness, 'slow upstream');

    expect(harness.calls.some((call) => call.startsWith('save:uc-a:'))).toBe(true);
    expect(harness.calls.some((call) => call.startsWith('global:'))).toBe(false);
    expect(harness.calls.find((call) => call.startsWith('save:'))).toContain('"slow upstream"');
    // The page reads its list again, which is where the new rule and the count come from.
    expect(harness.host.changes()).toBe(1);
    expect(harness.feedback().notice()).toContain('"slow upstream" created');
  });

  it('asks before deleting, and says what stops being watched', async () => {
    const declined = await setup({ confirmAnswer: false });
    await declined.click('[data-testid="uc-rule-delete-4"]');
    expect(declined.calls.filter((call) => call.startsWith('delete:'))).toEqual([]);

    const accepted = await setup({ confirmAnswer: true });
    await accepted.click('[data-testid="uc-rule-delete-4"]');
    expect(accepted.calls).toContain('delete:uc-a:4');
    expect(accepted.host.changes()).toBe(1);
  });

  it('says an empty list means global rules may still apply', async () => {
    // A quiet detector and an absent one look identical from a findings list. The place that
    // knows the difference has to say it — and "nothing here" is not the same as "nothing at all".
    const harness = await setup({ rules: [] });

    expect(harness.testid('no-rules')?.textContent).toContain('global rules');
  });

  it('does not claim nothing is watched when the rules could not be loaded', async () => {
    // The empty state is a statement about the rules; after a failed load nobody has read them.
    const harness = await setup({ rules: null, failed: true });

    expect(harness.testid('no-rules')).toBeNull();
    expect(harness.testid('rules-unavailable')).not.toBeNull();
    expect(harness.element.querySelector('.loading')).toBeNull();
  });

  it('says it is loading until the page has the list', async () => {
    const harness = await setup({ rules: null });

    expect(harness.element.querySelector('.loading')).not.toBeNull();
    expect(harness.testid('no-rules')).toBeNull();
  });
});

/**
 * "+ New rule" with an existing rule's name. The server upserts by name, so this replaces that
 * rule — kind, threshold, action and all — and used to say "created" while doing it.
 */
describe('RulesTab — a new rule named like an existing one', () => {
  it('asks first, naming what is replaced, and sends nothing when the answer is no', async () => {
    const harness = await setup({ confirmAnswer: false });
    await createNamed(harness, 'too many refusals');

    expect(harness.asked.at(-1)).toContain('"too many refusals" already exists');
    expect(harness.asked.at(-1)).toContain('block');
    expect(harness.calls.filter((call) => call.startsWith('save:'))).toEqual([]);
  });

  it('says "replaced", not "created", when the answer is yes', async () => {
    const harness = await setup({ confirmAnswer: true });
    await createNamed(harness, 'too many refusals');

    expect(harness.calls.some((call) => call.startsWith('save:uc-a:'))).toBe(true);
    expect(harness.feedback().notice()).toContain('"too many refusals" replaced');
  });

  it('asks nothing for a name that is new', async () => {
    const harness = await setup();
    await createNamed(harness, 'a new name');

    expect(harness.asked).toEqual([]);
  });

  it('asks nothing when an existing rule is edited, which is a save of that same rule', async () => {
    const harness = await setup();
    await harness.click('[data-testid="uc-rule-edit-4"]');
    await harness.click('[data-testid="rule-4-save"]');

    expect(harness.asked).toEqual([]);
    expect(harness.feedback().notice()).toContain('saved');
  });
});

describe('RulesTab — a refused save', () => {
  const refusal = () =>
    throwError(() => ({ status: 400, error: { error: { message: 'threshold: above 100.' } } }));

  it('says why inside the "new rule" window', async () => {
    const harness = await setup({ save: refusal() });
    await createNamed(harness, 'too high');

    const said = harness.testid('rule-editor-error');
    expect(said?.textContent).toContain('threshold: above 100.');
    expect(said?.closest('[data-testid="rule-editor"]')).not.toBeNull();
  });

  it('says why beside the rule being edited', async () => {
    const harness = await setup({ save: refusal() });
    await harness.click('[data-testid="uc-rule-edit-4"]');
    await harness.click('[data-testid="rule-4-save"]');

    expect(harness.testid('rule-4-error')?.textContent).toContain('threshold: above 100.');
  });

  it('opens a form without an earlier failure that is not its own', async () => {
    const harness = await setup();
    harness.feedback().fail({ status: 500 }, 'Could not load the members.');
    await harness.click('[data-testid="rule-add"]');

    expect(harness.testid('rule-editor-error')).toBeNull();
  });
});

describe('RulesTab — reading a rule at a glance', () => {
  it('reads a spike threshold as the multiple it means, never as the stored percent', async () => {
    // Stored as percent of the previous window: 300 is three times as much. Beside a spend rule a
    // bare "300" reads as an amount, and "300 ×" as three hundred times.
    const harness = await setup({ rules: [{ ...RULE, kind: 'spend_spike', threshold: 300 }] });
    const cell = harness.element.querySelector('tbody tr td:nth-child(3)')?.textContent ?? '';

    expect(cell).toContain('3× the previous window');
    expect(cell).not.toContain('300');
  });

  it('names the unit a share of requests is counted in', async () => {
    const harness = await setup();
    expect(harness.element.querySelector('tbody tr td:nth-child(3)')?.textContent).toContain(
      '50 % of requests',
    );
  });

  it('says when a rule is switched off, so it is not read as watching', async () => {
    const harness = await setup({ rules: [{ ...RULE, enabled: false }] });
    expect(harness.text()).toContain('off');
  });

  it('shows what a blocking rule takes away', async () => {
    const harness = await setup();

    expect(harness.text()).toContain('block');
    expect(harness.text()).toContain('stopped for 30 minutes');
  });

  it('closes the form when the edit is abandoned', async () => {
    const harness = await setup();
    await harness.click('[data-testid="uc-rule-edit-4"]');
    expect(harness.element.querySelector('app-rule-form')).not.toBeNull();

    const cancel = [...harness.element.querySelectorAll<HTMLButtonElement>('app-rule-form button')]
      .filter((button) => button.textContent?.trim() === 'Cancel')
      .at(0);
    cancel?.click();
    harness.fixture.detectChanges();

    expect(harness.element.querySelector('app-rule-form')).toBeNull();
  });

  it('says the change takes a moment to reach the gateway', async () => {
    const harness = await setup();
    await harness.click('[data-testid="uc-rule-edit-4"]');
    await harness.click('[data-testid="rule-4-save"]');

    expect(harness.feedback().notice()).toContain('few seconds');
  });
});
