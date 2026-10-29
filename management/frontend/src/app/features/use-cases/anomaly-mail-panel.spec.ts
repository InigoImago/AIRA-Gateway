import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { AnomalyMailPanel } from './anomaly-mail-panel';

const USE_CASE: UseCase = {
  slug: 'coding',
  name: 'Coding',
  description: '',
  processing_notes: '',
  mail_admins_on_anomalies: false,
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
    imports: [AnomalyMailPanel],
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
  const fixture = TestBed.createComponent(AnomalyMailPanel);
  fixture.componentRef.setInput('slug', 'coding');
  fixture.componentRef.setInput('useCase', useCase);
  fixture.componentRef.setInput('canManage', options.canManage ?? true);
  fixture.componentRef.setInput('mailEnabled', options.mailEnabled ?? true);
  fixture.componentInstance.saved.subscribe((value) => saved.push(value));
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  const testid = (id: string) => element.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  return { fixture, element, testid, sent, saved, feedback: TestBed.inject(PageFeedback) };
}

describe('AnomalyMailPanel', () => {
  it('is off until an administrator switches it on, and saves at once', async () => {
    const panel = await setup();
    const toggle = panel.testid('anomaly-mail-toggle') as HTMLInputElement;
    expect(toggle.checked).toBe(false);

    toggle.click();

    expect(panel.sent).toEqual([{ mail_admins_on_anomalies: true }]);
    expect(panel.saved[0].mail_admins_on_anomalies).toBe(true);
    expect(panel.feedback.notice()).toContain('are now mailed');
  });

  it('switches off again', async () => {
    const panel = await setup({ useCase: { ...USE_CASE, mail_admins_on_anomalies: true } });

    (panel.testid('anomaly-mail-toggle') as HTMLInputElement).click();

    expect(panel.sent).toEqual([{ mail_admins_on_anomalies: false }]);
  });

  it('reports a refused change through the page banner', async () => {
    const panel = await setup({ update: throwError(() => ({ status: 500 })) });

    (panel.testid('anomaly-mail-toggle') as HTMLInputElement).click();

    expect(panel.feedback.error()).toContain('Could not change the mail about anomaly findings.');
    expect(panel.saved).toEqual([]);
  });

  it('shows a reader who does not administer it the state, without a control', async () => {
    const panel = await setup({
      canManage: false,
      useCase: { ...USE_CASE, mail_admins_on_anomalies: true },
    });

    expect(panel.testid('anomaly-mail-toggle')).toBeNull();
    expect(panel.testid('anomaly-mail-readonly')?.textContent).toContain('mailed');
  });

  it('says plainly when the installation sends no mail at all', async () => {
    const off = await setup({ mailEnabled: false });
    expect(off.testid('anomaly-mail-off')?.textContent).toContain('sends no mail');

    const on = await setup();
    expect(on.testid('anomaly-mail-off')).toBeNull();
  });

  it('promises the mail names nobody', async () => {
    const panel = await setup();
    expect(panel.element.textContent).toContain('never which person, key or address');
  });
});
