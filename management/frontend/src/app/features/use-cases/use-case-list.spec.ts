import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { map } from 'rxjs/operators';
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
  const queries: { query: string; page: number }[] = [];
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
              // A real caller: `use-case-admin` is not a role any more (`ADR-0017`), and a
              // harness whose default nobody can hold is a harness testing a different product.
              roles: options.roles ?? ['global-admin'],
              use_cases: [],
            } as unknown as Me),
        },
      },
      {
        provide: UseCaseService,
        useValue: {
          /**
           * The list is paged **at the server** now (`FRD-208`), so the double answers with a
           * page rather than an array — and it records what was asked for, because "did the search
           * reach the server" is the property that matters once the filtering is not local.
           */
          listPage: (query: string, page: number) => {
            queries.push({ query, page });
            const source = options.list ?? of([DEMO]);
            return source.pipe(
              map((rows: UseCase[]) => {
                const matching = query
                  ? rows.filter((row) =>
                      `${row.name} ${row.slug}`.toLowerCase().includes(query.toLowerCase()),
                    )
                  : rows;
                const size = 25;
                const start = (page - 1) * size;
                return {
                  count: matching.length,
                  page,
                  page_size: size,
                  pages: Math.max(1, Math.ceil(matching.length / size)),
                  results: matching.slice(start, start + size),
                };
              }),
            );
          },
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
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    created,
    navigated,
    queries,
    component,
    element,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: (selector: string) => {
      element.querySelector<HTMLElement>(selector)?.click();
      fixture.detectChanges();
    },
    /** Type into a search box the way a person does — an `input` event, not a property set. */
    type: (id: string, value: string) => {
      const input = element.querySelector<HTMLInputElement>(`[data-testid="${id}"]`)!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    },
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
    /**
     * **Narrowed with `ADR-0017`.** Creating a use case is a Global Administrator's act — they
     * create it and name the group that administers it. `use-case-admin` used to pass this gate
     * and is not a role at all any more, so the case that proves the narrowing is somebody who
     * administers a use case and is refused here.
     */
    const reader = setup({ roles: [], open: false });
    expect(reader.component.canCreate()).toBe(false);
    expect(reader.text()).not.toContain('New use case');

    // Oversight is not authority: IT Steuerung sees every use case and creates none.
    const governance = setup({ roles: ['it-steuerung'], open: false });
    expect(governance.component.canCreate()).toBe(false);

    const admin = setup({ roles: ['global-admin'], open: false });
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

describe('UseCaseList — finding one among many', () => {
  // The search is debounced and goes to the server, so these drive the clock. Without the pause a
  // nine-letter query would be nine round trips against the slowest endpoint in the console.
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('searches by name and by technical id, at the server', () => {
    // One is what a person calls it, the other is what their systems quote — somebody arriving
    // from a log line has the second and not the first.
    const harness = setup({
      open: false,
      list: of([
        { slug: 'kundenservice', name: 'Customer service' },
        { slug: 'entwicklung', name: 'Engineering' },
      ] as unknown as UseCase[]),
    });

    harness.type('use-case-search', 'Customer');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    // The needle reached the server rather than being applied to rows already in hand.
    expect(harness.queries.at(-1)).toEqual({ query: 'Customer', page: 1 });
    expect(harness.text()).toContain('Customer service');
    expect(harness.text()).not.toContain('Engineering');

    harness.type('use-case-search', 'entwick');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('Engineering');
    expect(harness.text()).not.toContain('Customer service');
  });

  it('does not send a request per keystroke', () => {
    const harness = setup({
      open: false,
      list: of([{ slug: 'a', name: 'A' }] as unknown as UseCase[]),
    });
    const before = harness.queries.length;

    for (const value of ['k', 'ku', 'kun', 'kund', 'kunde']) {
      harness.type('use-case-search', value);
      vi.advanceTimersByTime(50);
    }
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();

    expect(harness.queries.length - before).toBe(1);
    expect(harness.queries.at(-1)?.query).toBe('kunde');
  });

  it('goes back to page one when the search changes', () => {
    // Otherwise a filter applied on page 4 asks the server for page 4 of a two-page result and
    // gets nothing, which reads as "no matches".
    const many = Array.from({ length: 60 }, (_, index) => ({
      slug: `uc-${index}`,
      name: `Use case ${index}`,
    })) as unknown as UseCase[];
    const harness = setup({ open: false, list: of(many) });
    harness.click('[data-testid="pager-next"]');
    expect(harness.queries.at(-1)?.page).toBe(2);

    harness.type('use-case-search', 'uc-1');
    vi.advanceTimersByTime(300);

    expect(harness.queries.at(-1)?.page).toBe(1);
  });

  it('says a search found nothing, and how much there was to search', () => {
    const harness = setup({
      open: false,
      list: of([{ slug: 'a', name: 'A' }] as unknown as UseCase[]),
    });

    harness.type('use-case-search', 'zzz');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();

    expect(harness.testid('use-case-no-match')?.textContent).toContain('No use case matches');
  });

  it('pages a long list rather than printing all of it', () => {
    // A live round found 801 use cases in one installation. Nothing about the screen was wrong,
    // and it was unusable.
    const many = Array.from({ length: 60 }, (_, index) => ({
      slug: `uc-${index}`,
      name: `Use case ${index}`,
    })) as unknown as UseCase[];
    const harness = setup({ open: false, list: of(many) });

    expect(harness.element.querySelectorAll('tbody tr').length).toBe(25);
    expect(harness.text()).toContain('1–25 of 60 use cases');

    harness.click('[data-testid="pager-next"]');
    expect(harness.text()).toContain('26–50 of 60');
  });
});
