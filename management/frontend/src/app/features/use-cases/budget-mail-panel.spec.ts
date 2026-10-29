import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { BudgetMailPanel, parseThresholds, thresholdsText } from './budget-mail-panel';

const USE_CASE: UseCase = {
  slug: 'coding',
  name: 'Coding',
  description: '',
  processing_notes: '',
  budget_mail_thresholds: [80, 100],
  permissions: { can_admin: true, can_manage: true, is_member: true },
};

async function setup(
  options: {
    useCase?: UseCase;
    canManage?: boolean;
    mailEnabled?: boolean;
    update?: Observable<UseCase>;
  } = {},
) {
  TestBed.resetTestingModule();
  const useCase = options.useCase ?? USE_CASE;
  const sent: Partial<UseCase>[] = [];
  const saved: UseCase[] = [];
  TestBed.configureTestingModule({
    imports: [BudgetMailPanel],
    providers: [
      {
        provide: UseCaseService,
        useValue: {
          update: (_slug: string, changes: Partial<UseCase>) => {
            sent.push(changes);
            return options.update ?? of({ ...useCase, ...changes });
          },
        },
      },
      PageFeedback,
    ],
  });
  const fixture = TestBed.createComponent(BudgetMailPanel);
  fixture.componentRef.setInput('slug', 'coding');
  fixture.componentRef.setInput('useCase', useCase);
  fixture.componentRef.setInput('canManage', options.canManage ?? true);
  fixture.componentRef.setInput('mailEnabled', options.mailEnabled ?? true);
  fixture.componentInstance.saved.subscribe((value) => saved.push(value));
  fixture.detectChanges();
  await fixture.whenStable();
  const element = fixture.nativeElement as HTMLElement;
  const testid = (id: string) => element.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const type = async (text: string) => {
    const field = testid('budget-mail-thresholds') as HTMLInputElement;
    field.value = text;
    field.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    await fixture.whenStable();
  };
  return {
    fixture,
    element,
    testid,
    type,
    sent,
    saved,
    feedback: TestBed.inject(PageFeedback),
    save: () => (testid('budget-mail-save') as HTMLButtonElement).disabled,
  };
}

describe('parseThresholds', () => {
  it('reads what an administrator types, in any order and with or without the sign', () => {
    expect(parseThresholds('100, 80%  50')).toEqual({ thresholds: [50, 80, 100] });
  });

  it('reads an empty field as no mail at all', () => {
    expect(parseThresholds('  ')).toEqual({ thresholds: [] });
  });

  it('refuses what the server would refuse, in its words', () => {
    expect(parseThresholds('0')).toEqual({
      error: 'Each threshold is a whole percentage from 1 to 100.',
    });
    expect(parseThresholds('101')).toHaveProperty('error');
    expect(parseThresholds('80.5')).toHaveProperty('error');
    expect(parseThresholds('eighty')).toHaveProperty('error');
    expect(parseThresholds('80, 80')).toEqual({ error: '80 % is named twice.' });
    expect(parseThresholds('10 20 30 40 50 60')).toEqual({ error: 'At most 5 thresholds.' });
  });

  it('says a list the way a sentence does', () => {
    expect(thresholdsText([100])).toBe('100%');
    expect(thresholdsText([80, 100])).toBe('80% and 100%');
    expect(thresholdsText([50, 80, 100])).toBe('50%, 80% and 100%');
  });
});

describe('BudgetMailPanel', () => {
  it('shows the saved shares and saves a change as the list the server keeps', async () => {
    const panel = await setup();
    expect((panel.testid('budget-mail-thresholds') as HTMLInputElement).value).toBe('80, 100');
    expect(panel.save()).toBe(true);

    await panel.type('50 80 100');
    expect(panel.save()).toBe(false);
    (panel.testid('budget-mail-save') as HTMLButtonElement).click();

    expect(panel.sent).toEqual([{ budget_mail_thresholds: [50, 80, 100] }]);
    expect(panel.saved[0].budget_mail_thresholds).toEqual([50, 80, 100]);
    expect(panel.feedback.notice()).toBe('A mail now goes out at 50%, 80% and 100% of a budget.');
  });

  it('says what is wrong before anything is sent', async () => {
    const panel = await setup();

    await panel.type('80, 120');

    expect(panel.testid('budget-mail-error')?.textContent).toContain('from 1 to 100');
    expect(panel.save()).toBe(true);
    expect(panel.sent).toEqual([]);
  });

  it('saves an empty field as no budget mail, and says so', async () => {
    const panel = await setup();

    await panel.type('');
    (panel.testid('budget-mail-save') as HTMLButtonElement).click();

    expect(panel.sent).toEqual([{ budget_mail_thresholds: [] }]);
    expect(panel.feedback.notice()).toContain('No mail goes out');
  });

  it('reads a use case that never said as mailing when a budget is used up', async () => {
    const panel = await setup({ useCase: { ...USE_CASE, budget_mail_thresholds: undefined } });

    expect((panel.testid('budget-mail-thresholds') as HTMLInputElement).value).toBe('100');
  });

  it('reports a refused save through the page banner and keeps what was typed', async () => {
    const panel = await setup({ update: throwError(() => ({ status: 500 })) });

    await panel.type('90');
    (panel.testid('budget-mail-save') as HTMLButtonElement).click();

    expect(panel.feedback.error()).toContain('Could not change when budget mail goes out.');
    expect((panel.testid('budget-mail-thresholds') as HTMLInputElement).value).toBe('90');
  });

  it('shows a reader who does not administer it what goes out, without a form', async () => {
    const panel = await setup({ canManage: false });

    expect(panel.testid('budget-mail-thresholds')).toBeNull();
    expect(panel.testid('budget-mail-readonly')?.textContent).toContain('80% and 100%');
  });

  it('says plainly when the installation sends no mail at all', async () => {
    const off = await setup({ mailEnabled: false });
    expect(off.testid('budget-mail-off')?.textContent).toContain('sends no mail');

    const on = await setup({ mailEnabled: true });
    expect(on.testid('budget-mail-off')).toBeNull();
  });
});
