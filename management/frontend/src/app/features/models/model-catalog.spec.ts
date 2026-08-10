import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { Capability, CatalogModel, Me, ModelCheck } from '../../core/api/models';
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
  add: () => void;
  discover: () => void;
  discovering: () => boolean;
  served: () => unknown[] | null;
  notCatalogued: () => { name: string }[];
  alreadyCatalogued: () => number;
  importServed: (model: unknown) => void;
  provider: { set: (v: string) => void; (): string };
  approved: { set: (v: boolean) => void; (): boolean };
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

const CHECK: ModelCheck = {
  model: 'gemini-2.0-flash',
  declared: true,
  served: true,
  reachable: true,
  detail: '3 models listed',
};

function setup(
  options: {
    models?: Observable<CatalogModel[]>;
    served?: Observable<unknown[]>;
    save?: Observable<CatalogModel>;
    roles?: string[];
    confirm?: boolean;
    /** What `:check` answers (`FRD-506`). */
    check?: Observable<ModelCheck>;
  } = {},
) {
  const checked: string[] = [];
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
          checkModel: (name: string) => {
            checked.push(name);
            return options.check ? options.check : of(CHECK);
          },
          models: () => options.models ?? of([FLASH, UNPRICED]),
          servedModels: () =>
            options.served ??
            of([
              { name: 'flash', airaDeclared: true },
              {
                name: 'gemini-3.1-flash-lite',
                airaDeclared: false,
                airaProvider: 'generative-language',
                airaPublisher: 'google',
                airaRegion: 'global',
              },
            ]),
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
    checked,
    html: () => fixture.nativeElement as HTMLElement,
    /** Run the reachability check for whatever name is in the editor. Creating a model now
     *  requires having *looked* (`FRD-506`), so every case that creates one does this — which is
     *  also the cheapest possible proof that the gate is real. */
    lookFirst: () => {
      (
        fixture.componentInstance as unknown as { runCheck: (m: { name: string }) => void }
      ).runCheck({ name: (fixture.componentInstance as unknown as Catalog).name() });
      fixture.detectChanges();
    },
    /** Open the first row's declaration. What a model *is* lives in the panel now; the columns
     *  carry what a catalog is scanned by. */
    openFirst: () => {
      (fixture.nativeElement as HTMLElement)
        .querySelector<HTMLElement>('[data-testid^="open-model-"]')
        ?.click();
      fixture.detectChanges();
    },
    testid: (id: string) =>
      (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="${id}"]`),
    click: (selector: string) => {
      (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(selector)?.click();
      fixture.detectChanges();
    },
    type: (id: string, value: string) => {
      const input = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
        `[data-testid="${id}"]`,
      )!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    },
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
    admin.openFirst();
    expect(admin.component.canEdit()).toBe(true);
    expect(admin.html().querySelector('[data-testid^="edit-"]')).not.toBeNull();

    const reader = setup({ roles: ['it-steuerung'] });
    reader.openFirst();
    expect(reader.component.canEdit()).toBe(false);
    // The panel still opens — reading a declaration is not editing one — and carries no actions.
    expect(reader.html().querySelector('[data-testid^="detail-"]')).not.toBeNull();
    expect(reader.html().querySelector('[data-testid^="edit-"]')).toBeNull();
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
    const harness = setup();
    const { component, saved } = harness;
    component.name.set('m-1');
    harness.lookFirst();
    component.save();
    expect(saved[0]).toMatchObject({ name: 'm-1', input_price_per_million: null });
  });

  it('sends prices as strings and normalises a comma', () => {
    const harness = setup();
    const { component, saved } = harness;
    component.name.set('m-1');
    component.inputPrice.set('0,075');
    component.outputPrice.set('0.30');
    harness.lookFirst();
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
    failedSave.lookFirst();
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

    // Save is unavailable until the model has been checked — the gate asserted through the DOM
    // rather than through the method, since the button is what a person actually reaches for.
    expect(html().querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
    expect(html().querySelector('[data-testid="check-required"]')).not.toBeNull();
    html().querySelector<HTMLButtonElement>('[data-testid="editor-check"]')?.click();
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

  it('removes a model from the panel that shows what it is', () => {
    /** The actions moved out of the row and into the opened declaration: they were two buttons in
     *  a column that pushed the table past the screen, and they act on the model whose fields are
     *  now in front of the reader. */
    const harness = setup();
    harness.openFirst();
    (harness.fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[data-testid="remove-gemini-2.0-flash"]')
      ?.click();
    harness.fixture.detectChanges();
    expect(harness.removed).toEqual(['gemini-2.0-flash']);
  });

  it('edits a model from the panel that shows what it is', () => {
    const harness = setup();
    harness.openFirst();
    (harness.fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[data-testid="edit-gemini-2.0-flash"]')
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
    page.lookFirst();
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

describe('ModelCatalog — finding one among many', () => {
  function models(count: number): CatalogModel[] {
    return Array.from({ length: count }, (_, index) => ({
      ...FLASH,
      name: `model-${index}`,
      provider: index % 2 === 0 ? 'vertex' : 'ollama',
    }));
  }

  it('searches by model name and by provider', () => {
    // "What do we serve on Vertex" is as common a question here as "what does this model cost".
    const harness = setup({ models: of(models(4)) });

    harness.type('model-search', 'model-2');
    expect(harness.html().querySelectorAll('tbody tr').length).toBe(1);

    harness.type('model-search', 'ollama');
    expect(harness.html().querySelectorAll('tbody tr').length).toBe(2);
  });

  it('says a search found nothing, and how big the catalog is', () => {
    const harness = setup({ models: of(models(3)) });
    harness.type('model-search', 'zzz');

    expect(harness.testid('model-no-match')?.textContent).toContain('catalog holds 3');
  });

  it('pages a catalog that has outgrown one screen', () => {
    const harness = setup({ models: of(models(60)) });

    expect(harness.html().querySelectorAll('tbody tr').length).toBe(25);
    harness.click('[data-testid="pager-next"]');
    expect(harness.text()).toContain('26–50 of 60 models');
  });

  // ---- is it reachable, or only written down? (`FRD-506`) -----------------------------------

  it('says a declared model nothing serves is not reachable, and why', () => {
    /** The case a missing credential produces, and the one the console could not show: an adapter
     *  is registered only when its credential is configured, so the model sat in the catalog
     *  looking healthy while every request for it came back `model_not_found`. */
    const harness = setup({
      check: of({
        model: 'gemini-2.0-flash',
        declared: true,
        served: false,
        reachable: null,
        detail: 'No upstream serves this model. A declaration is metadata; …credential…',
      }),
    });
    harness.openFirst();
    harness.html().querySelector<HTMLElement>('[data-testid^="check-gemini"]')?.click();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid="check-verdict"]')?.textContent).toContain(
      'nothing serves it',
    );
    expect(harness.html().querySelector('[data-testid="check-detail"]')?.textContent).toContain(
      'credential',
    );
  });

  it('does not report "not contacted" as reachable', () => {
    /** `FRD-117`'s rule: "we did not look" and "it is fine" are different answers, and only one of
     *  them is safe to act on. */
    const harness = setup({
      check: of({
        model: 'gemini-2.0-flash',
        declared: true,
        served: true,
        reachable: null,
        detail: 'This upstream offers nothing cheap to ask; it was not contacted.',
      }),
    });
    harness.openFirst();
    harness.html().querySelector<HTMLElement>('[data-testid^="check-gemini"]')?.click();
    harness.fixture.detectChanges();

    const badge = harness.html().querySelector('[data-testid="check-verdict"]');
    expect(badge?.textContent).toContain('not contacted');
    expect(badge?.classList.contains('badge--success')).toBe(false);
  });

  it('asks about the model whose panel is open', () => {
    const harness = setup();
    harness.openFirst();
    harness.html().querySelector<HTMLElement>('[data-testid^="check-"]')?.click();

    expect(harness.checked).toEqual(['gemini-2.0-flash']);
  });

  it('forgets a verdict when another model is opened', () => {
    /** A verdict left on screen under a different model is worse than none — it is a wrong answer
     *  that looks like a right one. */
    const harness = setup();
    harness.openFirst();
    harness.html().querySelector<HTMLElement>('[data-testid^="check-"]')?.click();
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('[data-testid="check-verdict"]')).not.toBeNull();

    harness.openFirst();
    harness.openFirst();

    expect(harness.html().querySelector('[data-testid="check-verdict"]')).toBeNull();
  });

  it('checks reachability from inside the editor, and never blocks saving on it', () => {
    /**
     * The design question this answers, asked directly: *"warum machen wir check reachability
     * nicht im Window, und wenn reachability false ist, dann kein Anlegen?"*
     *
     * In the window: yes. Blocking: **no**, and deliberately. Declaring a model before its
     * credential exists is the ordinary order of work — you write the catalog, then configure the
     * platform — and an adapter is registered only once the credential is there. A hard gate would
     * make it impossible to declare anything on a fresh installation, and impossible to declare a
     * model for a platform this deployment has not been given a key for yet. `FRD-114`'s rule:
     * deprecation warns, revocation blocks. A verdict is information.
     */
    const harness = setup({
      check: of({
        model: 'new-model',
        declared: false,
        served: false,
        reachable: null,
        detail: 'No upstream serves this model.',
      }),
    });
    harness.component.add();
    harness.component.name.set('new-model');
    harness.fixture.detectChanges();

    harness.html().querySelector<HTMLElement>('[data-testid="editor-check"]')?.click();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid="editor-verdict"]')?.textContent).toContain(
      'nothing serves it',
    );
    // The point of the test: Save is still available.
    expect(harness.component.canSave()).toBe(true);
  });

  it('does not carry a verdict from one model into the next window', () => {
    const harness = setup();
    harness.component.add();
    harness.component.name.set('a-model');
    harness.fixture.detectChanges();
    harness.html().querySelector<HTMLElement>('[data-testid="editor-check"]')?.click();
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('[data-testid="editor-verdict"]')).not.toBeNull();

    harness.component.add();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid="editor-verdict"]')).toBeNull();
  });

  it('reports a check that could not be run, instead of an empty badge', () => {
    /** The check itself can fail — the gateway may be unreachable, or the role may be wrong. That
     *  is a different fact from "the model is not reachable", and showing nothing would let the
     *  reader conclude the second. */
    const harness = setup({ check: throwError(() => ({ status: 503 })) });
    harness.openFirst();
    harness.html().querySelector<HTMLElement>('[data-testid^="check-gemini"]')?.click();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid="check-verdict"]')).toBeNull();
    expect(harness.component.error()).toBeTruthy();
  });

  // ---- only an approved model may be used (`FRD-307`) --------------------------------------

  it('starts a new declaration unapproved, and sends that', () => {
    /** A model appearing on an upstream is not the same event as somebody deciding it may be used
     *  here. The default is the decision. */
    const harness = setup();
    harness.component.add();
    harness.component.name.set('brand-new');
    harness.lookFirst();
    harness.component.save();

    expect(harness.saved[0]).toMatchObject({ name: 'brand-new', approved: false });
  });

  it('marks an unapproved model in the list, not only inside the row', () => {
    /** The one state a reader must not have to open a row to discover: this model is in the
     *  catalog and cannot be called. */
    const harness = setup({ models: of([{ ...FLASH, approved: false }]) });

    expect(harness.html().querySelector('[data-testid="not-approved"]')).not.toBeNull();
  });

  it('carries an existing approval into the editor rather than clearing it', () => {
    /** Editing a price must not quietly retire a model. */
    const harness = setup({ models: of([{ ...FLASH, approved: true }]) });
    harness.component.edit({ ...FLASH, approved: true });

    expect(harness.component.approved()).toBe(true);
  });

  it('says a reachable model is reachable, and an unreachable one is not', () => {
    /** Three verdicts, three sentences. "Not contacted" is covered elsewhere; these are the two
     *  that describe an actual attempt. */
    const reachable = setup();
    reachable.openFirst();
    reachable.html().querySelector<HTMLElement>('[data-testid^="check-gemini"]')?.click();
    reachable.fixture.detectChanges();
    expect(reachable.html().querySelector('[data-testid="check-verdict"]')?.textContent).toContain(
      'Reachable',
    );

    const unreachable = setup({
      check: of({
        model: 'gemini-2.0-flash',
        declared: true,
        served: true,
        reachable: false,
        detail: 'Not reachable (ConnectionError).',
      }),
    });
    unreachable.openFirst();
    unreachable.html().querySelector<HTMLElement>('[data-testid^="check-gemini"]')?.click();
    unreachable.fixture.detectChanges();
    expect(
      unreachable.html().querySelector('[data-testid="check-verdict"]')?.textContent,
    ).toContain('Not reachable');
  });

  it('finds a model by its display name and its provider, not only by its id', () => {
    /** Somebody looking for "the cheap Anthropic one" types neither the model id nor the exact
     *  display name, so the haystack carries all three. */
    const harness = setup();
    const view = harness.component as unknown as { view: { search: (v: string) => void } };

    view.view.search('google');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('gemini-2.0-flash');
  });

  it('leaves no declared field out of the panel', () => {
    /**
     * The claim `detailOf` makes is **exhaustiveness**: a catalog entry is what the gateway
     * enforces, so "what does this row actually say" must be answered in full. A field added to
     * `CatalogModel` and forgotten in the panel is a field the console silently does not show, and
     * a partial answer to that question is worse than none.
     *
     * Asserted by populating every field with a value that could not appear by accident and
     * requiring each to be on screen.
     */
    const harness = setup({
      models: of([
        {
          name: 'everything-1',
          display_name: 'Display Name Here',
          provider: 'provider-x',
          publisher: 'publisher-y',
          platform: 'platform-z',
          hosting: 'self_deployed' as const,
          underlying_model: 'underlying-q',
          addressing: { deployment: 'dep-42' },
          capabilities: ['generate' as const, 'tools' as const],
          is_declared: true,
          max_output_tokens: 4096,
          default_max_output_tokens: 512,
          thinking: { modes: ['disabled'] },
          embedding: { supports_batch: true },
          attachments: { media_types: ['application/pdf'] },
          is_priced: true,
          input_price_per_million: '1.2345',
          output_price_per_million: '6.7890',
          numeric_id: 9001,
          deprecated: true,
          updated_at: '2026-08-09T10:00:00Z',
        },
      ]),
    });
    harness.openFirst();
    const shown = harness.text();

    for (const value of [
      'Display Name Here',
      'provider-x',
      'publisher-y',
      'platform-z',
      'self_deployed',
      'underlying-q',
      'dep-42',
      'tools',
      '4096',
      '512',
      'disabled',
      'supports_batch',
      'application/pdf',
      '1.2345',
      '6.7890',
      '9001',
    ]) {
      expect(shown, `the panel does not show ${value}`).toContain(value);
    }
  });

  it('says "—" for a field nobody filled in, and never "null"', () => {
    /** An empty declaration is the ordinary case for a model somebody has only priced. Rendering
     *  `null` or `undefined` into the panel would read as a value. */
    const harness = setup({ models: of([{ name: 'bare-1' }]) });
    harness.openFirst();
    const shown = harness.text();

    expect(shown).not.toContain('null');
    expect(shown).not.toContain('undefined');
    expect(shown).toContain('undeclared');
    expect(shown).toContain('no price');
  });

  it('shows everything on file, so the panel answers what the row abbreviates', () => {
    /** Replaces "keeps a row and its buttons in the same row" (2026-08-09). That test guarded a
     *  `display: flex` on a `<td>`, and this table no longer has an actions cell — the property
     *  did not weaken, its subject left. Four other tables still carry `.table__actions`, and
     *  `console-usability.spec.ts` measures the geometry on one of them in a real browser, which
     *  is the only place `getComputedStyle` means anything. */
    const harness = setup({ models: of(models(1)) });
    harness.openFirst();
    const panel = harness.html();

    expect(panel.querySelector('[data-testid="detail-capabilities"]')).not.toBeNull();
    expect(panel.querySelector('[data-testid="detail-thinking"]')).not.toBeNull();
    expect(panel.querySelector('[data-testid="detail-numeric_id"]')).not.toBeNull();
  });
});

describe('ModelCatalog — importing what the gateway serves (`FRD-507`)', () => {
  it('asks only when asked, and separates catalogued from not', () => {
    /** A list of everything an endpoint offers, loaded on every visit and sitting beside an "Add"
     *  button, reads as a to-do list — one key here answered with 50 models. It is an action. */
    const { component } = setup();
    expect(component.served()).toBeNull();

    component.discover();

    expect(component.served()?.length).toBe(2);
    expect(component.alreadyCatalogued()).toBe(1);
    expect(component.notCatalogued().map((m) => m.name)).toEqual(['gemini-3.1-flash-lite']);
  });

  it('copies where a model lives and nothing else', () => {
    /** **The property an eager implementation breaks.** Provenance is a fact the adapter was
     *  configured with. A capability is a claim (`FRD-131` found a model that lists `tools` and
     *  answers in prose) and a price nobody set is not zero (`FRD-403`) — so the editor still asks,
     *  and an administrator who does not know the price has not declared the model free. */
    const { component } = setup();
    component.discover();

    component.importServed(component.notCatalogued()[0]);

    // The bare name, never Google's `models/…` resource form — that would catalogue an entry no
    // request can match, and it looks right in the table. The service strips it at the edge; this
    // asserts the value that reaches the form.
    expect(component.name()).toBe('gemini-3.1-flash-lite');
    expect(component.name()).not.toContain('models/');
    expect(component.provider()).toBe('generative-language');
    expect(component.publisher()).toBe('google');
    expect(component.inputPrice()).toBe('');
    expect(component.capabilities()).toEqual([]);
    expect(component.approved()).toBe(false);
  });

  it('says so when the catalog already has everything the gateway serves', () => {
    /** "Nothing to import" and "the gateway serves nothing" are different facts, and only one of
     *  them is good news. The empty state names which. */
    const { component, text, fixture } = setup({
      served: of([{ name: 'flash', airaDeclared: true }]),
    });

    component.discover();
    fixture.detectChanges();
    expect(component.notCatalogued()).toEqual([]);
    expect(text()).toContain('Every model the gateway serves is in the catalog');
  });
});

describe('ModelCatalog — discovery when things are missing', () => {
  it('reports a gateway that cannot be asked, instead of an empty list', () => {
    /** An empty list and an unreachable gateway look identical and are fixed differently — the
     *  same distinction `FRD-603` drew between "nothing happened" and "not yours to see". */
    const { component } = setup({ served: throwError(() => ({ status: 503 })) });

    component.discover();

    expect(component.served()).toBeNull();
    expect(component.error()).toBeTruthy();
    expect(component.discovering()).toBe(false);
  });

  it('shows an em dash where an adapter has declared no provenance', () => {
    /** A self-hosted server may name no region, and the mock names nothing at all. Blank stays
     *  blank: an adapter that declared nothing has not made a claim, and rendering an empty string
     *  as a value would turn silence into an assertion. */
    const { component, text, fixture } = setup({
      served: of([{ name: 'mock-1', airaDeclared: false }]),
    });

    component.discover();
    fixture.detectChanges();

    expect(component.notCatalogued().map((m) => m.name)).toEqual(['mock-1']);
    expect(text()).toContain('—');

    component.importServed(component.notCatalogued()[0]);
    expect(component.name()).toBe('mock-1');
    expect(component.provider()).toBe('');
  });
});
