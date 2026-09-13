import { TestBed } from '@angular/core/testing';
import { Observable, of } from 'rxjs';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { DataProtectionPanel } from './data-protection-panel';

type Writable<T> = { set: (v: T) => void; (): T };

interface Panel {
  retentionDays: Writable<number | null>;
  storePayloads: Writable<boolean>;
  restrictMembers: Writable<boolean>;
  retentionError: () => string | null;
  retentionChanged: () => boolean;
  canSaveRetention: () => boolean;
  saveRetention: () => void;
}

const USE_CASE: UseCase = {
  slug: 'demo-uc',
  name: 'Demo',
  description: 'A demo use case',
  processing_notes: '',
  retention_days: 7,
  store_payloads: true,
  permissions: { can_admin: true, can_manage: true, is_member: true },
};

/** The fields an `update:` call carried — asserted by value, so a new setting breaks nothing. */
function sentUpdate(calls: string[]): Record<string, unknown> {
  const call = calls.find((entry) => entry.startsWith('update:'));
  return call ? JSON.parse(call.slice('update:'.length)) : {};
}

function setup(options: { useCase?: UseCase; update?: Observable<UseCase> } = {}) {
  TestBed.resetTestingModule();
  const useCase = options.useCase ?? USE_CASE;
  const calls: string[] = [];
  const saved: UseCase[] = [];
  const service = {
    update: (_s: string, changes: Partial<UseCase>) => {
      calls.push(`update:${JSON.stringify(changes)}`);
      return options.update ?? of({ ...useCase, ...changes });
    },
  };
  TestBed.configureTestingModule({
    imports: [DataProtectionPanel],
    providers: [{ provide: UseCaseService, useValue: service }, PageFeedback],
  });
  const fixture = TestBed.createComponent(DataProtectionPanel);
  fixture.componentRef.setInput('slug', 'demo-uc');
  fixture.componentRef.setInput('useCase', useCase);
  fixture.componentRef.setInput('canManage', true);
  fixture.componentRef.setInput('shown', true);
  fixture.componentInstance.saved.subscribe((value) => saved.push(value));
  fixture.detectChanges();
  return {
    fixture,
    calls,
    saved,
    feedback: TestBed.inject(PageFeedback),
    component: fixture.componentInstance as unknown as Panel,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    html: () => fixture.nativeElement as HTMLElement,
  };
}

describe('DataProtectionPanel — retention', () => {
  it('does not offer to save an unchanged period', () => {
    const { component } = setup();
    expect(component.retentionChanged()).toBe(false);
    expect(component.canSaveRetention()).toBe(false);
  });

  it('offers no save until something actually changed', () => {
    const { component } = setup();
    expect(component.canSaveRetention()).toBe(false);

    component.retentionDays.set(30);
    expect(component.canSaveRetention()).toBe(true);
  });

  it('refuses a period outside the allowed range', () => {
    const { component } = setup();
    component.retentionDays.set(0);
    expect(component.retentionError()).toContain('Between 1 and 3650');
    component.retentionDays.set(4000);
    expect(component.retentionError()).toContain('Between 1 and 3650');
    component.retentionDays.set(null);
    expect(component.retentionError()).toContain('Set how many days');
    expect(component.canSaveRetention()).toBe(false);
  });

  it('does nothing when asked to save a change that is not there', () => {
    const { component, calls } = setup();
    component.saveRetention();

    expect(calls).toEqual([]);
  });

  it('saves from the form in the DOM and hands the result to the page', () => {
    const harness = setup();
    harness.component.retentionDays.set(14);
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('label[for="retention-days"]')).not.toBeNull();
    harness.html().querySelector('form')!.dispatchEvent(new Event('submit'));
    harness.fixture.detectChanges();

    expect(sentUpdate(harness.calls)).toMatchObject({ store_payloads: true, retention_days: 14 });
    expect(harness.saved[0]?.retention_days).toBe(14);
  });
});

describe('DataProtectionPanel — payload storage', () => {
  it('offers a switch that turns storage off entirely', () => {
    const harness = setup();
    expect(harness.component.storePayloads()).toBe(true);
    expect(harness.text()).toContain('Store prompts and responses');
    expect(harness.html().querySelector('#retention-days')).not.toBeNull();
  });

  it('the checkbox in the DOM actually toggles the setting', () => {
    // A one-way ngModel on a checkbox inside an NgForm writes the old value back, which a test
    // that only sets the signal cannot see.
    const harness = setup();
    const box = harness.html().querySelector<HTMLInputElement>('#store-payloads');
    expect(box).not.toBeNull();
    expect(box!.checked).toBe(true);

    box!.click();
    harness.fixture.detectChanges();

    expect(harness.component.storePayloads()).toBe(false);
    expect(box!.checked).toBe(false);
    expect(harness.html().querySelector('#retention-days')).toBeNull();

    box!.click();
    harness.fixture.detectChanges();
    expect(harness.component.storePayloads()).toBe(true);
  });

  it('hides the period and explains the consequence when storage is off', () => {
    const harness = setup();
    harness.component.storePayloads.set(false);
    harness.fixture.detectChanges();

    // Nothing is kept, so there is no period to ask for.
    expect(harness.html().querySelector('#retention-days')).toBeNull();
    expect(harness.component.retentionError()).toBeNull();
    expect(harness.text()).toContain('Nothing a caller sends or receives is written');
    expect(harness.text()).toContain('only be traced through the metadata');
  });

  it('counts switching payload storage off as a change', () => {
    const { component } = setup();
    component.storePayloads.set(false);

    expect(component.canSaveRetention()).toBe(true);
  });

  it('saves the switch and says what changed', () => {
    const { component, calls, feedback } = setup();
    component.storePayloads.set(false);
    expect(component.retentionChanged()).toBe(true);

    component.saveRetention();
    const sent = sentUpdate(calls);
    expect(sent).toMatchObject({ store_payloads: false });
    // With storage off there is no period to send.
    expect(sent).not.toHaveProperty('retention_days');
    expect(feedback.notice()).toContain('no longer stored');
    expect(feedback.notice()).toContain('removed on the next run');
  });
});

describe('DataProtectionPanel — who sees whose requests (`FRD-505` FR-4)', () => {
  it('offers its administrator a switch for who sees whose requests', () => {
    const { component } = setup();

    expect(component.restrictMembers()).toBe(false);
    component.restrictMembers.set(true);

    expect(component.retentionChanged()).toBe(true);
  });

  it('sends the restriction with the data-protection settings', () => {
    // One form, one save: what is kept and who may read it are one question.
    const { component, calls } = setup();
    component.restrictMembers.set(true);
    component.saveRetention();

    expect(sentUpdate(calls)).toMatchObject({ restrict_members_to_own_requests: true });
  });
});
