import { TestBed } from '@angular/core/testing';
import { Observable, Subject, map, of, throwError } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import {
  Capability,
  CatalogModel,
  GatewayProvider,
  Me,
  ModelCheck,
  ThinkingLevelCheck,
  OfferedModel,
} from '../../core/api/models';
import { Provenance, UseCaseService } from '../../core/api/use-case.service';
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

/** A provider whose models a request may name directly (`FRD-507` stage B). */
const STUDIO: GatewayProvider = {
  name: 'generative-language',
  label: 'Google AI Studio',
  publisher: 'google',
  region: 'global',
  canEnumerate: true,
  cataloguedIsEnough: true,
  servedModels: 0,
  adapters: 1,
};
/** Two adapters, one provider name, no listing that can answer for the platform. */
const VERTEX: GatewayProvider = {
  name: 'vertex',
  label: 'Google Vertex AI',
  publisher: 'google',
  region: 'europe-west1',
  canEnumerate: false,
  cataloguedIsEnough: false,
  servedModels: 4,
  adapters: 2,
};
const OFFERED_FLASH: OfferedModel = {
  name: 'gemini-flash-latest',
  displayName: 'Gemini Flash Latest',
  description: '',
  maxOutputTokens: 65536,
  canGenerate: true,
  canEmbed: false,
  canCachePrompts: true,
  thinking: true,
};
/** The vendor answered nothing about this one beyond its name — an OpenAI-compatible listing. */
const OFFERED_EMBED: OfferedModel = {
  name: 'text-embedding-004',
  displayName: '',
  description: '',
  maxOutputTokens: null,
  canGenerate: null,
  canEmbed: null,
  canCachePrompts: null,
  thinking: null,
};

interface Catalog {
  add: () => void;
  openProvenance: () => void;
  showProvenance: () => boolean;
  provenanceKnown: () => boolean;
  discover: () => void;
  discovering: () => boolean;
  served: () => unknown[] | null;
  notCatalogued: () => { name: string }[];
  alreadyCatalogued: () => number;
  importServed: (model: unknown) => void;
  provider: { set: (v: string) => void; (): string };
  approved: { set: (v: boolean) => void; (): boolean };
  models: () => CatalogModel[];
  view: { rows: () => CatalogModel[] };
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
  contextWindow: { set: (v: number | null) => void; (): number | null };
  maxOutput: { set: (v: number | null) => void; (): number | null };
  kiraId: { set: (v: number | null) => void; (): number | null };
  editorTab: { set: (v: string) => void; (): string };
  defaultOutput: { set: (v: number | null) => void; (): number | null };
  deprecated: { set: (v: boolean) => void; (): boolean };
  thinkingModes: { set: (v: string[]) => void; (): string[] };
  modeVerdicts: () => Record<string, { ok: boolean; detail: string }>;
  thinkingMin: { set: (v: number | null) => void; (): number | null };
  thinkingMax: { set: (v: number | null) => void; (): number | null };
  thinkingDefault: { set: (v: string) => void; (): string };
  thinkingLevels: { set: (v: string[]) => void; (): string[] };
  levelDraft: { set: (v: string) => void; (): string };
  addLevel: () => void;
  removeLevel: (word: string) => void;
  checkLevels: () => void;
  levelVerdict: (word: string) => { ok: boolean; detail: string } | null;
  declarableDefaults: () => string[];
  toggleThinkingMode: (mode: string, on: boolean) => void;
  dimensions: { set: (v: string) => void; (): string };
  defaultDimension: { set: (v: string | number) => void; (): string | number };
  taskTypes: { set: (v: string) => void; (): string };
  supportsBatch: { set: (v: boolean) => void; (): boolean };
  declaredDimensions: () => number[];
  mediaTypes: { set: (v: string[]) => void; (): string[] };
  toggleMediaType: (mediaType: string, on: boolean) => void;
  mediaTypeTokens: (mediaType: string) => number | null;
  showAdd: { set: (v: boolean) => void; (): boolean };
  name: { set: (v: string) => void; (): string };
  inputPrice: { set: (v: string) => void; (): string };
  outputPrice: { set: (v: string) => void; (): string };
  formError: () => string | null;
  canSave: () => boolean;
  save: () => void;
  edit: (m: CatalogModel) => void;
  remove: (m: CatalogModel) => void;
  providers: () => GatewayProvider[] | null;
  allowedRegions: () => string[];
  regionPermitted: () => boolean | null;
  providersError: () => string | null;
  providerIsCustom: { set: (v: boolean) => void; (): boolean };
  chooseProvider: (value: string) => void;
  selectedProvider: () => GatewayProvider | null;
  offerings: () => OfferedModel[] | null;
  offeringsError: () => string | null;
  useOffered: (model: OfferedModel) => void;
  providerLabel: (provider: GatewayProvider) => string;
  openBrowse: () => void;
  closeBrowse: () => void;
  showBrowse: () => boolean;
  browseProvider: { set: (v: string) => void; (): string };
  browseSearch: { set: (v: string) => void; (): string };
  browseMatches: () => OfferedModel[];
  askable: () => GatewayProvider[];
  catalogueOffered: (model: OfferedModel) => void;
  chooseBrowseProvider: (name: string) => void;
  editing: () => string;
  vendorFilled: () => string[];
  vendorSaid: () => string[];
  displayName: { set: (v: string) => void; (): string };
  platform: { set: (v: string) => void; (): string };
  checkedName: () => string | null;
  check: () => { reachable: boolean | null; served: boolean; detail?: string } | null;
  addManually: () => void;
  addByName: () => void;
  regions: { set: (v: string[]) => void; (): string[] };
  regionDraft: { set: (v: string) => void; (): string };
  addRegion: () => void;
  removeRegion: (region: string) => void;
  regionVerdict: (region: string) => { ok: boolean; detail: string } | null;
  regionAllowed: (region: string) => boolean | null;
  regionVerdicts: { set: (v: Record<string, { ok: boolean; detail: string }>) => void };
  forbiddenRegions: () => string[];
  mustCheck: () => boolean;
  OTHER: string;
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
    /** One per call, in order — so a test can let the first succeed and the second fail. */
    levelChecks?: Observable<ThinkingLevelCheck>[];
    /** What the gateway is configured with (`FRD-507` stage C). */
    providers?: Observable<GatewayProvider[]>;
    /** Where this installation permits processing, as the gateway states it (`ADR-0012` §6). */
    allowedRegions?: string[];
    /** What one provider answers when asked what it offers. */
    offerings?: Observable<OfferedModel[]>;
  } = {},
) {
  const checked: string[] = [];
  const askedLevels: {
    model: string;
    levels: string[];
    where: Provenance;
    modes: string[];
  }[] = [];
  const checkedWhere: Provenance[] = [];
  const asked: string[] = [];
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
          checkModel: (name: string, where: Provenance = {}) => {
            checked.push(name);
            checkedWhere.push(where);
            return options.check ? options.check : of(CHECK);
          },
          checkThinkingLevels: (
            name: string,
            levels: string[],
            where: Provenance = {},
            modes: string[] = [],
          ) => {
            askedLevels.push({ model: name, levels, where, modes });
            return (
              options.levelChecks?.shift() ??
              of({
                model: name,
                // The dialect's answer about each ticked mode. `auto` is the one every
                // OpenAI-family adapter declares it cannot express, and the one an administrator
                // could tick in silence until this button reported it.
                modes: modes.map((mode) => ({
                  mode,
                  accepted: mode !== 'auto',
                  detail:
                    mode === 'auto'
                      ? "This model's wire format has no way to say 'auto'. Declaring it here " +
                        'means every request that asks for it is refused — the model is never ' +
                        'reached.'
                      : "This model's wire format can express it.",
                })),
                results: levels.map((level) => ({
                  region: '',
                  level,
                  accepted: level !== 'medium',
                  detail:
                    level === 'medium'
                      ? 'Vertex upstream returned 400. thinking_level is not supported…'
                      : 'The model accepted it.',
                })),
              })
            );
          },
          models: () => options.models ?? of([FLASH, UNPRICED]),
          providers: () =>
            (options.providers ?? of([STUDIO, VERTEX])).pipe(
              map((providers: GatewayProvider[]) => ({
                providers,
                allowedRegions: options.allowedRegions ?? ['eu', 'europe-west1'],
              })),
            ),
          providerOfferings: (name: string) => {
            asked.push(name);
            return options.offerings ?? of([OFFERED_FLASH, OFFERED_EMBED]);
          },
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
    asked,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    checked,
    askedLevels,
    checkedWhere,
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
    // **Through the one entrance there is.** There were two buttons and the empty form was one of
    // them; adding now starts by saying where the model lives, and only a platform that cannot be
    // listed reaches a form to type into. Driven through the DOM the whole way, because the point
    // of the change is what a person can click.
    html().querySelector<HTMLButtonElement>('[data-testid="add-model"]')!.click();
    harness.fixture.detectChanges();
    harness.component.browseProvider.set('vertex');
    harness.fixture.detectChanges();
    html().querySelector<HTMLButtonElement>('[data-testid="add-manually"]')!.click();
    harness.fixture.detectChanges();

    const dialog = html().querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.getAttribute('aria-modal')).toBe('true');

    const name = html().querySelector<HTMLInputElement>('#model-name');
    expect(name).not.toBeNull();
    // The prices moved behind their own tab: eighteen fields in one column had the input price
    // sitting between the provider and the publisher. A reader looking for what a model *costs*
    // now opens the tab that says so, and this follows them there.
    html().querySelector<HTMLButtonElement>('[data-testid="tab-price"]')!.click();
    harness.fixture.detectChanges();
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

  it('offers the context window, and sends what was typed into it', async () => {
    /**
     * `FRD-132` §11, and the half `test_every_model_control_is_reachable.py` cannot see: that guard
     * asserts a writable serializer field has a payload key, this one asserts the control reaches
     * it. Neither alone would have caught `numeric_id`, which had no control *and* no key.
     *
     * The field exists because a client has nowhere else to learn the figure — an assistant's
     * "12% of the context used" divides by it, and with nothing declared OpenCode showed 0%.
     */
    const page = setup();
    page.component.showAdd.set(true);
    page.component.name.set('qwen3:0.6b');
    page.component.toggleCapability('generate', true);
    page.component.contextWindow.set(40960);
    page.component.maxOutput.set(4096);
    page.lookFirst();
    page.component.save();

    expect(page.saved[page.saved.length - 1].context_window).toBe(40960);
  });

  it('offers the KIRA id, and sends what was typed into it', async () => {
    /** The field the form never had. `numeric_id` is how a KIRA client names a model — that
     *  surface identifies models by integer, not by name — and the API has always accepted it, so
     *  every model catalogued from this screen went in with `NULL`: approvable, releasable, and
     *  invisible to `/kira/api/external/chat`, which answers `MODEL_NOT_FOUND` and names nothing
     *  that would tell the reader why. The detail panel even printed "KIRA id —", so the field was
     *  visible and unsettable.
     *
     *  Typed into the real input rather than set on the signal: the defect being guarded against is
     *  a control that renders and sends nothing, and a signal set from a test renders nothing. */
    const page = setup();
    page.component.showAdd.set(true);
    page.component.name.set('legacy-1');
    page.component.toggleCapability('generate', true);
    page.lookFirst();
    // `ngModel` inside a `<form>` registers its control on a microtask, so a value typed before
    // that flush reaches nothing. The wait is the test being a real interaction, not a signal set.
    await Promise.resolve();

    expect(page.testid('model-kira-id')).not.toBeNull();
    page.type('model-kira-id', '4711');
    expect(page.component.kiraId()).toBe(4711);

    page.component.save();
    expect(page.saved[page.saved.length - 1].numeric_id).toBe(4711);
  });

  it('leaves the KIRA id to the server when it is not typed', () => {
    /** Blank is not "none": the server assigns the next free number, so a model catalogued without
     *  a thought about KIRA is still addressable there. What must not be sent is a `0` or an empty
     *  string, which the server would have to refuse. */
    const page = setup();
    page.component.showAdd.set(true);
    page.component.name.set('plain-1');
    page.component.toggleCapability('generate', true);
    page.lookFirst();
    page.component.save();

    expect(page.saved[page.saved.length - 1].numeric_id).toBeNull();
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
          // An **object**, keyed by media type, with an optional per-type estimate. It was a
          // list here, which the server refuses ("media_types must be a non-empty object") —
          // invisible while the field was typed `Record<string, unknown>`, and named by the
          // compiler the moment the declaration got a real type.
          attachments: { media_types: { 'application/pdf': { tokens: 258 } } },
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

    // **And two it must not show.** `underlying_model` and `addressing` are stored, carried to the
    // gateway, and read by no dispatch decision. Printed among Provider, Platform and Hosting they
    // read as configuration — the same misreading that made "KIRA id —" look like a field somebody
    // had left blank rather than one nobody could fill. Either a reader appears and they get a
    // control (which `test_every_model_control_is_reachable.py` then requires), or they stay off
    // the panel.
    expect(shown).not.toContain('underlying-q');
    expect(shown).not.toContain('dep-42');
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

describe('ModelCatalog — the provider field in the editor (`FRD-507` stage C)', () => {
  it('offers what the gateway is configured with, in the DOM, rather than a typed string', () => {
    /** The field was a text box, so a model was declared under whatever somebody typed — and the
     *  two refusals a typo produces (`not in the model catalog`, `has not been approved`) are both
     *  correct and neither names the string that was wrong.
     *
     *  Asserted on the rendered `<option>`s: a component that held the list and rendered a text
     *  input would pass every signal assertion in this file. */
    const { component, fixture, html } = setup();

    component.add();
    fixture.detectChanges();

    const select = html().querySelector<HTMLSelectElement>('[data-testid="provider-select"]');
    expect(select).not.toBeNull();
    const options = [...select!.options].map((o) => o.value);
    expect(options).toContain('generative-language');
    expect(options).toContain('vertex');
    // The escape hatch is always there: declaring a model before its platform is configured is
    // the ordinary order of work, and a closed list would forbid it.
    expect(options).toContain(component.OTHER);
  });

  it('names the vendor and keeps the identifier visible', () => {
    /** `generative-language` beside `local` names neither vendor — reported from the running
     *  console. The label leads and the name stays in brackets, because the name is what gets
     *  written into the catalog and onto every audit row. */
    const { component } = setup();

    expect(component.providerLabel(STUDIO)).toContain('Google AI Studio');
    expect(component.providerLabel(STUDIO)).toContain('generative-language');
    expect(component.providerLabel(VERTEX)).toContain('europe-west1');
  });

  it('says whether cataloguing the model will be enough to reach it', () => {
    /** The field that decides whether an import produces a working model or a convincing
     *  decoration. Where the model name is not the whole addressing, the model must also be named
     *  in the gateway's configuration — and an administrator who is not told finds out from a
     *  caller. */
    const { component, fixture, text } = setup();

    component.add();
    component.chooseProvider('generative-language');
    fixture.detectChanges();
    expect(text()).toContain('is enough to reach it');

    component.chooseProvider('vertex');
    fixture.detectChanges();
    expect(text()).toContain("named in the gateway's configuration");
  });

  it('falls back to a typed provider when the gateway cannot be asked, and still saves', () => {
    /** Informs, never blocks. Declaring a model before its credential exists is the ordinary order
     *  of work, so a gateway that cannot be reached degrades to the text box it replaced rather
     *  than locking somebody out of their own catalog — `FRD-114`'s rule that deprecation warns
     *  and revocation blocks, one screen over. */
    const { component, fixture, html, text } = setup({
      providers: throwError(() => ({ status: 503 })),
    });

    component.add();
    fixture.detectChanges();

    expect(component.providerIsCustom()).toBe(true);
    expect(text()).toContain('Type the provider name instead');
    expect(html().querySelector('[data-testid="provider-select"]')).toBeNull();
    expect(html().querySelector('[data-testid="provider-typed"]')).not.toBeNull();
  });

  it('lets a provider be typed and taken back to the list', () => {
    const { component, fixture, html } = setup();

    component.add();
    component.chooseProvider(component.OTHER);
    fixture.detectChanges();
    expect(component.provider()).toBe('');
    expect(html().querySelector('[data-testid="provider-typed"]')).not.toBeNull();

    html().querySelector<HTMLButtonElement>('[data-testid="provider-back-to-list"]')!.click();
    fixture.detectChanges();
    expect(html().querySelector('[data-testid="provider-select"]')).not.toBeNull();
  });

  it('keeps a publisher the administrator typed when the provider declares none', () => {
    /** A provider that states no publisher has not said the field is empty. Overwriting a value
     *  somebody entered with a blank is the import rule inverted: silence becoming a decision. */
    const { component } = setup({
      providers: of([{ ...STUDIO, publisher: '', region: '', label: '' }]),
    });

    component.add();
    component.publisher.set('google');
    component.chooseProvider('generative-language');

    expect(component.publisher()).toBe('google');
    // And a provider with neither label nor region is named by itself rather than trailed by a
    // separator or wrapped in brackets around its own name.
    expect(component.providerLabel(component.providers()![0])).toBe('generative-language');
  });

  it('shows a configured provider as chosen even when the list arrives after the form', async () => {
    /** Opening the editor is what fetches the list, so a form carrying a provider is laid out
     *  before the answer comes back. A one-way rule left a perfectly configured provider stuck in
     *  the text box it was supposed to replace, for every model opened faster than the gateway
     *  answered.
     *
     *  The **arrival has to be late**, or this proves nothing: a stubbed `of()` answers inside the
     *  call that started it, so the editor's own guess is what the assertion would be reading and
     *  the deferred rule would never run. Written first with `of()`, where it passed against the
     *  broken code — the failure this project keeps recording as *a test that never reached the
     *  path it was named after*. */
    const late = new Subject<GatewayProvider[]>();
    const { component, fixture, html } = setup({ providers: late });

    component.edit({ ...FLASH, provider: 'vertex' });
    // A row on file states where the model lives, so the editor summarises it rather than asking
    // again. This test is about the select's behaviour, so it opens the block the way a reader
    // correcting the provenance would.
    component.openProvenance();
    fixture.detectChanges();
    // Nothing is known yet, so the field is the text box it degrades to.
    expect(component.providerIsCustom()).toBe(true);

    late.next([STUDIO, VERTEX]);
    late.complete();
    fixture.detectChanges();
    // `ngModel` writes the DOM value on a microtask: the select is a *form control*, not an
    // interpolation.
    await fixture.whenStable();

    expect(component.providerIsCustom()).toBe(false);
    expect(html().querySelector<HTMLSelectElement>('[data-testid="provider-select"]')!.value).toBe(
      'vertex',
    );
  });

  it('leaves the provider select alone for a model that has no provider at all', () => {
    /** An older catalog row, or one added before this field meant anything. Blank is not a typed
     *  value: the select simply shows nothing chosen. */
    const { component } = setup();

    component.edit({ ...UNPRICED });

    expect(component.provider()).toBe('');
    expect(component.providerIsCustom()).toBe(false);
  });
});

describe('ModelCatalog — one entrance, and what it stops asking', () => {
  /**
   * The owner's verdict on the two buttons that used to sit here: *"the options for adding a
   * model are too complex … if somebody other than me is to add one, that person will not
   * understand the screen"*, and *"the Add model button, where you have to type everything
   * yourself, is by now unnecessary — we have adding from a provider."*
   *
   * Both halves are one property: **a model lives somewhere, and the software already knows
   * where.** The empty form asked eight questions and five of them — provider, publisher,
   * platform, hosting, the KIRA id — were answered when the provider was chosen.
   */
  it('offers one way in, and it starts with the provider', () => {
    const { component, html } = setup();

    const buttons = [...html().querySelectorAll<HTMLButtonElement>('button')].filter((button) =>
      /add a model|add model/i.test(button.textContent ?? ''),
    );
    expect(buttons.length).toBe(1);

    buttons[0].click();
    // The browse window, not the editor: nothing is typed before it is known where it goes.
    expect(component.showBrowse()).toBe(true);
    expect(component.showAdd()).toBe(false);
  });

  it('states where the model lives instead of asking, once something knows', () => {
    const { component, fixture, html } = setup();

    component.addManually();
    component.provider.set('vertex');
    component.publisher.set('google');
    component.platform.set('vertex');
    fixture.detectChanges();

    const summary = html().querySelector('[data-testid="provenance-summary"]');
    expect(summary).not.toBeNull();
    // The **whole sentence**, not a substring of it. A summary assembled from template blocks
    // strands its punctuation — the formatter puts each `@if` on its own line, and the text node
    // that follows begins with a newline, so a reader gets "vertex , speaking". Asserting
    // `toContain('vertex')` would pass through that happily.
    expect(summary!.textContent!.replace(/\s+/g, ' ').trim()).toBe(
      'Lives on Google Vertex AI (vertex), speaking the google dialect, on the vertex platform. Change',
    );
    // The four boxes are gone — that is the whole point — while the two questions the provider
    // cannot answer stay in plain sight.
    expect(html().querySelector('[data-testid="provenance-fields"]')).toBeNull();
    expect(html().querySelector('#model-name')).not.toBeNull();
    expect(html().querySelector('#model-region')).not.toBeNull();
  });

  it('asks where the model lives when nothing knows', () => {
    /** The empty form is the one case where those fields really are the reader's to answer, so
     *  there it opens by itself. Hiding a question nobody else can answer is not simplicity. */
    const { component, fixture, html } = setup();

    component.addByName();
    fixture.detectChanges();

    expect(component.provenanceKnown()).toBe(false);
    expect(html().querySelector('[data-testid="provenance-fields"]')).not.toBeNull();
    expect(html().querySelector('[data-testid="provenance-summary"]')).toBeNull();
  });

  it('keeps the fields on screen while the reader is using them', () => {
    /** **The defect this test was written after.** Without a latch, the empty form opened the
     *  fields (nothing was known), the reader picked a provider from the select — and "a provider
     *  is now known" collapsed the block, taking the select out of the DOM under the pointer that
     *  had just used it. A rule about what a form knows, applied while somebody is telling it. */
    const { component, fixture, html } = setup();

    component.addByName();
    fixture.detectChanges();
    component.chooseProvider('vertex');
    fixture.detectChanges();

    expect(component.provenanceKnown()).toBe(true);
    expect(html().querySelector('[data-testid="provenance-fields"]')).not.toBeNull();
  });

  it('opens the fields on request, for a row that already states them', () => {
    const { component, fixture, html } = setup();

    component.edit({ ...FLASH, provider: 'vertex' });
    fixture.detectChanges();
    expect(html().querySelector('[data-testid="provenance-fields"]')).toBeNull();

    html().querySelector<HTMLButtonElement>('[data-testid="change-provenance"]')!.click();
    fixture.detectChanges();
    expect(html().querySelector('[data-testid="provenance-fields"]')).not.toBeNull();
  });

  it('does not carry one model’s reachability verdict onto a form for another', () => {
    /** **The second defect this pass found.** Two entrances to one window and only one of them
     *  swept the floor: `add()` clears the verdict with a comment saying why, and the manual route
     *  opened the same window beside it. Checking a row and then adding a model showed a green
     *  badge about a different model — the wrong answer wearing a right one's clothes. */
    const harness = setup({
      check: of({
        model: 'gemini-2.0-flash',
        declared: true,
        served: true,
        reachable: true,
        detail: 'gemini-2.0-flash answered',
      }),
    });
    const { component, fixture, html } = harness;

    harness.openFirst();
    html().querySelector<HTMLElement>('[data-testid^="check-gemini"]')!.click();
    fixture.detectChanges();
    expect(component.check()).not.toBeNull();

    component.addManually();
    fixture.detectChanges();
    expect(component.check()).toBeNull();
    expect(html().querySelector('[data-testid="editor-verdict"]')).toBeNull();
  });

  it('leaves a way forward when there is no provider to choose', () => {
    /** The empty form is still reachable, from the one place it is the honest option rather than
     *  the tempting one: a gateway with no upstream configured has nothing to fill anything in
     *  from. Removing the button without this would have removed the only way to declare a model
     *  on a fresh installation. */
    const { component, fixture, html } = setup({ providers: of([]) });

    component.openBrowse();
    fixture.detectChanges();
    expect(html().querySelector('[data-testid="browse-no-providers"]')).not.toBeNull();

    html().querySelector<HTMLButtonElement>('[data-testid="add-by-name"]')!.click();
    fixture.detectChanges();

    expect(component.showBrowse()).toBe(false);
    expect(component.showAdd()).toBe(true);
    expect(html().querySelector('#model-name')).not.toBeNull();
  });
});

describe('ModelCatalog — browsing what a provider offers (`FRD-507` stage C)', () => {
  it('lists every provider, and says which one publishes no list', () => {
    /** `canEnumerate` is stated rather than discovered by trying, and a capability gap must never
     *  be reported as a fault — those send a reader to two different systems.
     *
     *  **But it was reported as nothing at all.** A provider without a listing was filtered out of
     *  this window, and somebody who had just configured Agent Platform opened *Add from provider*
     *  and found no mention of it: indistinguishable from a credential that had not worked. It is
     *  listed now, marked, with the way in beside it — no error, and no silence either.
     */
    const { fixture, html } = setup();

    html().querySelector<HTMLButtonElement>('[data-testid="add-model"]')!.click();
    fixture.detectChanges();

    const select = html().querySelector<HTMLSelectElement>('[data-testid="browse-provider"]')!;
    const values = [...select.options].map((o) => o.value).filter(Boolean);
    expect(values).toEqual(['generative-language', 'vertex']);
    expect(select.textContent).toContain('publishes no list');
    // Still no error anywhere: the gap is a property of the platform.
    expect(html().querySelector('[data-testid="browse-providers-error"]')).toBeNull();
  });

  it('offers the manual route for a provider that cannot be listed', () => {
    /** "Type everything" is not an answer. What the platform tells us about itself is filled in,
     *  and the reader names the model — which is what choosing an offered one does, one step
     *  earlier. */
    const { component, fixture, html } = setup();

    html().querySelector<HTMLButtonElement>('[data-testid="add-model"]')!.click();
    fixture.detectChanges();
    component.browseProvider.set('vertex');
    fixture.detectChanges();

    expect(html().querySelector('[data-testid="provider-not-askable"]')).not.toBeNull();
    html().querySelector<HTMLButtonElement>('[data-testid="add-manually"]')!.click();
    fixture.detectChanges();

    expect(component.provider()).toBe('vertex');
    expect(html().querySelector('#model-name')).not.toBeNull();
  });

  it('asks straight away when there is only one provider to ask', () => {
    /** A select with a single option is a click that teaches nothing. Two or more and the reader
     *  picks — asserted below by *not* preselecting. */
    const one = setup();
    one.component.openBrowse();
    expect(one.asked).toEqual(['generative-language']);

    const two = setup({ providers: of([STUDIO, { ...STUDIO, name: 'ollama', label: 'ollama' }]) });
    two.component.openBrowse();
    expect(two.asked).toEqual([]);
  });

  /**
   * Reported from the console: open the listing, click *Catalogue…*, cancel, open it again — the
   * provider is still selected and nothing loads.
   *
   * `catalogueOffered` closes the dialog **without** clearing the provider, deliberately: it needs
   * it afterwards to record where the model came from. `openBrowse` then asked for the offerings
   * only where no provider was chosen, so a remembered one skipped the fetch. Half the state kept
   * and half dropped — the select said AI Studio and there was nothing under it, with no error.
   *
   * Asserted on **what was asked of the gateway**, not on what is on screen: an empty list and a
   * list that was never requested render identically, which is exactly why nobody caught this.
   */
  it('asks again for a provider that is still selected when the picker reopens', () => {
    const harness = setup({
      providers: of([STUDIO, { ...STUDIO, name: 'ollama', label: 'ollama' }]),
    });
    harness.component.openBrowse();
    harness.component.chooseBrowseProvider('generative-language');
    expect(harness.asked).toEqual(['generative-language']);

    // Catalogue something, which closes the window and keeps the provider, then cancel and reopen.
    harness.component.catalogueOffered(OFFERED_FLASH);
    harness.component.openBrowse();

    expect(harness.component.browseProvider()).toBe('generative-language');
    expect(harness.asked).toEqual(['generative-language', 'generative-language']);
  });

  /**
   * The same half-state one step along. A remembered provider this gateway no longer offers would
   * leave the select showing a name it cannot resolve — and the fetch would be for a provider that
   * is not in the list the reader can choose from.
   */
  it('forgets a remembered provider the gateway no longer offers', () => {
    const harness = setup({ providers: of([STUDIO]) });
    harness.component.openBrowse();
    harness.component.chooseBrowseProvider('gone-away');
    harness.component.openBrowse();

    expect(harness.component.browseProvider()).toBe('');
  });

  it('lists what the vendor offers, searchably, and marks what the catalog already has', () => {
    const { component, fixture, html, text } = setup({
      offerings: of([OFFERED_FLASH, OFFERED_EMBED, { ...OFFERED_FLASH, name: 'gemini-2.0-flash' }]),
    });

    component.openBrowse();
    fixture.detectChanges();

    expect(text()).toContain('gemini-flash-latest');
    expect(text()).toContain('3 offered by this credential');
    // Marked, never hidden: the reader would otherwise check each one against the table behind
    // the window to find out which are new.
    expect(text()).toContain('in the catalog');

    component.browseSearch.set('embed');
    fixture.detectChanges();
    expect(component.browseMatches().map((m) => m.name)).toEqual(['text-embedding-004']);
    expect(html().textContent).not.toContain('gemini-flash-latest');
  });

  it('says a provider answered with nothing rather than looking like it was never asked', () => {
    /** An empty listing is a fact about the credential — a key with no models enabled lists none
     *  — and a blank window is indistinguishable from a window that failed. */
    const { component, fixture, text } = setup({ offerings: of([]) });

    component.openBrowse();
    fixture.detectChanges();

    expect(text()).toContain('answered with no models at all');
  });

  it('reports a provider that would not answer, inside the window it was asked in', () => {
    /** A red bar on the page behind an open modal is a report about nothing. */
    const { component, fixture, text } = setup({ offerings: throwError(() => ({ status: 502 })) });

    component.openBrowse();
    fixture.detectChanges();

    expect(component.offeringsError()).toBeTruthy();
    expect(component.error()).toBeNull();
    expect(text()).toContain('Could not ask this provider');
  });

  it('tells a missing credential apart from a platform that cannot be asked', () => {
    /** Two different facts that were one message until somebody would have read the wrong one.
     *  **No provider configured** is a missing credential — an adapter is registered only when its
     *  credential is, so a gateway with no Google key and no self-hosted server has nobody to ask,
     *  and the fix is in the gateway's environment. **None publishes a list** is a missing
     *  capability, and it is not fixed at all: those models are named by hand.
     *
     *  An empty dropdown would have said neither, and sent somebody looking for a broken key that
     *  does not exist. */
    const none = setup({ providers: of([]) });
    none.component.openBrowse();
    none.fixture.detectChanges();
    expect(none.text()).toContain('no upstream configured');

    const mute = setup({ providers: of([VERTEX]) });
    mute.component.openBrowse();
    mute.fixture.detectChanges();
    expect(mute.text()).toContain('none of them publishes a model list');
  });

  it('hands one model to the editor with what the vendor stated, and nothing else', () => {
    /** The whole design in one assertion, and the property an eager implementation breaks.
     *
     *  Copied: the name, the display name, the output ceiling and the verbs — all facts, because
     *  the API refuses a larger request and answers 404 for a method missing from its own list.
     *  Not copied: the price (a price nobody set is not zero), and `thinking` — the vendor's flag
     *  says a model reasons and `FRD-114` needs the modes and the budgets, which no listing
     *  publishes. */
    const { component, fixture, html, text } = setup();

    component.openBrowse();
    fixture.detectChanges();
    html().querySelector<HTMLButtonElement>('[data-testid="offered-gemini-flash-latest"]')!.click();
    fixture.detectChanges();

    // One window at a time: the list is a choice, made once.
    expect(component.showBrowse()).toBe(false);
    expect(component.showAdd()).toBe(true);

    expect(component.name()).toBe('gemini-flash-latest');
    expect(component.displayName()).toBe('Gemini Flash Latest');
    expect(component.maxOutput()).toBe(65536);
    expect(component.provider()).toBe('generative-language');
    expect(component.publisher()).toBe('google');
    expect(component.platform()).toBe('generative-language');
    expect(component.capabilities().sort()).toEqual(['generate', 'prompt_caching']);

    expect(component.inputPrice()).toBe('');
    expect(component.outputPrice()).toBe('');
    expect(component.approved()).toBe(false);
    expect(component.hasCapability('thinking')).toBe(false);
    expect(component.hasCapability('tools')).toBe(false);

    // And it says which half is which, because an import that silently fills six fields and
    // silently leaves five is indistinguishable from one that failed at the other five.
    //
    // Two statements, deliberately not one: the **sentence** says what is now known — that half
    // used to be six empty boxes — and the **note** says what the import left alone. They said
    // the same thing for a while, one line apart, which reads as two facts and sends a reader
    // looking for the difference.
    expect(text()).toContain('Lives on Google AI Studio (generative-language)');
    expect(text()).toContain('a price nobody set is not zero');
    expect(text()).not.toContain('Filled in from');
    expect(component.vendorSaid().join(' ')).toContain('it reasons');
  });

  it('opens the existing declaration for a model the catalog already has', () => {
    /** Corrected, never added a second time — and never as a blank form carrying the vendor's
     *  answer, which would replace a measured capability or a price with a claim. */
    const { component, fixture, html } = setup({
      offerings: of([{ ...OFFERED_FLASH, name: 'gemini-2.0-flash' }]),
    });

    component.openBrowse();
    fixture.detectChanges();
    html().querySelector<HTMLButtonElement>('[data-testid="offered-gemini-2.0-flash"]')!.click();

    expect(component.editing()).toBe('gemini-2.0-flash');
    expect(component.inputPrice()).toBe('0.075');
  });

  it('turns a vendor saying nothing into no declaration at all', () => {
    /** `null` is a third answer, not a missing one. An OpenAI-compatible listing publishes bare
     *  ids, and `false` would be a *statement* that the model cannot generate — pre-filled into a
     *  form somebody is about to save, it becomes their decision (`FRD-114` FR-7). */
    const { component } = setup({ offerings: of([OFFERED_EMBED]) });

    component.openBrowse();
    component.catalogueOffered(component.offerings()![0]);

    expect(component.name()).toBe('text-embedding-004');
    expect(component.capabilities()).toEqual([]);
    expect(component.maxOutput()).toBeNull();
  });

  it('adds capabilities and never removes one the administrator ticked', () => {
    /** A vendor's silence must not untick a box somebody ticked from a measurement they made —
     *  which is the direction that matters, since the catalog is where measurements are kept. */
    const { component } = setup();

    component.openBrowse();
    component.capabilities.set(['tools']);
    component.useOffered(OFFERED_FLASH);

    expect(component.capabilities().sort()).toEqual(['generate', 'prompt_caching', 'tools']);
  });

  it('counts a live listing as having looked — but only where cataloguing is enough', () => {
    /** `mustCheck` refuses the *ignorance*, not the verdict, and a listing the vendor answered a
     *  second ago is a stronger answer than a ping. Where cataloguing is **not** enough the model
     *  still needs a configuration entry, and the check is exactly what says so — so that case
     *  keeps the gate. */
    const enough = setup();
    enough.component.openBrowse();
    enough.component.catalogueOffered(OFFERED_FLASH);
    expect(enough.component.mustCheck()).toBe(false);

    const notEnough = setup({
      providers: of([{ ...STUDIO, name: 'half-wired', cataloguedIsEnough: false }]),
    });
    notEnough.component.openBrowse();
    notEnough.component.catalogueOffered(OFFERED_FLASH);
    expect(notEnough.component.mustCheck()).toBe(true);
  });

  it('closes without touching the form behind it', () => {
    /** The browse window has its own provider signal. Looking at a list must not edit a
     *  half-finished declaration underneath — and the two would otherwise be one field. */
    const { component, fixture } = setup();

    component.add();
    component.chooseProvider('vertex');
    component.openBrowse();
    fixture.detectChanges();
    component.closeBrowse();

    expect(component.provider()).toBe('vertex');
    expect(component.offerings()).toBeNull();
    expect(component.browseProvider()).toBe('');
  });
});

describe('ModelCatalog — the browse window when things are half-there', () => {
  it('going back to "choose a provider" clears the list instead of leaving a stale one', () => {
    /** A list left standing under a provider that is no longer chosen is a wrong answer wearing a
     *  right one's clothes — the same rule the reachability verdict follows when another row
     *  opens. */
    const { component, asked, fixture } = setup({ providers: of([STUDIO, VERTEX]) });

    // One askable provider, so opening the window already asked it.
    component.openBrowse();
    fixture.detectChanges();
    expect(component.offerings()?.length).toBe(2);

    component.chooseBrowseProvider('');
    fixture.detectChanges();
    expect(component.offerings()).toBeNull();
    expect(asked).toEqual(['generative-language']);
  });

  it('opens without a provider list yet, and asks for it once', () => {
    /** The window can be opened before the gateway has answered — clicking twice must not queue a
     *  second identical question. */
    const late = new Subject<GatewayProvider[]>();
    const { component, fixture } = setup({ providers: late });

    component.openBrowse();
    component.openBrowse();
    fixture.detectChanges();
    expect(component.askable()).toEqual([]);
    expect(component.browseMatches()).toEqual([]);

    late.next([STUDIO]);
    late.complete();
    fixture.detectChanges();
    expect(component.askable().length).toBe(1);
  });

  it('catalogues a model even when nothing is known about the provider it came from', () => {
    /** `catalogueOffered` reads the provider for the provenance it copies, and a window opened
     *  against a list that never arrived has none. The model is still the model: the form opens
     *  with the name and without a claim about where it lives. */
    const { component } = setup({ providers: throwError(() => ({ status: 503 })) });

    component.openBrowse();
    component.catalogueOffered(OFFERED_FLASH);

    expect(component.showAdd()).toBe(true);
    expect(component.name()).toBe('gemini-flash-latest');
    expect(component.provider()).toBe('');
  });

  it("carries an embedding verb and the vendor's own description", () => {
    const { component, fixture, text } = setup({
      offerings: of([
        {
          ...OFFERED_EMBED,
          canEmbed: true,
          description: 'Obtain a distributed representation of a text.',
        },
      ]),
    });

    component.openBrowse();
    component.catalogueOffered(component.offerings()![0]);
    fixture.detectChanges();

    expect(component.capabilities()).toEqual(['embed']);
    expect(text()).toContain('distributed representation');
  });

  it("reads the editor's own provider when a model is filled in outside the window", () => {
    /** `useOffered` is reachable from the editor too, and "is cataloguing enough to reach this"
     *  has to be answered about the provider on the *form* then — not about a window that is not
     *  open. */
    const { component } = setup();

    component.add();
    component.chooseProvider('generative-language');
    component.useOffered(OFFERED_FLASH);

    expect(component.mustCheck()).toBe(false);
  });
});

describe('ModelCatalog — what a reader is looking for comes first', () => {
  it('lists the released models above the rest', () => {
    /** A catalog only grows, and somebody arriving here is almost always asking about a model that
     *  is **in use** — which an alphabetical list buries among drafts, retirements and the long
     *  tail of a vendor's listing. `approved` is the one property that says "this is live"
     *  (`FRD-307`), so it is the one that orders the table. */
    const { component } = setup({
      models: of([
        { name: 'aaa-draft', approved: false, is_priced: false },
        { name: 'zzz-released', approved: true, is_priced: false },
        { name: 'mmm-released', approved: true, is_priced: false },
      ]),
    });

    expect(component.view.rows().map((m: CatalogModel) => m.name)).toEqual([
      'mmm-released',
      'zzz-released',
      'aaa-draft',
    ]);
  });

  it('counts its warnings over the whole catalog, not over what is on top', () => {
    /** The reason the ordering is done here and not at the server: two of this screen's warnings
     *  are counts over **everything** (`FRD-208`), so the list is fetched whole on purpose. */
    const { component } = setup({
      models: of([
        { name: 'a', approved: true, is_priced: false },
        { name: 'b', approved: false, is_priced: false },
      ]),
    });

    expect(component.unpriced().length).toBe(2);
  });
});

/**
 * The three declaration blocks (`FRD-114`).
 *
 * The API has accepted `thinking`, `embedding` and `attachments` since they existed and the
 * console could not write any of them: it *showed* them in the opened row as JSON, and offered no
 * field. So `all-minilm` listed with a batch flag and no width — a Global Administrator could tick
 * "embed" and had nowhere to say how wide the vectors are, and the seed was the only way in.
 * `FRD-206` inverted: a capability with no way in announces itself through nothing, because an
 * absent control reads as a design decision.
 *
 * Nothing is lost by editing a model without them — measured against the running stack before this
 * was built, since the API upserts and leaves omitted fields alone. That is why this was a gap
 * rather than a defect, and why the tests below are about what the form can now *say*.
 */
describe('ModelCatalog — declaration blocks', () => {
  const DECLARED: CatalogModel = {
    name: 'declared-model',
    capabilities: ['generate', 'embed', 'thinking', 'attachments'],
    max_output_tokens: 40960,
    thinking: {
      modes: ['disabled'],
      min_tokens: 128,
      max_tokens: 8192,
      default: { mode: 'disabled' },
      levels: ['low', 'high'],
    },
    embedding: {
      dimensions: [384, 768],
      default: 384,
      task_types: ['RETRIEVAL_QUERY'],
      supports_batch: true,
    },
    attachments: { media_types: { 'application/pdf': { tokens: 258 } } },
  };

  it('keeps approval out of the tabs, where pressing Save cannot miss it', () => {
    /** The decision the split turned on.
     *
     *  `approved` is not a property of the model — it is what makes the model callable
     *  (`FRD-307`), and it starts **off**. Behind a tab, somebody fills in three screens, presses
     *  Save, and creates a model nothing can call, with the switch they never opened sitting at
     *  its default. It belongs beside Save, visible on every tab, which is the only place its
     *  state cannot be missed at the moment it takes effect. Deprecation keeps it company: both
     *  are statements about the model's standing rather than about what it is.
     */
    const harness = setup();
    const catalog = harness.component;
    catalog.showAdd.set(true);

    for (const tab of ['identity', 'capabilities', 'price'] as const) {
      catalog.editorTab.set(tab);
      harness.fixture.detectChanges();

      expect(
        harness.html().querySelector('[data-testid="model-approved"]'),
        `approval must be reachable on the ${tab} tab`,
      ).not.toBeNull();
    }
  });

  it('puts each field behind the question it answers', () => {
    /** Eighteen fields in one column, with the input price between the provider and the publisher.
     *  The grouping is the feature; asserting it stops the next field being appended wherever the
     *  file happens to end. */
    const harness = setup();
    const catalog = harness.component;
    catalog.showAdd.set(true);
    catalog.capabilities.set(['thinking']);

    const on = (tab: 'identity' | 'capabilities' | 'price', selector: string) => {
      catalog.editorTab.set(tab);
      harness.fixture.detectChanges();
      return harness.html().querySelector(selector) !== null;
    };

    expect(on('identity', '#model-name')).toBe(true);
    expect(on('identity', '#model-kira-id')).toBe(true);
    expect(on('identity', 'label[for="model-input"]')).toBe(false);

    expect(on('capabilities', '[data-testid="thinking-block"]')).toBe(true);
    expect(on('capabilities', '#model-max-output')).toBe(true);
    expect(on('capabilities', '#model-name')).toBe(false);

    expect(on('price', 'label[for="model-input"]')).toBe(true);
    expect(on('price', '[data-testid="thinking-block"]')).toBe(false);
  });

  it('shows a block only where its capability is ticked', () => {
    const harness = setup();
    const catalog = harness.component;
    catalog.showAdd.set(true);
    // The three declaration blocks live on the tab that answers "what can it do" — which is also
    // where the capability checkboxes are, so a reader never ticks a box on one screen and looks
    // for its consequences on another.
    catalog.editorTab.set('capabilities');
    catalog.capabilities.set([]);
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid="thinking-block"]')).toBeNull();
    expect(harness.html().querySelector('[data-testid="embedding-block"]')).toBeNull();
    expect(harness.html().querySelector('[data-testid="attachments-block"]')).toBeNull();

    catalog.capabilities.set(['thinking', 'embed', 'attachments']);
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid="thinking-block"]')).not.toBeNull();
    expect(harness.html().querySelector('[data-testid="embedding-block"]')).not.toBeNull();
    expect(harness.html().querySelector('[data-testid="attachments-block"]')).not.toBeNull();
  });

  it('loads what a model already declares into the form', () => {
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);

    expect(catalog.thinkingModes()).toEqual(['disabled']);
    expect(catalog.thinkingMax()).toBe(8192);
    expect(catalog.thinkingDefault()).toBe('disabled');
    expect(catalog.thinkingLevels()).toEqual(['low', 'high']);
    expect(catalog.dimensions()).toBe('384, 768');
    expect(catalog.defaultDimension()).toBe(384);
    expect(catalog.taskTypes()).toBe('RETRIEVAL_QUERY');
    expect(catalog.supportsBatch()).toBe(true);
    expect(catalog.mediaTypes()).toEqual(['application/pdf']);
  });

  it('sends all three blocks in the shape the validator takes', () => {
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);
    catalog.save();

    const sent = harness.saved[0];
    expect(sent.thinking).toEqual({
      modes: ['disabled'],
      min_tokens: 128,
      max_tokens: 8192,
      default: { mode: 'disabled' },
      levels: ['low', 'high'],
    });
    expect(sent.embedding).toEqual({
      dimensions: [384, 768],
      default: 384,
      task_types: ['RETRIEVAL_QUERY'],
      supports_batch: true,
    });
    expect(sent.attachments).toEqual({
      media_types: { 'application/pdf': { tokens: 258 } },
    });
  });

  it('a ticked media type with no estimate reserves nothing, and says so', async () => {
    /** The second field of the KIRA-id shape: the estimate was **displayed** beside a media type
     *  whenever the API had put one there, and no control could set it. Every declaration written
     *  in the console therefore sent `{"image/png": null}`, and the gateway reads a missing
     *  estimate as zero — `attachment_tokens` sums only the entries that are objects. A request
     *  carrying a 20 000-token document was reserved for as if it were a sentence, which reopens
     *  under documents the race `FRD-405` closed for text. */
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);
    catalog.toggleMediaType('image/png', true);
    catalog.save();

    expect(harness.saved[0].attachments?.media_types?.['image/png']).toBeNull();
  });

  it('sends the estimate that was typed beside the media type', async () => {
    /** Typed into the rendered input rather than set through the component: what is being
     *  prevented is a control that renders and sends nothing. */
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);
    catalog.editorTab.set('capabilities');
    catalog.toggleMediaType('image/png', true);
    harness.fixture.detectChanges();
    await Promise.resolve();

    expect(harness.testid('media-tokens-image/png')).not.toBeNull();
    harness.type('media-tokens-image/png', '1200');
    catalog.save();

    expect(harness.saved[0].attachments?.media_types?.['image/png']).toEqual({ tokens: 1200 });
    // The one already on file is untouched by the new control.
    expect(harness.saved[0].attachments?.media_types?.['application/pdf']).toEqual({ tokens: 258 });
  });

  it('forgets an estimate when its media type is unticked', () => {
    /** A number that is no longer on screen must not come back on a re-tick — that is a value
     *  nobody can see being sent, which is the whole defect. */
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);
    catalog.toggleMediaType('application/pdf', false);
    catalog.toggleMediaType('application/pdf', true);
    catalog.save();

    expect(harness.saved[0].attachments?.media_types?.['application/pdf']).toBeNull();
  });

  /**
   * `null`, not `{}`. An empty object replaces a declaration with an empty one — which the
   * validator would refuse for thinking (`modes` must be non-empty) and would silently accept for
   * embedding, leaving a model declared to embed with nothing said about it. Removing the
   * capability has to remove the block.
   */
  it('removes a block when its capability is unticked', () => {
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);
    catalog.toggleCapability('embed', false);
    catalog.toggleCapability('attachments', false);
    catalog.toggleCapability('thinking', false);
    catalog.save();

    const sent = harness.saved[0];

    // All three, and `null` rather than "falsy": replacing the thinking block with `{}` left this
    // green when it named only two of them, and an empty object is precisely the wrong answer —
    // it saves, the model keeps the capability, and nothing is declared about it.
    expect(sent.embedding).toBeNull();
    expect(sent.attachments).toBeNull();
    expect(sent.thinking).toBeNull();
  });

  /**
   * The validator refuses a default that is not among the declared modes, and a budget for a mode
   * that is not declared is a number nothing will ever read. Both would be a save that fails for a
   * reason the reader cannot see on screen — the form's own state is where it is visible.
   */
  it('drops a default naming a mode that is unticked, or a level that is removed', () => {
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);

    catalog.thinkingDefault.set('disabled');
    catalog.toggleThinkingMode('disabled', false);
    expect(catalog.thinkingDefault()).toBe('');

    catalog.thinkingDefault.set('high');
    catalog.removeLevel('high');
    expect(catalog.thinkingDefault()).toBe('');

    catalog.save();
    const thinking = harness.saved[0].thinking;
    expect(thinking?.modes).toEqual([]);
    expect(thinking?.levels).toEqual(['low']);
    expect(thinking?.default).toBeNull();
  });

  /**
   * A width that is no longer offered must not be sent: the validator refuses it, and a stale one
   * survives exactly the edit that shortened the list — which is the edit somebody makes when a
   * model drops a width.
   */
  it('sends a default width only while it is one of the declared ones', () => {
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);
    catalog.dimensions.set('768');
    catalog.save();

    expect(harness.saved[0].embedding?.dimensions).toEqual([768]);
    expect(harness.saved[0].embedding?.default).toBeNull();
  });

  /**
   * The form has no input for a per-type token estimate, so writing the block from the checkboxes
   * alone would drop one somebody measured — a silent loss, in a figure that only shows up in a
   * budget reservation (`FRD-110` §5.3).
   */
  it('carries a media type’s token estimate through an edit that never touched it', () => {
    const harness = setup();
    const catalog = harness.component;
    catalog.edit(DECLARED);
    catalog.toggleMediaType('image/png', true);
    catalog.save();

    expect(harness.saved[0].attachments?.media_types).toEqual({
      'application/pdf': { tokens: 258 },
      'image/png': null,
    });
  });

  it('offers no width the form has not declared', () => {
    const harness = setup();
    const catalog = harness.component;
    catalog.showAdd.set(true);
    catalog.capabilities.set(['embed']);
    catalog.dimensions.set('384, nonsense, 768, -1');

    expect(catalog.declaredDimensions()).toEqual([384, 768]);
  });
});

/**
 * The thinking levels, after `ADR-0021` turned a token table into the vendor's own words.
 *
 * The owner's objection, cataloguing a real model: *"If I now pick medium or low, you ask me how
 * many tokens that should be. You do not even find these parameters on the vendors' own pages.
 * How am I supposed to know?"* Nobody publishes it — and a guess did not merely sit in a database,
 * it went upstream as a **ceiling on the model's reasoning**, so a typed `medium = 2000` truncates
 * an agentic run that needed twenty thousand.
 *
 * What replaced it has to earn the free text: if the words are unconstrained, something other than
 * a rule in this repository must say whether one works. That is the model itself.
 */
describe('ModelCatalog — thinking levels are the vendor’s own words', () => {
  it('takes a word, lower-cases it, and refuses a duplicate quietly', () => {
    const { component } = setup();
    component.add();

    component.levelDraft.set('Low');
    component.addLevel();
    component.levelDraft.set('  low  ');
    component.addLevel();
    component.levelDraft.set('high');
    component.addLevel();

    // `Low` and `low` are the same instruction to every vendor, so two chips saying so would be
    // two chips the reader has to reconcile.
    expect(component.thinkingLevels()).toEqual(['low', 'high']);
    expect(component.levelDraft()).toBe('');
  });

  it('refuses a thinking mode typed as though it were a vendor word', () => {
    /** `disabled`, `auto` and `limited` are settings the gateway translates per dialect, not words
     *  a vendor takes. A model listing one as a level would send that string upstream and mean
     *  something else by it — and it is a mistake somebody makes precisely because the two lists
     *  sit one above the other. */
    const { component } = setup();
    component.add();

    component.levelDraft.set('auto');
    component.addLevel();

    expect(component.thinkingLevels()).toEqual([]);
    expect(component.error()).toContain('thinking mode');
  });

  it('offers modes and levels together as a default', () => {
    /** A default is whatever the model does when nobody says, and that is as likely to be a level
     *  as a mode. Offering only the modes would make a level undeclarable without saying so. */
    const { component } = setup();
    component.add();
    component.toggleThinkingMode('auto', true);
    component.levelDraft.set('high');
    component.addLevel();

    expect(component.declarableDefaults()).toEqual(['auto', 'high']);
  });

  it('asks the model and marks each word with what it answered', async () => {
    const harness = setup();
    const { component } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    component.thinkingLevels.set(['low']);
    // Typed but not committed: pressing the button without leaving the box would otherwise check
    // everything *except* the word the reader is looking at.
    component.levelDraft.set('medium');
    component.checkLevels();

    // **With where the form says it lives.** Both checks ask about the editor's state rather than
    // the saved row: correcting a provider and pressing a check used to answer about the
    // declaration being replaced, which is right about the wrong thing.
    expect(harness.askedLevels).toEqual([
      {
        model: 'gemini-2.5-flash',
        levels: ['low', 'medium'],
        where: { provider: FLASH.provider ?? '', publisher: '', region: '' },
        modes: component.thinkingModes(),
      },
    ]);
    expect(component.levelVerdict('low')).toEqual({ ok: true, detail: 'The model accepted it.' });
    // **The provider's own words**, which is the whole value of the button: no rule here could
    // have said `thinking_level is not supported by this model` as precisely, or stayed as true.
    expect(component.levelVerdict('medium')?.ok).toBe(false);
    expect(component.levelVerdict('medium')?.detail).toContain('thinking_level is not supported');
  });

  it('marks a thinking mode the model’s own dialect cannot express', () => {
    // Measured against the running stack on 2026-08-20: `auto` ticked here for a model served by
    // an OpenAI-dialect endpoint was accepted in silence, and every thinking request afterwards
    // answered `500 Internal error`. Every adapter had always declared which modes its wire
    // format can express — and **nothing on any path read that declaration**.
    const { component } = setup();
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    component.thinkingModes.set(['disabled', 'auto']);

    component.checkLevels();

    expect(component.modeVerdicts()['disabled'].ok).toBe(true);
    expect(component.modeVerdicts()['auto'].ok).toBe(false);
    // The sentence says what declaring it would *do*. The reader is deciding whether to leave a
    // box ticked, and "unsupported" alone does not tell them.
    expect(component.modeVerdicts()['auto'].detail).toContain('never reached');
  });

  it('shows the verdict beside the box it is about, and the sentence only for a refusal', () => {
    // The DOM, not the signal: what an administrator acts on is a mark next to a checkbox. A green
    // one needs no explanation under it — a red one is the whole point, so its sentence is
    // rendered rather than hidden in a tooltip.
    const harness = setup();
    const catalog = harness.component;
    catalog.edit({ ...FLASH, name: 'gemini-2.5-flash', capabilities: ['generate', 'thinking'] });
    catalog.thinkingModes.set(['disabled', 'auto']);
    catalog.editorTab.set('capabilities');
    catalog.checkLevels();
    harness.fixture.detectChanges();

    const html = harness.html();
    expect(html.querySelector('[data-testid="mode-verdict-disabled"]')?.textContent).toContain(
      'can say it',
    );
    expect(html.querySelector('[data-testid="mode-verdict-auto"]')?.textContent).toContain(
      'cannot say it',
    );
    expect(html.querySelector('[data-testid="mode-why-auto"]')?.textContent).toContain(
      'never reached',
    );
    // No sentence under the box that is fine: an explanation for every row is an explanation
    // nobody reads, and the one that matters stops standing out.
    expect(html.querySelector('[data-testid="mode-why-disabled"]')).toBeNull();
  });

  it('shows no verdict at all before the button is pressed', () => {
    // An unasked question is not a verdict (`FRD-506`). Ticks appearing on load would be this
    // console's opinion about a model nobody has asked.
    const harness = setup();
    harness.component.edit({
      ...FLASH,
      name: 'gemini-2.5-flash',
      capabilities: ['generate', 'thinking'],
    });
    harness.component.thinkingModes.set(['disabled', 'auto']);
    harness.component.editorTab.set('capabilities');
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid^="mode-verdict-"]')).toBeNull();
  });

  it('clears the marks when a later question fails, rather than leaving stale ones', () => {
    // `FRD-506`'s rule, and it applies to the modes for the same reason it applies to the words: a
    // red mark says "this model refuses it", and an unreachable gateway says nothing about it.
    //
    // **The first press has to succeed**, or this asserts nothing: written without it, the test
    // passed while the clearing line was deleted — there were no marks to leave behind. Found by
    // breaking the property by hand, which is the whole reason that is done.
    const { component } = setup({
      levelChecks: [
        of({
          model: 'gemini-2.5-flash',
          results: [],
          modes: [{ mode: 'auto', accepted: false, detail: 'no field for it' }],
        }),
        throwError(() => ({ status: 502, error: { error: { message: 'no' } } })),
      ],
    });
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    component.thinkingModes.set(['auto']);

    component.checkLevels();
    expect(component.modeVerdicts()['auto'].ok).toBe(false);

    component.checkLevels();

    expect(component.modeVerdicts()).toEqual({});
  });

  it('clears the previous answers when the question itself fails', () => {
    /** A red chip says *the model refused this word*. The gateway being unreachable says nothing
     *  about the word at all, and leaving the **last** answers standing beside a failed question
     *  is worse than either — a verdict about a request that did not happen, which is the shape
     *  `LESSONS.md` records as unknown rendered as a number.
     *
     *  **Asked twice on purpose.** Written first with a single failing call, where the assertion
     *  passed against code that cleared nothing: there was nothing to clear. A test that never
     *  reaches the path it is named after is this project's most-repeated defect, and this is the
     *  mutation run catching it rather than a reviewer. */
    const harness = setup({
      levelChecks: [
        of({
          model: 'gemini-2.5-flash',
          results: [{ region: '', level: 'low', accepted: true, detail: 'The model accepted it.' }],
        }),
        throwError(() => ({ status: 503 })),
      ],
    });
    const { component } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    component.thinkingLevels.set(['low']);

    component.checkLevels();
    expect(component.levelVerdict('low')?.ok).toBe(true);

    component.checkLevels();
    expect(component.levelVerdict('low')).toBeNull();
    expect(component.error()).toContain('Could not ask');
  });

  it('removes a word, its verdict, and a default naming it', () => {
    const harness = setup();
    const { component } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    component.thinkingLevels.set(['low']);
    component.checkLevels();
    component.thinkingDefault.set('low');
    expect(component.levelVerdict('low')).not.toBeNull();

    component.removeLevel('low');

    expect(component.thinkingLevels()).toEqual([]);
    expect(component.levelVerdict('low')).toBeNull();
    // Or the save fails against a validator refusing a default nothing declares, for a reason the
    // reader cannot see on screen.
    expect(component.thinkingDefault()).toBe('');
  });
});

/**
 * Residency, refused where the region is typed rather than where it is used (`ADR-0012` §6).
 *
 * The gateway has always enforced `AIRA_ALLOWED_REGIONS` — at the moment it *addresses* a request,
 * which is correct and late: a model is catalogued here, and whoever did it hears nothing until a
 * caller gets a 4xx, possibly weeks later. Measured on the running installation on 2026-08-19: two
 * requests had been processed at `global`, a region that names no place and guarantees none, and
 * nothing anywhere said so.
 *
 * The console can only refuse earlier if it knows the list — and it must not know it by holding a
 * copy. One policy, published by the plane that owns it.
 */
describe('ModelCatalog — a region this installation does not permit', () => {
  it('refuses one that is not on the gateway’s list, and names what is', () => {
    const { component } = setup({ allowedRegions: ['eu', 'europe-west1'] });
    component.add();
    component.name.set('gemini-3.5-flash');
    component.regions.set(['global']);

    expect(component.regionAllowed('global')).toBe(false);
    expect(component.forbiddenRegions()).toEqual(['global']);
    expect(component.formError()).toContain(
      "Region 'global' is not permitted by this installation",
    );
    // The remedy names where the policy lives, because it is not changed here.
    expect(component.formError()).toContain('AIRA_ALLOWED_REGIONS');
    expect(component.canSave()).toBe(false);
  });

  it('permits one that is on the list', () => {
    const { component } = setup({ allowedRegions: ['eu', 'europe-west1'] });
    component.add();
    component.regions.set(['europe-west1']);

    expect(component.regionAllowed('europe-west1')).toBe(true);
    expect(component.forbiddenRegions()).toEqual([]);
    expect(component.formError()).not.toContain('permits');
  });

  it('has no opinion about an empty region', () => {
    /** Empty is the correct answer for a platform addressed by model name alone — AI Studio, an
     *  OpenAI-compatible server — where a region would be a claim about routing nothing acts on. */
    const { component } = setup({ allowedRegions: ['eu'] });
    component.add();
    component.regionDraft.set('   ');
    component.addRegion();

    expect(component.regions()).toEqual([]);
    expect(component.forbiddenRegions()).toEqual([]);
    expect(component.formError()).not.toContain('permits');
  });

  it('has no opinion when the gateway did not say', () => {
    /** **The edge case that matters most, because getting it wrong breaks everything.** An older
     *  gateway sends no `allowedRegions`, and a console reading absence as an empty allow-list
     *  would refuse *every* region anybody typed — a console that has never heard the policy
     *  enforcing its own idea of it.
     *
     *  Absence means *this gateway did not say*. The console declines to have an opinion and the
     *  gateway refuses at request time exactly as it always did: informing where it can, never
     *  blocking on its own ignorance (`FRD-506`'s rule, one screen over). */
    const { component } = setup({ allowedRegions: [] });
    component.add();
    component.name.set('m');
    component.regions.set(['somewhere-else']);

    expect(component.regionAllowed('somewhere-else')).toBeNull();
    expect(component.forbiddenRegions()).toEqual([]);
    expect(component.formError()).toBeNull();
  });

  it('offers the permitted regions without confining the field to them', () => {
    /** A `datalist`, not a `<select>`: somebody widening the policy on the gateway should be able
     *  to type the new region before this console has been reloaded, and a region the list does
     *  not carry deserves the refusal above rather than silent absence. */
    const { component, fixture, html } = setup({ allowedRegions: ['eu', 'europe-west1'] });
    component.add();
    fixture.detectChanges();

    const offered = [...html().querySelectorAll('#allowed-regions option')].map(
      (option) => (option as HTMLOptionElement).value,
    );
    expect(offered).toEqual(['eu', 'europe-west1']);
    expect(html().querySelector('#model-region')?.tagName).toBe('INPUT');
  });

  it('says so beside the field, not only in the footer', () => {
    const { component, fixture, html } = setup({ allowedRegions: ['eu'] });
    component.add();
    component.regions.set(['global']);
    fixture.detectChanges();

    const note = html().querySelector('[data-testid="region-not-permitted"]');
    expect(note).not.toBeNull();
    expect(note!.textContent).toContain('eu');
  });
});

/**
 * Several regions, in the order they should be tried (`FRD-609`).
 *
 * *"In the catalogue I would like to be able to enter several regions, and they should also be
 * checkable — whether the model is reachable, and the same for the thinking methods."*
 *
 * Order is the meaning, not the presentation: the first entry is where an ordinary request goes,
 * and the rest are what the gateway falls back to when a region cannot serve.
 */
describe('ModelCatalog — a model in several regions', () => {
  it('keeps the order they were typed in', () => {
    const { component } = setup();
    component.add();

    for (const region of ['europe-west4', 'europe-west1', 'eu']) {
      component.regionDraft.set(region);
      component.addRegion();
    }

    expect(component.regions()).toEqual(['europe-west4', 'europe-west1', 'eu']);
  });

  it('drops a repeat rather than queueing the same failure twice', () => {
    const { component } = setup();
    component.add();
    component.regionDraft.set('eu');
    component.addRegion();
    component.regionDraft.set('  eu  ');
    component.addRegion();

    expect(component.regions()).toEqual(['eu']);
  });

  it('sends them as a list, and null when there are none', () => {
    /** `{regions: [...]}` is the shape both planes read. A model addressed by name alone — an
     *  OpenAI-compatible server, AI Studio — sends `null`, because an empty object would be a
     *  claim about routing that nothing acts on. */
    const harness = setup({ allowedRegions: ['europe-west1', 'europe-west4'] });
    const { component } = harness;
    component.add();
    component.name.set('m');
    component.regions.set(['europe-west1', 'europe-west4']);
    harness.lookFirst();
    component.save();

    expect(harness.saved[0].addressing).toEqual({
      regions: ['europe-west1', 'europe-west4'],
    });

    component.add();
    component.name.set('m2');
    harness.lookFirst();
    component.save();
    expect(harness.saved[1].addressing).toBeNull();
  });

  it('reads a model saved with the old single-region shape', () => {
    /** Rows written before a model could name several still carry `{region: "x"}`, and a
     *  redelivered event can carry it after a rollback. One reader for both spellings, in the same
     *  place the gateway has one. */
    const { component } = setup();
    component.edit({ ...FLASH, addressing: { region: 'europe-west3' } });

    expect(component.regions()).toEqual(['europe-west3']);
  });

  it('marks each region with what the gateway said about it', () => {
    /** **The reason the list is checkable at all.** A model in three places is reachable in some
     *  and not others, and a single yes-or-no about the first would be an answer to a question
     *  nobody asked. */
    const harness = setup({
      check: of({
        model: 'gemini-2.5-pro',
        declared: true,
        served: true,
        reachable: true,
        detail: 'answered in europe-west4. Not reachable in: europe-west1.',
        regions: [
          { region: 'europe-west1', reachable: false, detail: 'Vertex returned 404.' },
          { region: 'europe-west4', reachable: true, detail: 'answered' },
        ],
      }),
    });
    const { component } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-pro' });
    component.regions.set(['europe-west1', 'europe-west4']);
    harness.lookFirst();

    expect(component.regionVerdict('europe-west1')).toEqual({
      ok: false,
      detail: 'Vertex returned 404.',
    });
    expect(component.regionVerdict('europe-west4')?.ok).toBe(true);
  });

  it('asks about every region it holds', () => {
    const harness = setup();
    const { component } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-pro' });
    component.regions.set(['europe-west1', 'europe-west4']);
    component.thinkingLevels.set(['low']);
    component.checkLevels();

    // Comma-separated on the wire, because a query parameter is a string; one shape, split by the
    // one reader that knows what it means.
    expect(harness.askedLevels[0].where.region).toBe('europe-west1,europe-west4');
  });

  it('refuses only the regions the installation does not permit, and names them', () => {
    const { component } = setup({ allowedRegions: ['eu', 'europe-west1'] });
    component.add();
    component.name.set('m');
    component.regions.set(['europe-west1', 'global', 'us-central1']);

    expect(component.forbiddenRegions()).toEqual(['global', 'us-central1']);
    expect(component.formError()).toContain("'global', 'us-central1'");
    expect(component.formError()).toContain('are not permitted');
    // The permitted one is not implicated.
    expect(component.regionAllowed('europe-west1')).toBe(true);
  });

  it('removes a region and the verdict that was about it', () => {
    const { component } = setup();
    component.add();
    component.regions.set(['eu', 'europe-west1']);
    component.regionVerdicts.set({ eu: { ok: true, detail: 'answered' } });

    component.removeRegion('eu');

    expect(component.regions()).toEqual(['europe-west1']);
    expect(component.regionVerdict('eu')).toBeNull();
  });
});
