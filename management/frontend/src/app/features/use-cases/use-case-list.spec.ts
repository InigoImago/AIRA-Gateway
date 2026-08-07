import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { Me, UseCase } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { UseCaseList, slugify } from './use-case-list';

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
  canSubmit: () => boolean;
  showCreate: { set: (v: boolean) => void; (): boolean };
  openCreate: () => void;
  closeCreate: () => void;
  nameChanged: (v: string) => void;
  slugChanged: (v: string) => void;
  create: () => void;
  reload: () => void;
}

function setup(
  options: {
    list?: Observable<UseCase[]>;
    create?: Observable<UseCase>;
    roles?: string[];
    open?: boolean;
  } = {},
) {
  TestBed.resetTestingModule();
  const created: Partial<UseCase>[] = [];
  const navigated: unknown[][] = [];
  TestBed.configureTestingModule({
    imports: [UseCaseList],
    providers: [
      provideRouter([]),
      {
        provide: MeService,
        useValue: {
          get: () =>
            of({
              username: 'u',
              roles: options.roles ?? ['use-case-admin'],
              use_cases: [],
            } as unknown as Me),
        },
      },
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
  // Recorded rather than executed: the real router has no route for the detail page here, and a
  // failed navigation would be an error about routing rather than about this screen.
  const router = TestBed.inject(Router);
  router.navigate = ((path: unknown[]) => {
    navigated.push(path);
    return Promise.resolve(true);
  }) as typeof router.navigate;

  const fixture = TestBed.createComponent(UseCaseList);
  fixture.detectChanges();
  const component = fixture.componentInstance as unknown as List;
  // Most of these assertions are about the creation form, which now lives in a window.
  if (options.open !== false) {
    component.showCreate.set(true);
    fixture.detectChanges();
  }
  return {
    fixture,
    created,
    navigated,
    component,
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
    expect(component.slugError()).toContain('Lowercase letters, digits, and hyphens only');
    expect(component.canSubmit()).toBe(false);
  });

  it('accepts a valid slug and creates the use case', () => {
    const { component, created, navigated } = setup();
    component.slug.set('new-uc');
    component.name.set('New');
    expect(component.slugError()).toBeNull();
    expect(component.canSubmit()).toBe(true);
    component.create();
    expect(created).toEqual([{ slug: 'new-uc', name: 'New' }]);
    expect(component.slug()).toBe('');
    // A use case with no members, no budget and no limits is not finished, and the list is
    // exactly the screen that makes it look finished.
    expect(navigated).toEqual([['/use-cases', 'new-uc']]);
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
    expect(component.canSubmit()).toBe(false);
  });

  it('offers the action only to somebody the server would let through', () => {
    const reader = setup({ roles: ['use-case-user'], open: false });
    expect(reader.component.canCreate()).toBe(false);
    expect(reader.text()).not.toContain('New use case');

    const admin = setup({ roles: ['use-case-admin'], open: false });
    expect(admin.component.canCreate()).toBe(true);
    expect(admin.text()).toContain('New use case');
  });

  it('fills the technical id from the name, and stops once it is typed by hand', () => {
    const { component } = setup();

    component.nameChanged('Kundenservice Prüfung');
    // Transliterated, not stripped: "prfung" reads as a typo forever after.
    expect(component.slug()).toBe('kundenservice-pruefung');

    component.nameChanged('Kundenservice Prüfung 2');
    expect(component.slug()).toBe('kundenservice-pruefung-2');

    component.slugChanged('mine');
    component.nameChanged('Something else entirely');
    expect(component.slug()).toBe('mine');
  });

  it('discards a half-typed use case when the window is closed', () => {
    const { component, created } = setup();
    component.nameChanged('Abandoned');
    component.closeCreate();
    expect(component.showCreate()).toBe(false);
    expect(created).toEqual([]);
    component.openCreate();
    expect(component.name()).toBe('');
    expect(component.slug()).toBe('');
  });
});

describe('slugify', () => {
  it('produces an id the server accepts, or nothing at all', () => {
    expect(slugify('  Hello,  World! ')).toBe('hello-world');
    expect(slugify('Größe & Maß')).toBe('groesse-mass');
    expect(slugify('***')).toBe('');
    expect(slugify('x'.repeat(200)).length).toBe(60);
  });
});

describe('UseCaseList form', () => {
  it('closes the window after a successful creation', () => {
    // Zoneless: closing from the HTTP callback must schedule a re-render, or the window stays on
    // screen still showing what was just submitted.
    const { component, fixture } = setup();
    component.slug.set('new-uc');
    component.name.set('New');
    fixture.detectChanges();
    component.create();
    fixture.detectChanges();

    const html = fixture.nativeElement as HTMLElement;
    expect(html.querySelector('[role="dialog"]')).toBeNull();
    expect(html.querySelector('#uc-slug')).toBeNull();
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
