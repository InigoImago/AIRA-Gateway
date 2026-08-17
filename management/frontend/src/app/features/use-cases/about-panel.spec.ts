import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { AboutPanel } from './about-panel';

const USE_CASE = {
  slug: 'demo',
  name: 'Demo',
  description: 'What it was for',
  processing_notes: 'No personal data.',
} as UseCase;

interface Panel {
  save: () => void;
  edit: (field: 'description' | 'processingNotes', value: string) => void;
}

function setup(options: { canManage?: boolean; update?: unknown } = {}) {
  const sent: Partial<UseCase>[] = [];
  const service = {
    update: (_slug: string, changes: Partial<UseCase>) => {
      sent.push(changes);
      return (options.update ?? of({ ...USE_CASE, ...changes })) as never;
    },
  };
  TestBed.configureTestingModule({
    imports: [AboutPanel],
    providers: [{ provide: UseCaseService, useValue: service }, PageFeedback],
  });
  const fixture: ComponentFixture<AboutPanel> = TestBed.createComponent(AboutPanel);
  fixture.componentRef.setInput('slug', 'demo');
  fixture.componentRef.setInput('canManage', options.canManage ?? true);
  fixture.componentRef.setInput('useCase', USE_CASE);
  fixture.detectChanges();
  return {
    fixture,
    sent,
    panel: fixture.componentInstance as unknown as Panel,
    feedback: TestBed.inject(PageFeedback),
    html: () => fixture.nativeElement as HTMLElement,
    field: (id: string) =>
      (fixture.nativeElement as HTMLElement).querySelector<HTMLTextAreaElement>(
        `[data-testid="${id}"]`,
      ),
  };
}

describe('AboutPanel', () => {
  it('fills both fields from the use case', async () => {
    /** The whole point: these were printed and unsettable, so the first thing to prove is that
     *  what the server holds arrives *in the control* rather than beside it. */
    const page = setup();
    await Promise.resolve();
    page.fixture.detectChanges();

    expect(page.field('uc-description')?.value).toBe('What it was for');
    expect(page.field('uc-processing')?.value).toBe('No personal data.');
  });

  it('sends both fields, trimmed', () => {
    const page = setup();
    page.panel.edit('description', '  a routing assistant  ');
    page.panel.edit('processingNotes', '  prompts are stored for 7 days  ');
    page.panel.save();

    expect(page.sent).toEqual([
      { description: 'a routing assistant', processing_notes: 'prompts are stored for 7 days' },
    ]);
  });

  it('does not overwrite what is being typed when the parent reloads', async () => {
    /** The parent reloads the use case after every other panel's save. Without the touched flag
     *  the effect would refill both fields from the server mid-sentence, and the reader would
     *  watch their own text vanish with nothing to explain it. */
    const page = setup();
    page.panel.edit('description', 'half a sentence');
    page.fixture.componentRef.setInput('useCase', { ...USE_CASE, description: 'from the server' });
    page.fixture.detectChanges();
    await Promise.resolve();

    expect(page.field('uc-description')?.value).toBe('half a sentence');
  });

  it('shows the values to a reader who may not change them, disabled rather than as prose', async () => {
    /** Read-only is a control, greyed — never a paragraph: a paragraph does not read as
     *  configuration, which is how the released-models overview went unread. */
    const page = setup({ canManage: false });
    // `ngModel` applies a disabled state through the form control, on a microtask.
    await Promise.resolve();
    page.fixture.detectChanges();

    expect(page.field('uc-description')?.disabled).toBe(true);
    expect(page.field('uc-processing')?.disabled).toBe(true);
    expect(page.html().querySelector('button')).toBeNull();
  });

  it('refuses to save for a reader who may not manage', () => {
    const page = setup({ canManage: false });
    page.panel.edit('description', 'sneaking one in');
    page.panel.save();

    expect(page.sent).toEqual([]);
  });

  it('reports a failed save instead of looking saved', () => {
    const page = setup({ update: throwError(() => ({ status: 500 })) });
    page.panel.edit('description', 'anything');
    page.panel.save();

    expect(page.feedback.error()).toContain('Could not change what this use case says');
  });
});
