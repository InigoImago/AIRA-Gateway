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
  action: 'alert',
  target: 'subject',
  action_minutes: null,
  throttle_rpm: null,
  enabled: true,
};

@Component({
  selector: 'app-rules-host',
  imports: [RulesTab],
  template: `<app-rules-tab
    [slug]="slug()"
    [canManage]="canManage()"
    (countChanged)="count.set($event)"
  />`,
  providers: [PageFeedback],
})
class Host {
  readonly slug = signal('uc-a');
  readonly canManage = signal(true);
  readonly count = signal(-1);
}

interface Options {
  rules?: Observable<AnomalyRule[]>;
  canManage?: boolean;
  confirmAnswer?: boolean;
  save?: Observable<AnomalyRule>;
}

async function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [
      {
        provide: UseCaseService,
        useValue: {
          useCaseRules: (slug: string) => {
            calls.push(`list:${slug}`);
            return options.rules ?? of([RULE]);
          },
          saveUseCaseRule: (slug: string, rule: Partial<AnomalyRule>) => {
            calls.push(`save:${slug}:${JSON.stringify(rule)}`);
            return options.save ?? of(RULE);
          },
          deleteUseCaseRule: (slug: string, id: number) => {
            calls.push(`delete:${slug}:${id}`);
            return of(undefined);
          },
        },
      },
      { provide: ConfirmService, useValue: { ask: () => options.confirmAnswer ?? true } },
    ],
  });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.canManage.set(options.canManage ?? true);
  fixture.detectChanges();
  await fixture.whenStable();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    calls,
    element,
    host: fixture.componentInstance,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: async (selector: string) => {
      element.querySelector<HTMLElement>(selector)?.click();
      fixture.detectChanges();
      await fixture.whenStable();
    },
  };
}

describe('RulesTab', () => {
  it('exists at all — the console pointed here before this did', async () => {
    // The security console said a use-case rule "is changed on that use case", and there was no
    // such screen. An instruction with no destination is the `FRD-206` defect one level of
    // indirection further out.
    const harness = await setup();

    expect(harness.calls).toContain('list:uc-a');
    expect(harness.text()).toContain('too many refusals');
  });

  it('says what each rule does, not just its kind', async () => {
    const harness = await setup();
    expect(harness.text()).toContain('refused');
    expect(harness.text()).toContain('per caller');
  });

  it('tells the parent how many there are', async () => {
    const harness = await setup({ rules: of([RULE, { ...RULE, id: 5 }]) });
    expect(harness.host.count()).toBe(2);
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
    // one would be refused for exactly the people this screen exists for.
    const harness = await setup();
    await harness.click('[data-testid="rule-add"]');

    const form = harness.element.querySelector('app-rule-form');
    expect(form).not.toBeNull();
    expect(harness.testid('new-rule-name')).not.toBeNull();
    // The kind is choosable while the rule is new, and only then.
    expect(harness.testid('new-rule-kind')).not.toBeNull();
  });

  it('saves what the form emits, to this use case', async () => {
    const harness = await setup();
    const tab = harness.fixture.debugElement.children[0].componentInstance as unknown as {
      save: (changes: Partial<AnomalyRule>) => void;
    };

    tab.save({ name: 'new rule', kind: 'error_rate', threshold: 20 });

    expect(harness.calls.some((call) => call.startsWith('save:uc-a:'))).toBe(true);
  });

  it('asks before deleting, and says what stops being watched', async () => {
    const declined = await setup({ confirmAnswer: false });
    (
      declined.fixture.debugElement.children[0].componentInstance as unknown as {
        remove: (rule: AnomalyRule) => void;
      }
    ).remove(RULE);
    expect(declined.calls.filter((call) => call.startsWith('delete:'))).toEqual([]);

    const accepted = await setup({ confirmAnswer: true });
    (
      accepted.fixture.debugElement.children[0].componentInstance as unknown as {
        remove: (rule: AnomalyRule) => void;
      }
    ).remove(RULE);
    expect(accepted.calls).toContain('delete:uc-a:4');
  });

  it('says an empty list means global rules may still apply', async () => {
    // A quiet detector and an absent one look identical from a findings list. The place that
    // knows the difference has to say it — and "nothing here" is not the same as "nothing at all".
    const harness = await setup({ rules: of([]) });

    expect(harness.testid('no-rules')?.textContent).toContain('global rules');
    expect(harness.host.count()).toBe(0);
  });

  it('reports a failed load rather than showing an empty list', async () => {
    const harness = await setup({ rules: throwError(() => ({ status: 500 })) });
    const feedback = harness.fixture.debugElement.injector.get(PageFeedback);

    expect(feedback.error()).toContain('Could not load the anomaly rules');
    expect(harness.testid('no-rules')).not.toBeNull();
  });
});

describe('RulesTab — reading a rule at a glance', () => {
  it('names the unit a threshold is counted in', async () => {
    // "50" means half the requests under one kind and half a multiple under another. A bare
    // number beside a rule is the reading `rule-language` exists to prevent.
    const harness = await setup({ rules: of([{ ...RULE, kind: 'spend_spike', threshold: 300 }]) });

    expect(harness.text()).toContain('× the previous window');
  });

  it('says when a rule is switched off, so it is not read as watching', async () => {
    const harness = await setup({ rules: of([{ ...RULE, enabled: false }]) });
    expect(harness.text()).toContain('off');
  });

  it('shows what a blocking rule takes away', async () => {
    const harness = await setup({
      rules: of([{ ...RULE, action: 'block', action_minutes: 30 }]),
    });

    expect(harness.text()).toContain('block');
    expect(harness.text()).toContain('stopped for 30 minutes');
  });

  it('closes the form when the edit is abandoned', async () => {
    const harness = await setup();
    await harness.click('[data-testid="uc-rule-edit-4"]');
    expect(harness.element.querySelector('app-rule-form')).not.toBeNull();

    const tab = harness.fixture.debugElement.children[0].componentInstance as unknown as {
      cancel: () => void;
    };
    tab.cancel();
    harness.fixture.detectChanges();

    expect(harness.element.querySelector('app-rule-form')).toBeNull();
  });

  it('says the change takes a moment to reach the gateway', async () => {
    const harness = await setup();
    const tab = harness.fixture.debugElement.children[0].componentInstance as unknown as {
      save: (changes: Partial<AnomalyRule>) => void;
    };
    tab.save({ name: 'too many refusals', threshold: 60 });
    const feedback = harness.fixture.debugElement.injector.get(PageFeedback);

    expect(feedback.notice()).toContain('few seconds');
  });
});
