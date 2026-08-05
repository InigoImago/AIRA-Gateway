import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { UseCaseList } from './use-case-list';

const DEMO: UseCase = { slug: 'demo-uc', name: 'Demo', description: '', processing_notes: '' };

interface List {
  slug: { set: (v: string) => void; (): string };
  name: { set: (v: string) => void; (): string };
  loading: () => boolean;
  creating: () => boolean;
  error: () => string | null;
  isEmpty: () => boolean;
  slugError: () => string | null;
  canCreate: () => boolean;
  create: () => void;
  reload: () => void;
}

function setup(options: { list?: Observable<UseCase[]>; create?: Observable<UseCase> } = {}) {
  TestBed.resetTestingModule();
  const created: Partial<UseCase>[] = [];
  TestBed.configureTestingModule({
    imports: [UseCaseList],
    providers: [
      provideRouter([]),
      {
        provide: UseCaseService,
        useValue: {
          list: () => options.list ?? of([DEMO]),
          create: (useCase: Partial<UseCase>) => {
            created.push(useCase);
            return options.create ?? of(DEMO);
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(UseCaseList);
  fixture.detectChanges();
  return {
    fixture,
    created,
    component: fixture.componentInstance as unknown as List,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
  };
}

describe('UseCaseList', () => {
  it('renders the use cases returned by the service', () => {
    const { text } = setup();
    expect(text()).toContain('Demo');
    expect(text()).toContain('demo-uc');
  });

  it('shows a loading state instead of an empty list while the request is open', () => {
    const { component, text } = setup({ list: new Observable<UseCase[]>(() => undefined) });
    expect(component.loading()).toBe(true);
    expect(text()).toContain('Loading use cases…');
    expect(text()).not.toContain('No use cases yet');
  });

  it('distinguishes "nothing there" from "still loading"', () => {
    const { component, text } = setup({ list: of([]) });
    expect(component.loading()).toBe(false);
    expect(component.isEmpty()).toBe(true);
    expect(text()).toContain('No use cases yet');
  });

  it('reports a failed load', () => {
    const { component, text } = setup({ list: throwError(() => ({ status: 500 })) });
    expect(component.error()).toBe('Failed to load use cases.');
    expect(text()).toContain('Failed to load use cases.');
    expect(component.loading()).toBe(false);
  });

  it('rejects a slug that the server would reject anyway', () => {
    const { component } = setup();
    component.slug.set('Not A Slug');
    component.name.set('Name');
    expect(component.slugError()).toBe('Lowercase letters, digits, and hyphens only.');
    expect(component.canCreate()).toBe(false);
  });

  it('accepts a valid slug and creates the use case', () => {
    const { component, created } = setup();
    component.slug.set('new-uc');
    component.name.set('New');
    expect(component.slugError()).toBeNull();
    expect(component.canCreate()).toBe(true);
    component.create();
    expect(created).toEqual([{ slug: 'new-uc', name: 'New' }]);
    expect(component.slug()).toBe('');
  });

  it('does not submit an incomplete form', () => {
    const { component, created } = setup();
    component.slug.set('new-uc');
    component.name.set('');
    component.create();
    expect(created).toEqual([]);
  });

  it('keeps the entered values and explains a rejected creation', () => {
    const { component } = setup({
      create: throwError(() => ({
        status: 400,
        error: { error: { message: 'Request failed.', details: { slug: ['Already exists.'] } } },
      })),
    });
    component.slug.set('demo-uc');
    component.name.set('Demo');
    component.create();
    expect(component.error()).toBe('Request failed. slug: Already exists.');
    expect(component.slug()).toBe('demo-uc');
    expect(component.creating()).toBe(false);
  });

  it('blocks a second submission while the first is in flight', () => {
    const { component } = setup({ create: new Observable<UseCase>(() => undefined) });
    component.slug.set('new-uc');
    component.name.set('New');
    component.create();
    expect(component.creating()).toBe(true);
    expect(component.canCreate()).toBe(false);
  });
});

describe('UseCaseList form', () => {
  it('clears the inputs in the DOM after a successful creation', () => {
    // Zoneless: resetting the model from the HTTP callback must schedule a re-render, or the
    // form keeps showing what was just submitted.
    const { component, fixture } = setup();
    component.slug.set('new-uc');
    component.name.set('New');
    fixture.detectChanges();
    component.create();
    fixture.detectChanges();

    const slugInput = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
      '#uc-slug',
    );
    expect(slugInput?.value).toBe('');
  });

  it('shows the slug rule inline and disables the submit button', () => {
    const { component, fixture } = setup();
    component.slug.set('Bad Slug');
    fixture.detectChanges();
    const html = fixture.nativeElement as HTMLElement;
    expect(html.querySelector('#uc-slug-error')?.textContent).toContain('Lowercase letters');
    expect(html.querySelector<HTMLInputElement>('#uc-slug')?.getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(html.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  });
});
