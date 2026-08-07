import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { Capability, CatalogModel, Me } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { ModelCatalog } from './model-catalog';

const FLASH: CatalogModel = {
  name: 'gemini-2.0-flash',
  display_name: 'Flash',
  provider: 'google',
  input_price_per_million: '0.075',
  output_price_per_million: '0.30',
  is_priced: true,
};
const UNPRICED: CatalogModel = { name: 'mystery-1', is_priced: false };
const DECLARED: CatalogModel = {
  name: 'claude-sonnet-4-5@20250929',
  provider: 'anthropic',
  is_priced: true,
  is_declared: true,
  capabilities: ['generate', 'thinking'],
  publisher: 'anthropic',
  platform: 'vertex',
  hosting: 'managed',
  max_output_tokens: 64000,
  default_max_output_tokens: 4096,
  deprecated: true,
};

interface Catalog {
  models: () => CatalogModel[];
  loading: () => boolean;
  error: () => string | null;
  notice: () => string | null;
  canEdit: () => boolean;
  unpriced: () => CatalogModel[];
  undeclared: () => CatalogModel[];
  capabilities: { set: (v: Capability[]) => void; (): Capability[] };
  hasCapability: (c: Capability) => boolean;
  toggleCapability: (c: Capability, on: boolean) => void;
  publisher: { set: (v: string) => void; (): string };
  hosting: { set: (v: string) => void; (): string };
  maxOutput: { set: (v: number | null) => void; (): number | null };
  defaultOutput: { set: (v: number | null) => void; (): number | null };
  deprecated: { set: (v: boolean) => void; (): boolean };
  showAdd: { set: (v: boolean) => void; (): boolean };
  name: { set: (v: string) => void; (): string };
  inputPrice: { set: (v: string) => void; (): string };
  outputPrice: { set: (v: string) => void; (): string };
  formError: () => string | null;
  canSave: () => boolean;
  save: () => void;
  edit: (m: CatalogModel) => void;
  remove: (m: CatalogModel) => void;
}

function setup(
  options: {
    models?: Observable<CatalogModel[]>;
    save?: Observable<CatalogModel>;
    roles?: string[];
    confirm?: boolean;
  } = {},
) {
  TestBed.resetTestingModule();
  const saved: CatalogModel[] = [];
  const removed: string[] = [];
  const me: Me = {
    subject: 's',
    username: 'admin',
    email: '',
    roles: options.roles ?? ['global-admin'],
    use_cases: [],
  };

  TestBed.configureTestingModule({
    imports: [ModelCatalog],
    providers: [
      { provide: MeService, useValue: { get: () => of(me) } },
      { provide: ConfirmService, useValue: { ask: () => options.confirm ?? true } },
      {
        provide: UseCaseService,
        useValue: {
          models: () => options.models ?? of([FLASH, UNPRICED]),
          saveModel: (model: CatalogModel) => {
            saved.push(model);
            return options.save ?? of(model);
          },
          removeModel: (name: string) => {
            removed.push(name);
            return of(undefined as unknown as void);
          },
        },
      },
    ],
  });

  const fixture = TestBed.createComponent(ModelCatalog);
  fixture.detectChanges();
  return {
    fixture,
    saved,
    removed,
    component: fixture.componentInstance as unknown as Catalog,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    html: () => fixture.nativeElement as HTMLElement,
  };
}

describe('ModelCatalog', () => {
  it('lists the catalog with its prices', () => {
    const { text } = setup();
    expect(text()).toContain('gemini-2.0-flash');
    expect(text()).toContain('0.075');
    expect(text()).toContain('0.30');
  });

  it('flags models that cannot be costed', () => {
    const { component, text } = setup();
    expect(component.unpriced().map((m) => m.name)).toEqual(['mystery-1']);
    expect(text()).toContain('no price');
    expect(text()).toContain('left out of every spend figure');
  });

  it('hides the editing surface from everyone but a global admin', () => {
    const admin = setup();
    expect(admin.component.canEdit()).toBe(true);
    expect(admin.html().querySelector('[aria-label^="Edit"]')).not.toBeNull();

    const reader = setup({ roles: ['it-steuerung'] });
    expect(reader.component.canEdit()).toBe(false);
    expect(reader.html().querySelector('[aria-label^="Edit"]')).toBeNull();
    expect(reader.text()).not.toContain('Add model');
  });

  it('names the model it is editing, and leaving the window discards the edit', () => {
    const harness = setup();
    const html = () => harness.fixture.nativeElement as HTMLElement;

    harness.component.edit({
      name: 'gemini-2.0-flash',
      display_name: '',
      provider: 'google',
      input_price_per_million: '1.00',
      output_price_per_million: '2.00',
      is_priced: true,
      is_declared: true,
      capabilities: [],
    } as never);
    harness.fixture.detectChanges();

    // Which model this window is about must be on screen. The unfolding panel it replaced put the
    // form far below the row it came from, with nothing saying which row that was.
    const dialog = html().querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain('gemini-2.0-flash');

    harness.component.name.set('typo');
    [...html().querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === 'Cancel')
      ?.click();
    harness.fixture.detectChanges();

    expect(html().querySelector('[role="dialog"]')).toBeNull();
    expect(harness.saved).toHaveLength(0);
    // Reopening must not show the abandoned edit.
    expect(harness.component.name()).toBe('');
  });

  it('refuses a price that is not an amount', () => {
    const { component } = setup();
    component.name.set('m-1');
    component.inputPrice.set('teuer');
    component.outputPrice.set('1.00');
    expect(component.formError()).toContain('amounts per 1,000,000 tokens');
    expect(component.canSave()).toBe(false);
  });

  it('refuses a model priced in only one direction', () => {
    const { component } = setup();
    component.name.set('m-1');
    component.inputPrice.set('1.00');
    expect(component.formError()).toContain('both');
  });

  it('accepts a model with no price at all', () => {
    const { component, saved } = setup();
    component.name.set('m-1');
    component.save();
    expect(saved[0]).toMatchObject({ name: 'm-1', input_price_per_million: null });
  });

  it('sends prices as strings and normalises a comma', () => {
    const { component, saved } = setup();
    component.name.set('m-1');
    component.inputPrice.set('0,075');
    component.outputPrice.set('0.30');
    component.save();
    expect(saved[0].input_price_per_million).toBe('0.075');
    expect(typeof saved[0].input_price_per_million).toBe('string');
  });

  it('loads a row into the form for correction', () => {
    const { component } = setup();
    component.edit(FLASH);
    expect(component.name()).toBe('gemini-2.0-flash');
    expect(component.inputPrice()).toBe('0.075');
    expect(component.showAdd()).toBe(true);
  });

  it('asks before removing a model', () => {
    const declined = setup({ confirm: false });
    declined.component.remove(FLASH);
    expect(declined.removed).toEqual([]);

    const accepted = setup();
    accepted.component.remove(FLASH);
    expect(accepted.removed).toEqual(['gemini-2.0-flash']);
    expect(accepted.component.notice()).toContain('removed');
  });

  it('reports a failed load and a failed save', () => {
    const failedLoad = setup({ models: throwError(() => ({ status: 500 })) });
    expect(failedLoad.component.error()).toBe('Could not load the model catalog.');
    expect(failedLoad.component.loading()).toBe(false);

    const failedSave = setup({
      save: throwError(() => ({ status: 403, error: { error: { message: 'Not an admin.' } } })),
    });
    failedSave.component.name.set('m-1');
    failedSave.component.save();
    expect(failedSave.component.error()).toBe('Not an admin.');
  });

  it('shows a loading state rather than an empty catalog', () => {
    const { component, text } = setup({ models: new Observable<CatalogModel[]>(() => undefined) });
    expect(component.loading()).toBe(true);
    expect(text()).toContain('Loading catalog…');
  });

  it('says when the catalog is genuinely empty', () => {
    expect(setup({ models: of([]) }).text()).toContain('No models in the catalog yet');
  });
});

describe('ModelCatalog interactions', () => {
  it('opens the editor window, fills it, and saves from the DOM', async () => {
    const harness = setup();
    const html = () => harness.fixture.nativeElement as HTMLElement;

    expect(html().querySelector('#model-name')).toBeNull();
    [...html().querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('Add model'))
      ?.click();
    harness.fixture.detectChanges();

    const dialog = html().querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.getAttribute('aria-modal')).toBe('true');

    const name = html().querySelector<HTMLInputElement>('#model-name');
    expect(name).not.toBeNull();
    expect(html().querySelector('label[for="model-input"]')).not.toBeNull();
    expect(html().querySelector('label[for="model-output"]')).not.toBeNull();

    harness.component.name.set('m-1');
    harness.component.inputPrice.set('1.00');
    harness.component.outputPrice.set('2.00');
    harness.fixture.detectChanges();

    // Through the footer button, which lives *outside* the form and reaches it by `form=`. Firing
    // submit on the form directly would pass even if that association were wrong, and an
    // unclickable Save is the only way this window can fail.
    html().querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
    harness.fixture.detectChanges();

    expect(harness.saved[0]).toMatchObject({
      name: 'm-1',
      input_price_per_million: '1.00',
      output_price_per_million: '2.00',
    });
    // A successful save clears and closes the window.
    expect(harness.component.showAdd()).toBe(false);
    expect(harness.component.name()).toBe('');
  });

  it('disables the submit button while the form is invalid', () => {
    const harness = setup();
    harness.component.showAdd.set(true);
    harness.fixture.detectChanges();
    const html = harness.fixture.nativeElement as HTMLElement;

    expect(html.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
    expect(html.querySelector('.field__hint--error')?.textContent).toContain(
      'model id is required',
    );
  });

  it('removes a model from its row button', () => {
    const harness = setup();
    const button = (harness.fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>(
      '[aria-label="Remove gemini-2.0-flash"]',
    );
    button?.click();
    harness.fixture.detectChanges();
    expect(harness.removed).toEqual(['gemini-2.0-flash']);
  });

  it('edits a model from its row button', () => {
    const harness = setup();
    (harness.fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[aria-label="Edit gemini-2.0-flash"]')
      ?.click();
    harness.fixture.detectChanges();
    expect(harness.component.name()).toBe('gemini-2.0-flash');
    expect(
      (harness.fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>('#model-name')
        ?.value,
    ).toBeDefined();
  });

  it('shows the display name and a dash for a missing provider', () => {
    const { text } = setup({ models: of([FLASH, UNPRICED]) });
    expect(text()).toContain('Flash');
    expect(text()).toContain('—');
  });
});

describe('ModelCatalog — declarations (FRD-114)', () => {
  it('marks a model nobody has described, because it quietly does less than the list suggests', () => {
    const { html, text } = setup({ models: of([UNPRICED, DECLARED]) });

    expect(html().querySelector('[data-testid="undeclared-caveat"]')).not.toBeNull();
    expect(text()).toContain('absence of information is not permission');
    expect(text()).toContain('undeclared');
  });

  it('says nothing about declarations when every model has one', () => {
    const { html } = setup({ models: of([DECLARED]) });

    expect(html().querySelector('[data-testid="undeclared-caveat"]')).toBeNull();
  });

  it('shows a deprecated model as deprecated rather than hiding or removing it', () => {
    // Warning, not blocking — that is what makes it possible to announce a retirement before
    // performing one.
    const { text } = setup({ models: of([DECLARED]) });

    expect(text()).toContain('deprecated');
    expect(text()).toContain('claude-sonnet-4-5@20250929');
  });

  it('sends the declaration with the price', () => {
    const page = setup();
    page.component.showAdd.set(true);
    page.component.name.set('claude-1');
    page.component.toggleCapability('thinking', true);
    page.component.toggleCapability('generate', true);
    page.component.publisher.set('anthropic');
    page.component.hosting.set('managed');
    page.component.maxOutput.set(64000);
    page.component.defaultOutput.set(4096);
    page.component.deprecated.set(true);
    page.component.save();

    const sent = page.saved[page.saved.length - 1];
    expect(sent.capabilities?.sort()).toEqual(['generate', 'thinking']);
    expect(sent.publisher).toBe('anthropic');
    expect(sent.hosting).toBe('managed');
    expect(sent.max_output_tokens).toBe(64000);
    expect(sent.default_max_output_tokens).toBe(4096);
    expect(sent.deprecated).toBe(true);
  });

  it('refuses a default output cap above the maximum before sending it', () => {
    const page = setup();
    page.component.showAdd.set(true);
    page.component.name.set('impossible-1');
    page.component.maxOutput.set(1024);
    page.component.defaultOutput.set(4096);

    expect(page.component.formError()).toContain('cannot exceed the maximum');
    expect(page.component.canSave()).toBe(false);
  });

  it('loads a declaration into the form so it can be corrected in place', () => {
    const page = setup({ models: of([DECLARED]) });
    page.component.edit(DECLARED);

    expect(page.component.hasCapability('thinking')).toBe(true);
    expect(page.component.hasCapability('embed')).toBe(false);
    expect(page.component.publisher()).toBe('anthropic');
    expect(page.component.maxOutput()).toBe(64000);
    expect(page.component.deprecated()).toBe(true);
  });

  it('unticking a capability removes it rather than leaving it in the list', () => {
    const page = setup();
    page.component.capabilities.set(['generate', 'thinking']);
    page.component.toggleCapability('thinking', false);

    expect(page.component.capabilities()).toEqual(['generate']);
  });
});
