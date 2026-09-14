import { TestBed } from '@angular/core/testing';
import { Observable, of } from 'rxjs';
import { Page, RoleChange } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { RoleChanges } from './role-changes';

function change(id: number): RoleChange {
  return {
    id,
    role: 'controlling',
    action: 'updated',
    actor: 'admin',
    at: '2026-09-14T09:00:00+00:00',
    before: { label: 'Controlling', group_path: '/finance', permissions: [] },
    after: { label: 'Controlling', group_path: '/finance', permissions: ['report.read_all'] },
  };
}

function page(number: number, results: RoleChange[]): Page<RoleChange> {
  return { count: 30, page: number, page_size: 25, pages: 2, results };
}

function setup() {
  TestBed.resetTestingModule();
  const asked: number[] = [];
  const service = {
    roleChanges: (options: { page?: number }): Observable<Page<RoleChange>> => {
      const number = options.page ?? 1;
      asked.push(number);
      const results =
        number === 1
          ? Array.from({ length: 25 }, (_, i) => change(100 - i))
          : Array.from({ length: 5 }, (_, i) => change(75 - i));
      return of(page(number, results));
    },
  };
  TestBed.configureTestingModule({
    imports: [RoleChanges],
    providers: [{ provide: UseCaseService, useValue: service }, PageFeedback],
  });
  const fixture = TestBed.createComponent(RoleChanges);
  fixture.detectChanges();
  const html = () => fixture.nativeElement as HTMLElement;
  return {
    fixture,
    asked,
    html,
    rows: () => html().querySelectorAll('tbody tr'),
    pager: () => html().querySelector('[data-testid="role-changes-pager"]')!.textContent!,
    component: fixture.componentInstance,
  };
}

describe('RoleChanges — paged at the server (`FRD-614` FR-6)', () => {
  it('shows one page and the total, and replaces it with the next', () => {
    const harness = setup();
    expect(harness.rows().length).toBe(25);
    expect(harness.pager()).toContain('1–25 of 30 changes');
    expect(harness.pager()).toContain('Page 1 of 2');

    harness.html().querySelector<HTMLButtonElement>('[data-testid="pager-next"]')!.click();
    harness.fixture.detectChanges();

    expect(harness.asked).toEqual([1, 2]);
    expect(harness.rows().length).toBe(5);
    expect(harness.pager()).toContain('26–30 of 30 changes');
  });

  it('returns to the first page after a change, where the new entry is', () => {
    const harness = setup();
    harness.html().querySelector<HTMLButtonElement>('[data-testid="pager-next"]')!.click();
    harness.fixture.detectChanges();

    harness.component.reload();
    harness.fixture.detectChanges();

    expect(harness.asked).toEqual([1, 2, 1]);
    expect(harness.rows().length).toBe(25);
  });
});
