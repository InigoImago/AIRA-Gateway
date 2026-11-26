import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { ConfigurationChange, Page } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfigurationChangesPage, shown } from './configuration-changes-page';

function change(overrides: Partial<ConfigurationChange> = {}): ConfigurationChange {
  return {
    id: 1,
    changed_at: '2026-11-25T09:00:00+00:00',
    actor: 'boss',
    process: '',
    event_type: 'membership.upserted',
    kind: 'membership',
    action: 'updated',
    object_key: 'test-otel-uc|ada',
    use_case: 'test-otel-uc',
    person: 'ada',
    before: { slug: 'test-otel-uc', username: 'ada', role: 'user' },
    after: { slug: 'test-otel-uc', username: 'ada', role: 'admin' },
    fields_changed: ['role'],
    trace_id: '',
    ...overrides,
  };
}

function page(results: ConfigurationChange[]): Page<ConfigurationChange> {
  return { count: results.length, page: 1, page_size: 25, pages: 1, results };
}

interface Asked {
  kind?: string;
  useCase?: string;
  page?: number;
}

function setup(answer: () => Observable<Page<ConfigurationChange>> = () => of(page([change()]))) {
  TestBed.resetTestingModule();
  const asked: Asked[] = [];
  const service = {
    configurationChanges: (options: Asked): Observable<Page<ConfigurationChange>> => {
      asked.push({ ...options });
      return answer();
    },
  };
  TestBed.configureTestingModule({
    imports: [ConfigurationChangesPage],
    providers: [{ provide: UseCaseService, useValue: service }],
  });
  const fixture = TestBed.createComponent(ConfigurationChangesPage);
  fixture.detectChanges();
  const html = () => fixture.nativeElement as HTMLElement;
  const testid = (id: string) => html().querySelector<HTMLElement>(`[data-testid="${id}"]`);
  return { fixture, asked, html, testid };
}

describe('ConfigurationChangesPage — who changed what, from what to what (`FRD-632`)', () => {
  it('lists a change with when, who, what, the action and whom it concerns', () => {
    const harness = setup();

    expect(harness.testid('history-actor')!.textContent).toContain('boss');
    expect(harness.testid('history-what')!.textContent).toContain('Member');
    expect(harness.testid('history-what')!.textContent).toContain('test-otel-uc|ada');
    expect(harness.testid('history-action')!.textContent).toContain('Changed');
    expect(harness.testid('history-person')!.textContent).toContain('ada');
  });

  it('opens a change to show each field before and after', () => {
    const harness = setup();
    harness.testid('history-open-1')!.click();
    harness.fixture.detectChanges();

    const fields = harness.testid('history-fields')!.textContent!;
    expect(fields).toContain('role');
    expect(fields).toContain('user → admin');
  });

  it('names the process when no person made the change', () => {
    const harness = setup(() =>
      of(page([change({ actor: '', process: 'seed', action: 'saved', before: null })])),
    );

    expect(harness.testid('history-actor')!.textContent).toContain('(seed)');
    harness.testid('history-open-1')!.click();
    harness.fixture.detectChanges();
    expect(harness.testid('history-before-unknown')).not.toBeNull();
  });

  it('asks again from the first page when the kind is chosen', () => {
    const harness = setup();
    const select = harness.testid('history-kind') as HTMLSelectElement;
    select.value = select.options[2].value;
    select.dispatchEvent(new Event('change'));
    harness.fixture.detectChanges();

    expect(harness.asked.at(-1)).toEqual(expect.objectContaining({ kind: 'membership', page: 1 }));
  });

  it('filters by use case when the slug is entered', () => {
    const harness = setup();
    const input = harness.testid('history-use-case') as HTMLInputElement;
    input.value = 'test-otel-uc';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter' }));
    harness.fixture.detectChanges();

    expect(harness.asked.at(-1)).toEqual(expect.objectContaining({ useCase: 'test-otel-uc' }));
  });

  it('says so when nothing has been recorded', () => {
    const harness = setup(() => of(page([])));

    expect(harness.testid('no-history')).not.toBeNull();
  });

  it('says why when the history cannot be read', () => {
    const harness = setup(() =>
      throwError(() => ({ status: 403, error: { error: { message: 'Not allowed.' } } })),
    );

    expect(harness.html().textContent).toContain('Not allowed.');
  });

  it('shows a value as text, whatever it is', () => {
    expect(shown('admin')).toBe('admin');
    expect(shown(['a', 'b'])).toBe('["a","b"]');
    expect(shown(null)).toBe('—');
    expect(shown(undefined)).toBe('—');
    expect(shown(0)).toBe('0');
  });
});
