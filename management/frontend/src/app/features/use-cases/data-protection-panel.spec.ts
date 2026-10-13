import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { ContentMode, DataProtectionPanel, fieldsFor, modeOf } from './data-protection-panel';

type Writable<T> = { set: (v: T) => void; (): T };

interface Panel {
  retentionDays: Writable<number | null>;
  mode: Writable<ContentMode>;
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
  payload_visibility: 'members' as const,
  permissions: { can_admin: true, can_manage: true, is_member: true },
};

/** The fields an `update:` call carried — asserted by value, so a new setting breaks nothing. */
function sentUpdate(calls: string[]): Record<string, unknown> {
  const call = calls.find((entry) => entry.startsWith('update:'));
  return call ? JSON.parse(call.slice('update:'.length)) : {};
}

function setup(
  options: {
    useCase?: UseCase;
    update?: Observable<UseCase>;
    canManage?: boolean;
    /** The answer to "this deletes stored content — go ahead?". */
    confirmAnswer?: boolean;
  } = {},
) {
  TestBed.resetTestingModule();
  const useCase = options.useCase ?? USE_CASE;
  const calls: string[] = [];
  const asked: string[] = [];
  const saved: UseCase[] = [];
  const service = {
    update: (_s: string, changes: Partial<UseCase>) => {
      calls.push(`update:${JSON.stringify(changes)}`);
      return options.update ?? of({ ...useCase, ...changes });
    },
  };
  TestBed.configureTestingModule({
    imports: [DataProtectionPanel],
    providers: [
      { provide: UseCaseService, useValue: service },
      PageFeedback,
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
  const fixture = TestBed.createComponent(DataProtectionPanel);
  fixture.componentRef.setInput('slug', 'demo-uc');
  fixture.componentRef.setInput('useCase', useCase);
  fixture.componentRef.setInput('canManage', options.canManage ?? true);
  fixture.componentRef.setInput('shown', true);
  fixture.componentInstance.saved.subscribe((value) => saved.push(value));
  fixture.detectChanges();
  const html = () => fixture.nativeElement as HTMLElement;
  return {
    fixture,
    calls,
    asked,
    saved,
    feedback: TestBed.inject(PageFeedback),
    component: fixture.componentInstance as unknown as Panel,
    text: () => html().textContent ?? '',
    html,
    radio: (mode: ContentMode) =>
      html().querySelector<HTMLInputElement>(`[data-testid="content-mode-${mode}"]`),
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

describe('DataProtectionPanel — four content modes (`FRD-622` FR-1)', () => {
  it('reads the two fields as one of four modes', () => {
    // Not stored wins: with nothing written, who may read it is moot.
    expect(modeOf({ store_payloads: false, payload_visibility: 'own' })).toBe('not-stored');
    expect(modeOf({ store_payloads: false, payload_visibility: 'oversight' })).toBe('not-stored');
    expect(modeOf({ store_payloads: true, payload_visibility: 'oversight' })).toBe('oversight');
    expect(modeOf({ store_payloads: true, payload_visibility: 'own' })).toBe('admins-and-own');
    expect(modeOf({ store_payloads: true, payload_visibility: 'members' })).toBe('members');
    expect(modeOf(null)).toBe('members');
    // A server that does not send the field at all: the mode that existed before it did.
    expect(modeOf({ store_payloads: true })).toBe('members');
  });

  it('saves each mode as the two fields the gateway enforces', () => {
    expect(fieldsFor('not-stored')).toEqual({ store_payloads: false });
    expect(fieldsFor('oversight')).toEqual({
      store_payloads: true,
      payload_visibility: 'oversight',
    });
    expect(fieldsFor('admins-and-own')).toEqual({
      store_payloads: true,
      payload_visibility: 'own',
    });
    expect(fieldsFor('members')).toEqual({
      store_payloads: true,
      payload_visibility: 'members',
    });
  });

  it('round-trips every mode through the fields and back', () => {
    // The pair has to be a bijection on the stored modes, or the panel opens on a mode nobody
    // chose: `fieldsFor` is what a save sends and `modeOf` is what the next open reads.
    for (const mode of ['oversight', 'admins-and-own', 'members'] as const) {
      const fields = fieldsFor(mode);
      expect(modeOf({ store_payloads: true, payload_visibility: fields.payload_visibility })).toBe(
        mode,
      );
    }
  });

  it('offers the three modes in the DOM with the current one chosen', () => {
    const harness = setup();

    expect(harness.radio('not-stored')).not.toBeNull();
    expect(harness.radio('oversight')).not.toBeNull();
    expect(harness.radio('admins-and-own')).not.toBeNull();
    expect(harness.radio('members')?.checked).toBe(true);
    expect(harness.text()).toContain('read by nobody in this use case');
    expect(harness.text()).toContain("Administrators, and each person's own requests");
    expect(harness.text()).toContain('Every member of the use case');
  });

  it('a radio in the DOM changes the mode', () => {
    const harness = setup();
    harness.radio('admins-and-own')!.click();
    harness.fixture.detectChanges();

    expect(harness.component.mode()).toBe('admins-and-own');
    expect(harness.component.retentionChanged()).toBe(true);
  });

  it('hides the period and explains the consequence when nothing is stored', () => {
    const harness = setup();
    harness.radio('not-stored')!.click();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('#retention-days')).toBeNull();
    expect(harness.component.retentionError()).toBeNull();
    expect(harness.text()).toContain('Nothing a caller sends or receives is written');
    expect(harness.text()).toContain('only be traced through the metadata');
  });

  it('saves "not stored" without a period and says what changed', () => {
    const { component, calls, feedback } = setup();
    component.mode.set('not-stored');
    component.saveRetention();

    const sent = sentUpdate(calls);
    expect(sent).toEqual({ store_payloads: false });
    expect(feedback.notice()).toContain('no longer stored');
    expect(feedback.notice()).toContain('removed on the next run');
  });

  it('saves the restricted mode with the restriction on', () => {
    const harness = setup();
    harness.radio('admins-and-own')!.click();
    harness.fixture.detectChanges();
    harness.html().querySelector('form')!.dispatchEvent(new Event('submit'));

    expect(sentUpdate(harness.calls)).toEqual({
      store_payloads: true,
      payload_visibility: 'own',
      retention_days: 7,
    });
  });

  it('says that the platform roles can read stored content, and that every read is recorded', () => {
    // The choice decides who inside the use case reads, not whether the platform roles can.
    const { text } = setup();

    expect(text()).toContain('Global Administrators and IT Security');
    expect(text()).toContain('Every read is recorded');
  });

  it('shows a reader who may not change it the mode in words', () => {
    const { html } = setup({
      canManage: false,
      useCase: { ...USE_CASE, payload_visibility: 'own' as const },
    });

    expect(html().querySelector('[data-testid="content-mode-readonly"]')?.textContent).toContain(
      "Administrators, and each person's own requests",
    );
    expect(html().querySelector('[data-testid="retention-readonly"]')?.textContent).toContain('7');
    expect(html().querySelector('input[type="radio"]')).toBeNull();
  });
});

describe('DataProtectionPanel — oversight only (`FRD-622` FR-1b)', () => {
  it('saves the mode the use case cannot read itself', () => {
    const harness = setup();
    harness.radio('oversight')!.click();
    harness.fixture.detectChanges();
    harness.html().querySelector('form')!.dispatchEvent(new Event('submit'));

    expect(sentUpdate(harness.calls)).toEqual({
      store_payloads: true,
      payload_visibility: 'oversight',
      retention_days: 7,
    });
  });

  it('asks for a retention period, because something is still stored', () => {
    // The mode that reads *least* still writes, so the period is the one thing it must not drop:
    // content nobody in the use case can see still has to be erased on time.
    const harness = setup();
    harness.radio('oversight')!.click();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('#retention-days')).not.toBeNull();
    expect(harness.component.retentionError()).toBeNull();
  });

  it('shows a reader who may not change it that the use case cannot read its own content', () => {
    const { html } = setup({
      canManage: false,
      useCase: { ...USE_CASE, payload_visibility: 'oversight' as const },
    });

    expect(html().querySelector('[data-testid="content-mode-readonly"]')?.textContent).toContain(
      'Global Administrators and IT Security only',
    );
  });
});

/**
 * Shortening the period or switching storage off deletes stored prompts and responses on the next
 * retention run — the one irreversible thing on this form. It used to be announced only afterwards.
 */
describe('DataProtectionPanel — before anything is deleted', () => {
  it('asks before a shorter period, naming both, and sends nothing on no', () => {
    const harness = setup({ useCase: { ...USE_CASE, retention_days: 365 }, confirmAnswer: false });
    harness.component.retentionDays.set(1);
    harness.component.saveRetention();

    expect(harness.asked.at(-1)).toContain('1 day(s) instead of 365');
    expect(harness.asked.at(-1)).toContain('cannot be brought back');
    expect(harness.calls).toEqual([]);
  });

  it('asks before storage is switched off', () => {
    const harness = setup({ confirmAnswer: false });
    harness.component.mode.set('not-stored');
    harness.component.saveRetention();

    expect(harness.asked.at(-1)).toContain('Stop storing prompts and responses');
    expect(harness.calls).toEqual([]);
  });

  it('saves once the answer is yes', () => {
    const harness = setup({ useCase: { ...USE_CASE, retention_days: 365 } });
    harness.component.retentionDays.set(30);
    harness.component.saveRetention();

    expect(sentUpdate(harness.calls)['retention_days']).toBe(30);
  });

  it('asks nothing when nothing is deleted: a longer period, or a change of who may read', () => {
    const longer = setup();
    longer.component.retentionDays.set(30);
    longer.component.saveRetention();
    expect(longer.asked).toEqual([]);

    const readers = setup();
    readers.component.mode.set('oversight');
    readers.component.saveRetention();
    expect(readers.asked).toEqual([]);
    expect(readers.calls.length).toBe(1);
  });

  it('asks nothing when storage was already off', () => {
    const harness = setup({ useCase: { ...USE_CASE, store_payloads: false } });
    harness.component.mode.set('members');
    harness.component.retentionDays.set(3);
    harness.component.saveRetention();

    expect(harness.asked).toEqual([]);
  });
});

describe('DataProtectionPanel — a refused save', () => {
  it("says why in the server's words, keeps the change, and frees the button", () => {
    const harness = setup({
      update: throwError(() => ({
        status: 400,
        error: {
          error: { message: 'retention_days: Ensure this value is less than or equal to 3650.' },
        },
      })),
    });
    harness.component.retentionDays.set(30);
    harness.component.saveRetention();
    harness.fixture.detectChanges();

    expect(harness.feedback.error()).toContain('less than or equal to 3650');
    expect(harness.component.retentionDays()).toBe(30);
    expect(harness.saved).toEqual([]);
    expect(harness.component.canSaveRetention()).toBe(true);
  });
});
