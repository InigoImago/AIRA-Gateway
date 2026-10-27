import { formatDate } from '@angular/common';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { Observable, Subject, defer, map, of, throwError } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import {
  Capability,
  CatalogModel,
  GatewayProvider,
  Me,
  ModelCheck,
  ThinkingLevelCheck,
  BillingPrices,
  ModelReference,
  OfferedModel,
  ReferenceEntry,
} from '../../core/api/models';
import { Provenance, UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { ModelCatalog } from './model-catalog';
import { ModelEditor, convert } from './model-editor';
import { ServedModels } from './served-models';

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
/** What the model reference answers (`FRD-508`): two Vertex drafts and one for AI Studio. */
const SONNET: ReferenceEntry = {
  platform: 'vertex',
  name: 'claude-sonnet-9',
  display_name: 'Claude Sonnet 9',
  publisher: 'anthropic',
  family: 'claude-sonnet',
  status: '',
  release_date: '2026-09-01',
  knowledge: '2026-05',
  description: 'Balanced',
  declaration: {
    capabilities: ['generate', 'tools', 'attachments', 'thinking', 'prompt_caching'],
    context_window: 1000000,
    max_output_tokens: 64000,
    thinking: { modes: ['limited'], min_tokens: 1024, max_tokens: 63999, levels: ['low', 'high'] },
    attachments: { media_types: { 'application/pdf': null, 'image/png': null } },
    fixed_sampling: true,
  },
  prices: { input: '3', output: '15', cached_input: '0.3', cache_write: '3.75' },
  notes: ['the thinking ceiling is the output cap less one; the list states none'],
};
const OLD_GEMINI: ReferenceEntry = {
  ...SONNET,
  name: 'gemini-old',
  display_name: 'Gemini Old',
  publisher: 'google',
  status: 'deprecated',
  release_date: '2026-10-01',
  notes: [],
};
const STUDIO_GEMINI: ReferenceEntry = {
  ...SONNET,
  platform: 'generative-language',
  name: 'gemini-studio',
  publisher: 'google',
  notes: [],
};
const REFERENCE: ModelReference = {
  source: 'https://models.dev/api.json',
  as_of: '2026-10-17',
  currency: 'USD',
  live_error: null,
  entries: [OLD_GEMINI, SONNET, STUDIO_GEMINI],
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

/** The page: the catalog, one row's detail and check, and the browse window. */
interface Page {
  models: () => CatalogModel[];
  view: { rows: () => CatalogModel[] };
  loading: () => boolean;
  canEdit: () => boolean;
  unpriced: () => CatalogModel[];
  undeclared: () => CatalogModel[];
  check: () => { reachable: boolean | null; served: boolean; detail?: string } | null;
  edit: (m: CatalogModel) => void;
  remove: (m: CatalogModel) => void;
  importServed: (model: unknown) => void;
  openBrowse: () => void;
  closeBrowse: () => void;
  showBrowse: () => boolean;
  browseProvider: { set: (v: string) => void; (): string };
  browseSearch: { set: (v: string) => void; (): string };
  browseMatches: () => OfferedModel[];
  askable: () => GatewayProvider[];
  catalogueOffered: (model: OfferedModel) => void;
  chooseBrowseProvider: (name: string) => void;
  offerings: () => OfferedModel[] | null;
  offeringsError: () => string | null;
  addManually: () => void;
  addByName: () => void;
}

/** What the gateway serves and the catalog does not know (`FRD-507`). */
interface Discovery {
  discover: () => void;
  discovering: () => boolean;
  served: () => unknown[] | null;
  notCatalogued: () => { name: string }[];
  alreadyCatalogued: () => number;
}

/** The editor window: the form, its checks and the save. */
interface Editor {
  add: () => void;
  edit: (m: CatalogModel) => void;
  openProvenance: () => void;
  showProvenance: () => boolean;
  provenanceKnown: () => boolean;
  provider: { set: (v: string) => void; (): string };
  approved: { set: (v: boolean) => void; (): boolean };
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
  fixedSampling: { set: (v: boolean) => void; (): boolean };
  defaultTopP: { set: (v: number | null) => void; (): number | null };
  defaultTopK: { set: (v: number | null) => void; (): number | null };
  fromReferenceEntry: (
    entry: ReferenceEntry,
    reference: ModelReference,
    upstream: GatewayProvider | null,
  ) => void;
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
  cacheWritePrice: { set: (v: string) => void; (): string };
  cachedPrice: { set: (v: string) => void; (): string };
  formError: () => string | null;
  canSave: () => boolean;
  save: () => void;
  providers: () => GatewayProvider[] | null;
  allowedRegions: () => string[];
  permittedRegions: () => string[];
  providersError: () => string | null;
  providerIsCustom: { set: (v: boolean) => void; (): boolean };
  chooseProvider: (value: string) => void;
  selectedProvider: () => GatewayProvider | null;
  useOffered: (model: OfferedModel) => void;
  providerLabel: (provider: GatewayProvider) => string;
  editing: () => string;
  vendorSaid: () => string[];
  displayName: { set: (v: string) => void; (): string };
  platform: { set: (v: string) => void; (): string };
  checkedName: () => string | null;
  check: () => { reachable: boolean | null; served: boolean; detail?: string } | null;
  runCheck: (m: { name: string }) => void;
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
    /** What `/me` lists. Absent means a Global Administrator's two that matter here: maintaining the
     *  catalog, and asking the gateway about a model (`operations.diagnose`). */
    permissions?: string[];
    confirm?: boolean;
    /** What `:check` answers (`FRD-506`). */
    check?: Observable<ModelCheck>;
    /** One per call, in order — so a test can let the first succeed and the second fail. */
    levelChecks?: Observable<ThinkingLevelCheck>[];
    /** What the gateway is configured with (`FRD-507` stage C). */
    providers?: Observable<GatewayProvider[]>;
    /** Where this installation permits processing, as the gateway states it (`ADR-0012` §6). */
    allowedRegions?: string[];
    /** Regions single models are permitted by name (`AIRA_ALLOWED_MODEL_REGIONS`). */
    modelRegions?: Record<string, string[]>;
    /** What one provider answers when asked what it offers. */
    offerings?: Observable<OfferedModel[]>;
    /** `AIRA_CURRENCY` as `/me` states it. */
    currency?: string;
    /** What Management's model reference answers (`FRD-508`). */
    reference?: Observable<ModelReference>;
    /** What the gateway's billing lookup answers (`FRD-509`). */
    billing?: Observable<BillingPrices>;
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
  const billingAsked: { provider: string; model: string; region: string }[] = [];
  const asked: string[] = [];
  TestBed.resetTestingModule();
  const saved: CatalogModel[] = [];
  const removed: string[] = [];
  const me: Me = {
    subject: 's',
    username: 'admin',
    email: '',
    roles: options.roles ?? ['global-admin'],
    permissions: options.permissions ?? ['catalog.write', 'operations.diagnose'],
    use_cases: [],
  };

  TestBed.configureTestingModule({
    imports: [ModelCatalog],
    providers: [
      provideRouter([]),
      {
        provide: MeService,
        useValue: { currency: signal(options.currency ?? ''), get: () => of(me) },
      },
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
                // The dialect's answer about each ticked mode: every OpenAI-family adapter
                // declares that it cannot express `auto`.
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
                modelRegions: options.modelRegions ?? {},
              })),
            ),
          providerOfferings: (name: string) => {
            asked.push(name);
            return options.offerings ?? of([OFFERED_FLASH, OFFERED_EMBED]);
          },
          modelReference: () => options.reference ?? of(REFERENCE),
          billingPrices: (provider: string, model: string, region = '') => {
            billingAsked.push({ provider, model, region });
            return (
              options.billing ??
              throwError(() => ({
                status: 501,
                error: { error: { message: "'vertex' has no billing account to ask." } },
              }))
            );
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
  const editor = fixture.debugElement.query(By.directive(ModelEditor))
    .componentInstance as unknown as Editor;
  return {
    fixture,
    saved,
    removed,
    component: fixture.componentInstance as unknown as Page,
    editor,
    /** Rendered for a role that may catalogue; `undefined` for a reader. */
    discovery: fixture.debugElement.query(By.directive(ServedModels))
      ?.componentInstance as unknown as Discovery,
    feedback: fixture.debugElement.injector.get(PageFeedback),
    asked,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    checked,
    askedLevels,
    checkedWhere,
    billingAsked,
    html: () => fixture.nativeElement as HTMLElement,
    /** Run the reachability check for whatever name is in the editor. Creating a model requires
     *  having *looked* (`FRD-506`), so every case that creates one does this. */
    lookFirst: () => {
      editor.runCheck({ name: editor.name() });
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
  it('heads prices in the installation’s currency, never in a symbol of its own', () => {
    const harness = setup({ currency: 'EUR' });
    const text = (harness.fixture.nativeElement as HTMLElement).textContent ?? '';

    expect(text).toContain('Input EUR / 1M');
    expect(text).toContain('Output EUR / 1M');
    expect(text).toContain('per 1,000,000 tokens, in EUR. Input');
    expect(text).not.toContain('$');
  });

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

  it('hides the editing surface from everyone without `catalog.write`', () => {
    const admin = setup();
    admin.openFirst();
    expect(admin.component.canEdit()).toBe(true);
    expect(admin.html().querySelector('[data-testid^="edit-"]')).not.toBeNull();

    const reader = setup({ permissions: ['usecase.read_all', 'report.read_all'] });
    reader.openFirst();
    expect(reader.component.canEdit()).toBe(false);
    // The panel still opens — reading a declaration is not editing one — and carries no actions.
    expect(reader.html().querySelector('[data-testid^="detail-"]')).not.toBeNull();
    expect(reader.html().querySelector('[data-testid^="edit-"]')).toBeNull();
    expect(reader.html().querySelector('[data-testid="add-model"]')).toBeNull();
    // The guard on the guard: the same query finds the entrance where it is offered.
    expect(admin.html().querySelector('[data-testid="add-model"]')).not.toBeNull();
  });

  it('asks the permission, never the role', () => {
    // The server decides what a role holds, so the role alone offers nothing…
    const roleOnly = setup({ roles: ['global-admin'], permissions: [] });
    roleOnly.openFirst();
    expect(roleOnly.html().querySelector('[data-testid^="edit-"]')).toBeNull();
    expect(roleOnly.html().querySelector('[data-testid="add-model"]')).toBeNull();

    // …and the permission alone is enough.
    const permissionOnly = setup({ roles: [], permissions: ['catalog.write'] });
    permissionOnly.openFirst();
    expect(permissionOnly.html().querySelector('[data-testid^="edit-"]')).not.toBeNull();
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

    // Which model this section is about must be on screen.
    const section = html().querySelector('[data-testid="model-editor"]');
    expect(section?.textContent).toContain('gemini-2.0-flash');

    harness.editor.name.set('typo');
    [...html().querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === 'Cancel')
      ?.click();
    harness.fixture.detectChanges();

    expect(html().querySelector('[data-testid="model-editor"]')).toBeNull();
    expect(harness.saved).toHaveLength(0);
    // Reopening must not show the abandoned edit.
    expect(harness.editor.name()).toBe('');
  });

  it('refuses a price that is not an amount', () => {
    const { editor } = setup();
    editor.name.set('m-1');
    editor.inputPrice.set('teuer');
    editor.outputPrice.set('1.00');
    expect(editor.formError()).toContain('amounts per 1,000,000 tokens');
    expect(editor.canSave()).toBe(false);
  });

  it('refuses a model priced in only one direction', () => {
    const { editor } = setup();
    editor.name.set('m-1');
    editor.inputPrice.set('1.00');
    expect(editor.formError()).toContain('both');
  });

  it('accepts a model with no price at all', () => {
    const harness = setup();
    const { editor, saved } = harness;
    editor.name.set('m-1');
    harness.lookFirst();
    editor.save();
    expect(saved[0]).toMatchObject({ name: 'm-1', input_price_per_million: null });
  });

  it('sends prices as strings and normalises a comma', () => {
    const harness = setup();
    const { editor, saved } = harness;
    editor.name.set('m-1');
    editor.inputPrice.set('0,075');
    editor.outputPrice.set('0.30');
    harness.lookFirst();
    editor.save();
    expect(saved[0].input_price_per_million).toBe('0.075');
    expect(typeof saved[0].input_price_per_million).toBe('string');
  });

  it('loads a row into the form for correction', () => {
    const { component, editor } = setup();
    component.edit(FLASH);
    expect(editor.name()).toBe('gemini-2.0-flash');
    expect(editor.inputPrice()).toBe('0.075');
    expect(editor.showAdd()).toBe(true);
  });

  it('asks before removing a model', () => {
    const declined = setup({ confirm: false });
    declined.component.remove(FLASH);
    expect(declined.removed).toEqual([]);

    const accepted = setup();
    accepted.component.remove(FLASH);
    expect(accepted.removed).toEqual(['gemini-2.0-flash']);
    expect(accepted.feedback.notice()).toContain('removed');
  });

  it('reports a failed load and a failed save', () => {
    const failedLoad = setup({ models: throwError(() => ({ status: 500 })) });
    expect(failedLoad.feedback.error()).toBe('Could not load the model catalog.');
    expect(failedLoad.component.loading()).toBe(false);

    const failedSave = setup({
      save: throwError(() => ({ status: 403, error: { error: { message: 'Not an admin.' } } })),
    });
    failedSave.editor.name.set('m-1');
    failedSave.lookFirst();
    failedSave.editor.save();
    expect(failedSave.feedback.error()).toBe('Not an admin.');
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
    // **Through the one entrance there is**: adding starts by saying where the model lives. Driven
    // through the DOM the whole way, because what matters is what a person can click.
    html().querySelector<HTMLButtonElement>('[data-testid="add-model"]')!.click();
    harness.fixture.detectChanges();
    harness.component.browseProvider.set('vertex');
    harness.fixture.detectChanges();
    html().querySelector<HTMLButtonElement>('[data-testid="add-manually"]')!.click();
    harness.fixture.detectChanges();

    const section = html().querySelector('[data-testid="model-editor"]');
    expect(section).not.toBeNull();
    // The catalog gives way to it: nothing behind the editor can be changed past it.
    expect(html().querySelector('[data-testid="add-model"]')).toBeNull();

    const name = html().querySelector<HTMLInputElement>('#model-name');
    expect(name).not.toBeNull();
    // The prices live behind their own tab; a reader looking for what a model *costs* opens the
    // tab that says so, and this follows them there.
    html().querySelector<HTMLButtonElement>('[data-testid="tab-price"]')!.click();
    harness.fixture.detectChanges();
    expect(html().querySelector('label[for="model-input"]')).not.toBeNull();
    expect(html().querySelector('label[for="model-output"]')).not.toBeNull();

    harness.editor.name.set('m-1');
    harness.editor.inputPrice.set('1.00');
    harness.editor.outputPrice.set('2.00');
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
    expect(harness.editor.showAdd()).toBe(false);
    expect(harness.editor.name()).toBe('');
  });

  it('disables the submit button while the form is invalid', () => {
    const harness = setup();
    harness.editor.showAdd.set(true);
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

  it('edits a model from the panel that shows what it is', async () => {
    const harness = setup();
    harness.openFirst();
    (harness.fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[data-testid="edit-gemini-2.0-flash"]')
      ?.click();
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    expect(harness.editor.name()).toBe('gemini-2.0-flash');
    // The box a person reads, not only the signal behind it.
    expect(harness.html().querySelector<HTMLInputElement>('#model-name')?.value).toBe(
      'gemini-2.0-flash',
    );
  });

  it('shows the display name and a dash for a missing provider', () => {
    const harness = setup({ models: of([FLASH, UNPRICED]) });
    const row = [...harness.html().querySelectorAll('tbody tr')].find((tr) =>
      tr.textContent?.includes('mystery-1'),
    )!;
    expect(harness.text()).toContain('Flash');
    // The provider cell itself — the row's Output cell carries a dash of its own for "no price".
    expect(row.querySelectorAll('td')[2].textContent?.trim()).toBe('—');
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
    page.editor.showAdd.set(true);
    page.editor.name.set('claude-1');
    page.editor.toggleCapability('thinking', true);
    page.editor.toggleCapability('generate', true);
    page.editor.publisher.set('anthropic');
    page.editor.hosting.set('managed');
    page.editor.maxOutput.set(64000);
    page.editor.defaultOutput.set(4096);
    page.editor.deprecated.set(true);
    page.lookFirst();
    page.editor.save();

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
     * `FRD-132` §11. `test_every_model_control_is_reachable.py` asserts a writable field has a
     * payload key; this asserts the control reaches it. A client has nowhere else to learn the
     * window — an assistant's "12% of the context used" divides by it.
     */
    const page = setup();
    page.editor.showAdd.set(true);
    page.editor.name.set('qwen3:0.6b');
    page.editor.toggleCapability('generate', true);
    page.editor.contextWindow.set(40960);
    page.editor.maxOutput.set(4096);
    page.lookFirst();
    page.editor.save();

    expect(page.saved[page.saved.length - 1].context_window).toBe(40960);
  });

  it('offers the KIRA id, and sends what was typed into it', async () => {
    /** `numeric_id` is how a KIRA client names a model (`FRD-107`): without a control, a model
     *  catalogued here is invisible to `/kira/api/external/chat`.
     *
     *  Typed into the real input rather than set on the signal: the defect being guarded against is
     *  a control that renders and sends nothing, and a signal set from a test renders nothing. */
    const page = setup();
    page.editor.showAdd.set(true);
    page.editor.name.set('legacy-1');
    page.editor.toggleCapability('generate', true);
    page.lookFirst();
    // `ngModel` inside a `<form>` registers its control on a microtask, so a value typed before
    // that flush reaches nothing. The wait is the test being a real interaction, not a signal set.
    await Promise.resolve();

    expect(page.testid('model-kira-id')).not.toBeNull();
    page.type('model-kira-id', '4711');
    expect(page.editor.kiraId()).toBe(4711);

    page.editor.save();
    expect(page.saved[page.saved.length - 1].numeric_id).toBe(4711);
  });

  it('leaves the KIRA id to the server when it is not typed', () => {
    /** Blank is not "none": the server assigns the next free number, so a model catalogued without
     *  a thought about KIRA is still addressable there. What must not be sent is a `0` or an empty
     *  string, which the server would have to refuse. */
    const page = setup();
    page.editor.showAdd.set(true);
    page.editor.name.set('plain-1');
    page.editor.toggleCapability('generate', true);
    page.lookFirst();
    page.editor.save();

    expect(page.saved[page.saved.length - 1].numeric_id).toBeNull();
  });

  it('refuses a default output cap above the maximum before sending it', () => {
    const page = setup();
    page.editor.showAdd.set(true);
    page.editor.name.set('impossible-1');
    page.editor.maxOutput.set(1024);
    page.editor.defaultOutput.set(4096);

    expect(page.editor.formError()).toContain('cannot exceed the maximum');
    expect(page.editor.canSave()).toBe(false);
  });

  it('loads a declaration into the form so it can be corrected in place', () => {
    const page = setup({ models: of([DECLARED]) });
    page.component.edit(DECLARED);

    expect(page.editor.hasCapability('thinking')).toBe(true);
    expect(page.editor.hasCapability('embed')).toBe(false);
    expect(page.editor.publisher()).toBe('anthropic');
    expect(page.editor.maxOutput()).toBe(64000);
    expect(page.editor.deprecated()).toBe(true);
  });

  it('unticking a capability removes it rather than leaving it in the list', () => {
    const page = setup();
    page.editor.capabilities.set(['generate', 'thinking']);
    page.editor.toggleCapability('thinking', false);

    expect(page.editor.capabilities()).toEqual(['generate']);
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
     * In the window, and **never blocking**: a model is often declared before its credential
     * exists, and an adapter is registered only once it does — a hard gate would make a fresh
     * installation undeclarable. `FRD-114`: deprecation warns, revocation blocks. A verdict is
     * information.
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
    harness.editor.add();
    harness.editor.name.set('new-model');
    harness.fixture.detectChanges();

    harness.html().querySelector<HTMLElement>('[data-testid="editor-check"]')?.click();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid="editor-verdict"]')?.textContent).toContain(
      'nothing serves it',
    );
    // The point of the test: Save is still available.
    expect(harness.editor.canSave()).toBe(true);
  });

  it('does not carry a verdict from one model into the next window', () => {
    const harness = setup();
    harness.editor.add();
    harness.editor.name.set('a-model');
    harness.fixture.detectChanges();
    harness.html().querySelector<HTMLElement>('[data-testid="editor-check"]')?.click();
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('[data-testid="editor-verdict"]')).not.toBeNull();

    harness.editor.add();
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
    expect(harness.feedback.error()).toBeTruthy();
  });

  // ---- only an approved model may be used (`FRD-307`) --------------------------------------

  it('starts a new declaration unapproved, and sends that', () => {
    /** A model appearing on an upstream is not the same event as somebody deciding it may be used
     *  here. The default is the decision. */
    const harness = setup();
    harness.editor.add();
    harness.editor.name.set('brand-new');
    harness.lookFirst();
    harness.editor.save();

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

    expect(harness.editor.approved()).toBe(true);
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
     * enforces, so "what does this row actually say" must be answered in full.
     *
     * Derived from the type rather than from a list somebody keeps: the fixture is a
     * `Required<CatalogModel>` and the table below is keyed by every key of `CatalogModel`, so a
     * field added to the model fails to **compile** here until somebody decides how the panel
     * shows it — or writes down why it does not.
     */
    const EVERYTHING: Required<CatalogModel> = {
      name: 'everything-1',
      display_name: 'Display Name Here',
      approved: true,
      provider: 'provider-x',
      input_price_per_million: '1.2345',
      cached_input_price_per_million: '0.1111',
      cache_write_price_per_million: '2.2222',
      output_price_per_million: '6.7890',
      is_priced: true,
      updated_at: '2026-08-09T10:00:00Z',
      capabilities: ['generate', 'tools'],
      publisher: 'publisher-y',
      platform: 'platform-z',
      addressing: { regions: ['europe-west4', 'europe-west1'], deployment: 'dep-42' },
      underlying_model: 'underlying-q',
      context_window: 1048576,
      max_output_tokens: 4096,
      default_max_output_tokens: 512,
      thinking: { modes: ['disabled'] },
      embedding: { supports_batch: true },
      // An **object**, keyed by media type, with an optional per-type estimate — the server
      // refuses a list.
      attachments: { media_types: { 'application/pdf': { tokens: 258 } } },
      hosting: 'self_deployed',
      fixed_sampling: true,
      default_sampling: { top_k: 40 },
      deprecated: true,
      numeric_id: 9001,
      is_declared: true,
    };
    const SHOWN_AS: Record<keyof CatalogModel, { testid: string; shows: string } | null> = {
      name: null, // the row's own title, above the panel
      display_name: { testid: 'display_name', shows: 'Display Name Here' },
      approved: { testid: 'approved', shows: 'yes' },
      provider: { testid: 'provider', shows: 'provider-x' },
      input_price_per_million: { testid: 'input_price', shows: '1.2345' },
      cached_input_price_per_million: { testid: 'cached_input_price', shows: '0.1111' },
      cache_write_price_per_million: { testid: 'cache_write_price', shows: '2.2222' },
      output_price_per_million: { testid: 'output_price', shows: '6.7890' },
      is_priced: null, // said by the two prices, which read "no price" without one
      updated_at: {
        testid: 'updated_at',
        shows: formatDate('2026-08-09T10:00:00Z', 'yyyy-MM-dd HH:mm', 'en-US'),
      },
      capabilities: { testid: 'capabilities', shows: 'generate, tools' },
      publisher: { testid: 'publisher', shows: 'publisher-y' },
      platform: { testid: 'platform', shows: 'platform-z' },
      addressing: { testid: 'regions', shows: 'europe-west4, europe-west1' },
      underlying_model: null, // read by nothing (`test_every_model_control_is_reachable.py`)
      context_window: { testid: 'context_window', shows: '1048576' },
      max_output_tokens: { testid: 'max_output_tokens', shows: '4096' },
      default_max_output_tokens: { testid: 'default_max_output_tokens', shows: '512' },
      thinking: { testid: 'thinking', shows: 'disabled' },
      embedding: { testid: 'embedding', shows: 'supports_batch' },
      attachments: { testid: 'attachments', shows: 'application/pdf' },
      hosting: { testid: 'hosting', shows: 'self_deployed' },
      fixed_sampling: { testid: 'fixed_sampling', shows: 'yes' },
      default_sampling: { testid: 'default_sampling', shows: 'top_k' },
      deprecated: { testid: 'deprecated', shows: 'yes' },
      numeric_id: { testid: 'numeric_id', shows: '9001' },
      is_declared: null, // said by the capabilities, which read "undeclared" without it
    };
    const harness = setup({ models: of([EVERYTHING]) });
    harness.openFirst();

    for (const [field, expected] of Object.entries(SHOWN_AS)) {
      if (!expected) continue;
      const cell = harness.html().querySelector(`[data-testid="detail-${expected.testid}"]`);
      expect(cell?.textContent, `the panel does not show ${field}`).toContain(expected.shows);
    }
    // A timestamp as a person reads it, like every other table here — not the wire format.
    expect(harness.text()).not.toContain('2026-08-09T10:00:00Z');

    // **And two it must not show**: `underlying_model` and the addressing keys besides the
    // regions. Printed among Provider, Platform and Hosting they read as configuration; a field
    // that steers a request gets a control instead (`test_every_model_control_is_reachable.py`).
    expect(harness.text()).not.toContain('underlying-q');
    expect(harness.text()).not.toContain('dep-42');
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
    /** The opened panel answers what the row's columns abbreviate. (Row geometry is measured in a
     *  real browser by `console-usability.spec.ts`, the only place `getComputedStyle` means
     *  anything.) */
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
    /** A list of everything an endpoint offers, loaded on every visit beside an "Add" button,
     *  reads as a to-do list. It is an action. */
    const { discovery } = setup();
    expect(discovery.served()).toBeNull();

    discovery.discover();

    expect(discovery.served()?.length).toBe(2);
    expect(discovery.alreadyCatalogued()).toBe(1);
    expect(discovery.notCatalogued().map((m) => m.name)).toEqual(['gemini-3.1-flash-lite']);
  });

  it('copies where a model lives and nothing else', () => {
    /** **The property an eager implementation breaks.** Provenance is a fact the adapter was
     *  configured with. A capability is a claim (`FRD-131` found a model that lists `tools` and
     *  answers in prose) and a price nobody set is not zero (`FRD-403`) — so the editor still asks,
     *  and an administrator who does not know the price has not declared the model free. */
    const { component, editor, discovery } = setup();
    discovery.discover();

    component.importServed(discovery.notCatalogued()[0]);

    // The bare name, never Google's `models/…` resource form — that would catalogue an entry no
    // request can match, and it looks right in the table. The service strips it at the edge; this
    // asserts the value that reaches the form.
    expect(editor.name()).toBe('gemini-3.1-flash-lite');
    expect(editor.name()).not.toContain('models/');
    expect(editor.provider()).toBe('generative-language');
    expect(editor.publisher()).toBe('google');
    expect(editor.inputPrice()).toBe('');
    expect(editor.capabilities()).toEqual([]);
    expect(editor.approved()).toBe(false);
  });

  it('says so when the catalog already has everything the gateway serves', () => {
    /** "Nothing to import" and "the gateway serves nothing" are different facts, and only one of
     *  them is good news. The empty state names which. */
    const { discovery, text, fixture } = setup({
      served: of([{ name: 'flash', airaDeclared: true }]),
    });

    discovery.discover();
    fixture.detectChanges();
    expect(discovery.notCatalogued()).toEqual([]);
    expect(text()).toContain('Every model the gateway serves is in the catalog');
  });
});

describe('ModelCatalog — discovery when things are missing', () => {
  it('reports a gateway that cannot be asked, instead of an empty list', () => {
    /** An empty list and an unreachable gateway look identical and are fixed differently — the
     *  same distinction `FRD-603` drew between "nothing happened" and "not yours to see". */
    const { discovery, feedback } = setup({ served: throwError(() => ({ status: 503 })) });

    discovery.discover();

    expect(discovery.served()).toBeNull();
    expect(feedback.error()).toBeTruthy();
    expect(discovery.discovering()).toBe(false);
  });

  it('shows an em dash where an adapter has declared no provenance', () => {
    /** A self-hosted server may name no region, and the mock names nothing at all. Blank stays
     *  blank: an adapter that declared nothing has not made a claim, and rendering an empty string
     *  as a value would turn silence into an assertion. */
    const harness = setup({ served: of([{ name: 'mock-1', airaDeclared: false }]) });
    const { component, editor, discovery, text, fixture } = harness;

    discovery.discover();
    fixture.detectChanges();

    expect(discovery.notCatalogued().map((m) => m.name)).toEqual(['mock-1']);
    expect(text()).toContain('mock-1');
    const cells = [...harness.html().querySelectorAll('tbody tr')]
      .find((tr) => tr.textContent?.includes('mock-1'))!
      .querySelectorAll('td');
    expect(cells[1].textContent?.trim()).toBe('— / —');
    expect(cells[2].textContent?.trim()).toBe('—');

    component.importServed(discovery.notCatalogued()[0]);
    expect(editor.name()).toBe('mock-1');
    expect(editor.provider()).toBe('');
  });
});

describe('ModelCatalog — the provider field in the editor (`FRD-507` stage C)', () => {
  it('offers what the gateway is configured with, in the DOM, rather than a typed string', () => {
    /** Chosen from what the gateway has rather than typed: a typo produces two correct refusals
     *  (`not in the model catalog`, `has not been approved`) and neither names the string.
     *
     *  Asserted on the rendered `<option>`s: a component that held the list and rendered a text
     *  input would pass every signal assertion in this file. */
    const { editor, fixture, html } = setup();

    editor.add();
    fixture.detectChanges();

    const select = html().querySelector<HTMLSelectElement>('[data-testid="provider-select"]');
    expect(select).not.toBeNull();
    const options = [...select!.options].map((o) => o.value);
    expect(options).toContain('generative-language');
    expect(options).toContain('vertex');
    // The escape hatch is always there: declaring a model before its platform is configured is
    // the ordinary order of work, and a closed list would forbid it.
    expect(options).toContain(editor.OTHER);
  });

  it('names the vendor and keeps the identifier visible', () => {
    /** `generative-language` beside `local` names neither vendor. The label leads and the name
     *  stays in brackets, because the name is what gets written into the catalog and onto every
     *  audit row. */
    const { editor } = setup();

    expect(editor.providerLabel(STUDIO)).toContain('Google AI Studio');
    expect(editor.providerLabel(STUDIO)).toContain('generative-language');
    expect(editor.providerLabel(VERTEX)).toContain('europe-west1');
  });

  it('says whether cataloguing the model will be enough to reach it', () => {
    /** The field that decides whether an import produces a working model or a convincing
     *  decoration. Where the model name is not the whole addressing, the model must also be named
     *  in the gateway's configuration — and an administrator who is not told finds out from a
     *  caller. */
    const { editor, fixture, text } = setup();

    editor.add();
    editor.chooseProvider('generative-language');
    fixture.detectChanges();
    expect(text()).toContain('is enough to reach it');

    editor.chooseProvider('vertex');
    fixture.detectChanges();
    expect(text()).toContain("named in the gateway's configuration");
  });

  it('falls back to a typed provider when the gateway cannot be asked, and still saves', () => {
    /** Informs, never blocks. Declaring a model before its credential exists is the ordinary order
     *  of work, so a gateway that cannot be reached degrades to the text box it replaced rather
     *  than locking somebody out of their own catalog — `FRD-114`'s rule that deprecation warns
     *  and revocation blocks, one screen over. */
    const { editor, fixture, html, text } = setup({
      providers: throwError(() => ({ status: 503 })),
    });

    editor.add();
    fixture.detectChanges();

    expect(editor.providerIsCustom()).toBe(true);
    expect(text()).toContain('Type the provider name instead');
    expect(html().querySelector('[data-testid="provider-select"]')).toBeNull();
    expect(html().querySelector('[data-testid="provider-typed"]')).not.toBeNull();
  });

  it('lets a provider be typed and taken back to the list', () => {
    const { editor, fixture, html } = setup();

    editor.add();
    editor.chooseProvider(editor.OTHER);
    fixture.detectChanges();
    expect(editor.provider()).toBe('');
    expect(html().querySelector('[data-testid="provider-typed"]')).not.toBeNull();

    html().querySelector<HTMLButtonElement>('[data-testid="provider-back-to-list"]')!.click();
    fixture.detectChanges();
    expect(html().querySelector('[data-testid="provider-select"]')).not.toBeNull();
  });

  it('keeps a publisher the administrator typed when the provider declares none', () => {
    /** A provider that states no publisher has not said the field is empty. Overwriting a value
     *  somebody entered with a blank is the import rule inverted: silence becoming a decision. */
    const { editor } = setup({
      providers: of([{ ...STUDIO, publisher: '', region: '', label: '' }]),
    });

    editor.add();
    editor.publisher.set('google');
    editor.chooseProvider('generative-language');

    expect(editor.publisher()).toBe('google');
    // And a provider with neither label nor region is named by itself rather than trailed by a
    // separator or wrapped in brackets around its own name.
    expect(editor.providerLabel(editor.providers()![0])).toBe('generative-language');
  });

  it('shows a configured provider as chosen even when the list arrives after the form', async () => {
    /** Opening the editor is what fetches the list, so a form carrying a provider is laid out
     *  before the answer comes back, and the field has to settle in both directions on arrival.
     *
     *  The **arrival has to be late**, or this proves nothing: a stubbed `of()` answers inside the
     *  call that started it, so the deferred rule would never run. */
    const late = new Subject<GatewayProvider[]>();
    const { component, editor, fixture, html } = setup({ providers: late });

    component.edit({ ...FLASH, provider: 'vertex' });
    // A row on file states where the model lives, so the editor summarises it rather than asking
    // again. This test is about the select's behaviour, so it opens the block the way a reader
    // correcting the provenance would.
    editor.openProvenance();
    fixture.detectChanges();
    // Nothing is known yet, so the field is the text box it degrades to.
    expect(editor.providerIsCustom()).toBe(true);

    late.next([STUDIO, VERTEX]);
    late.complete();
    fixture.detectChanges();
    // `ngModel` writes the DOM value on a microtask: the select is a *form control*, not an
    // interpolation.
    await fixture.whenStable();

    expect(editor.providerIsCustom()).toBe(false);
    expect(html().querySelector<HTMLSelectElement>('[data-testid="provider-select"]')!.value).toBe(
      'vertex',
    );
  });

  it('leaves the provider select alone for a model that has no provider at all', () => {
    /** An older catalog row, or one added before this field meant anything. Blank is not a typed
     *  value: the select simply shows nothing chosen. */
    const { component, editor } = setup();

    component.edit({ ...UNPRICED });

    expect(editor.provider()).toBe('');
    expect(editor.providerIsCustom()).toBe(false);
  });
});

describe('ModelCatalog — one entrance, and what it stops asking', () => {
  /**
   * **A model lives somewhere, and the software already knows where.** Adding starts with the
   * provider, and the five facts it answers — provider, publisher, platform, hosting, the KIRA id —
   * are stated rather than asked again.
   */
  it('offers one way in, and it starts with the provider', () => {
    const { component, editor, html } = setup();

    const buttons = [...html().querySelectorAll<HTMLButtonElement>('button')].filter((button) =>
      /add a model|add model/i.test(button.textContent ?? ''),
    );
    expect(buttons.length).toBe(1);

    buttons[0].click();
    // The browse window, not the editor: nothing is typed before it is known where it goes.
    expect(component.showBrowse()).toBe(true);
    expect(editor.showAdd()).toBe(false);
  });

  it('states where the model lives instead of asking, once something knows', () => {
    const { component, editor, fixture, html } = setup();

    component.addManually();
    editor.provider.set('vertex');
    editor.publisher.set('google');
    editor.platform.set('vertex');
    fixture.detectChanges();

    const summary = html().querySelector('[data-testid="provenance-summary"]');
    expect(summary).not.toBeNull();
    // The **whole sentence**, not a substring of it. A summary assembled from template blocks
    // strands its punctuation — the formatter puts each `@if` on its own line, and the text node
    // that follows begins with a newline, so a reader gets "vertex , speaking". Asserting
    // `toContain('vertex')` would pass through that happily.
    expect(summary!.textContent!.replace(/\s+/g, ' ').trim()).toBe(
      'Lives on Google Vertex AI (vertex), published by google, on the vertex platform. Change',
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
    const { component, editor, fixture, html } = setup();

    component.addByName();
    fixture.detectChanges();

    expect(editor.provenanceKnown()).toBe(false);
    expect(html().querySelector('[data-testid="provenance-fields"]')).not.toBeNull();
    expect(html().querySelector('[data-testid="provenance-summary"]')).toBeNull();
  });

  it('keeps the fields on screen while the reader is using them', () => {
    /** Without a latch, picking a provider makes one "known" and collapses the block, taking the
     *  select out of the DOM under the pointer that just used it. */
    const { component, editor, fixture, html } = setup();

    component.addByName();
    fixture.detectChanges();
    editor.chooseProvider('vertex');
    fixture.detectChanges();

    expect(editor.provenanceKnown()).toBe(true);
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
    /** Every entrance to the editor starts without a verdict: a green badge about the row that was
     *  checked, on a form for a new model, is the wrong answer wearing a right one's clothes. */
    const harness = setup({
      check: of({
        model: 'gemini-2.0-flash',
        declared: true,
        served: true,
        reachable: true,
        detail: 'gemini-2.0-flash answered',
      }),
    });
    const { component, editor, fixture, html } = harness;

    harness.openFirst();
    html().querySelector<HTMLElement>('[data-testid^="check-gemini"]')!.click();
    fixture.detectChanges();
    expect(component.check()).not.toBeNull();

    component.addManually();
    fixture.detectChanges();
    expect(editor.check()).toBeNull();
    expect(html().querySelector('[data-testid="editor-verdict"]')).toBeNull();
  });

  it('leaves a way forward when there is no provider to choose', () => {
    /** The empty form stays reachable where it is the honest option: a gateway with no upstream
     *  has nothing to fill anything in from, and this is how a fresh installation declares a
     *  model at all. */
    const { component, editor, fixture, html } = setup({ providers: of([]) });

    component.openBrowse();
    fixture.detectChanges();
    expect(html().querySelector('[data-testid="browse-no-providers"]')).not.toBeNull();

    html().querySelector<HTMLButtonElement>('[data-testid="add-by-name"]')!.click();
    fixture.detectChanges();

    expect(component.showBrowse()).toBe(false);
    expect(editor.showAdd()).toBe(true);
    expect(html().querySelector('#model-name')).not.toBeNull();
  });
});

describe('ModelCatalog — browsing what a provider offers (`FRD-507` stage C)', () => {
  it('lists every provider, and says which one publishes no list', () => {
    /** `canEnumerate` is stated rather than discovered by trying, and a capability gap is never a
     *  fault. Nor is it silence: a provider without a listing is listed and marked, because an
     *  absent entry is indistinguishable from a credential that did not work.
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
    const { component, editor, fixture, html } = setup();

    html().querySelector<HTMLButtonElement>('[data-testid="add-model"]')!.click();
    fixture.detectChanges();
    component.browseProvider.set('vertex');
    fixture.detectChanges();

    expect(html().querySelector('[data-testid="provider-not-askable"]')).not.toBeNull();
    html().querySelector<HTMLButtonElement>('[data-testid="add-manually"]')!.click();
    fixture.detectChanges();

    expect(editor.provider()).toBe('vertex');
    expect(html().querySelector('#model-name')).not.toBeNull();
  });

  it('says whether naming the model there is enough, as the gateway reports it', () => {
    /** The gateway states per provider whether a catalogue entry reaches a model (`FRD-507`). The
     *  window said a configuration entry was needed on every unlistable platform — on Vertex,
     *  where cataloguing has been enough since stage B, it sent whoever added a model to edit an
     *  environment file that needed no line. */
    const way = (cataloguedIsEnough: boolean) => {
      const { component, fixture, html } = setup({
        providers: of([STUDIO, { ...VERTEX, cataloguedIsEnough }]),
      });
      html().querySelector<HTMLButtonElement>('[data-testid="add-model"]')!.click();
      fixture.detectChanges();
      component.browseProvider.set('vertex');
      fixture.detectChanges();
      return html().querySelector('[data-testid="provider-not-askable-way-in"]')!.textContent ?? '';
    };

    expect(way(true)).toContain('cataloguing the model is enough');
    expect(way(true)).not.toContain('AIRA_FOUNDRY_DEPLOYMENTS');
    expect(way(false)).toContain('must also be named in the gateway');
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
   * `catalogueOffered` closes the window **without** clearing the provider (it records where the
   * model came from), so reopening must ask for the remembered provider again — or the select
   * names it with nothing under it.
   *
   * Asserted on **what was asked of the gateway**, not on what is on screen: an empty list and a
   * list that was never requested render identically.
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
    const { component, feedback, fixture, text } = setup({
      offerings: throwError(() => ({ status: 502 })),
    });

    component.openBrowse();
    fixture.detectChanges();

    expect(component.offeringsError()).toBeTruthy();
    expect(feedback.error()).toBeNull();
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
    const { component, editor, fixture, html, text } = setup();

    component.openBrowse();
    fixture.detectChanges();
    html().querySelector<HTMLButtonElement>('[data-testid="offered-gemini-flash-latest"]')!.click();
    fixture.detectChanges();

    // One window at a time: the list is a choice, made once.
    expect(component.showBrowse()).toBe(false);
    expect(editor.showAdd()).toBe(true);

    expect(editor.name()).toBe('gemini-flash-latest');
    expect(editor.displayName()).toBe('Gemini Flash Latest');
    expect(editor.maxOutput()).toBe(65536);
    expect(editor.provider()).toBe('generative-language');
    expect(editor.publisher()).toBe('google');
    expect(editor.platform()).toBe('generative-language');
    expect(editor.capabilities().sort()).toEqual(['generate', 'prompt_caching']);

    expect(editor.inputPrice()).toBe('');
    expect(editor.outputPrice()).toBe('');
    expect(editor.approved()).toBe(false);
    expect(editor.hasCapability('thinking')).toBe(false);
    expect(editor.hasCapability('tools')).toBe(false);

    // And it says which half is which: the **sentence** states what is now known, the **note**
    // what the import left alone — each once, or a reader looks for the difference.
    expect(text()).toContain('Lives on Google AI Studio (generative-language)');
    expect(text()).toContain('a price nobody set is not zero');
    expect(text()).not.toContain('Filled in from');
    expect(editor.vendorSaid().join(' ')).toContain('it reasons');
  });

  it('opens the existing declaration for a model the catalog already has', () => {
    /** Corrected, never added a second time — and never as a blank form carrying the vendor's
     *  answer, which would replace a measured capability or a price with a claim. */
    const { component, editor, fixture, html } = setup({
      offerings: of([{ ...OFFERED_FLASH, name: 'gemini-2.0-flash' }]),
    });

    component.openBrowse();
    fixture.detectChanges();
    html().querySelector<HTMLButtonElement>('[data-testid="offered-gemini-2.0-flash"]')!.click();

    expect(editor.editing()).toBe('gemini-2.0-flash');
    expect(editor.inputPrice()).toBe('0.075');
  });

  it('turns a vendor saying nothing into no declaration at all', () => {
    /** `null` is a third answer, not a missing one. An OpenAI-compatible listing publishes bare
     *  ids, and `false` would be a *statement* that the model cannot generate — pre-filled into a
     *  form somebody is about to save, it becomes their decision (`FRD-114` FR-7). */
    const { component, editor } = setup({ offerings: of([OFFERED_EMBED]) });

    component.openBrowse();
    component.catalogueOffered(component.offerings()![0]);

    expect(editor.name()).toBe('text-embedding-004');
    expect(editor.capabilities()).toEqual([]);
    expect(editor.maxOutput()).toBeNull();
  });

  it('adds capabilities and never removes one the administrator ticked', () => {
    /** A vendor's silence must not untick a box somebody ticked from a measurement they made —
     *  which is the direction that matters, since the catalog is where measurements are kept. */
    const { component, editor } = setup();

    component.openBrowse();
    editor.capabilities.set(['tools']);
    editor.useOffered(OFFERED_FLASH);

    expect(editor.capabilities().sort()).toEqual(['generate', 'prompt_caching', 'tools']);
  });

  it('counts a live listing as having looked — but only where cataloguing is enough', () => {
    /** `mustCheck` refuses the *ignorance*, not the verdict, and a listing the vendor answered a
     *  second ago is a stronger answer than a ping. Where cataloguing is **not** enough the model
     *  still needs a configuration entry, and the check is exactly what says so — so that case
     *  keeps the gate. */
    const enough = setup();
    enough.component.openBrowse();
    enough.component.catalogueOffered(OFFERED_FLASH);
    expect(enough.editor.mustCheck()).toBe(false);

    const notEnough = setup({
      providers: of([{ ...STUDIO, name: 'half-wired', cataloguedIsEnough: false }]),
    });
    notEnough.component.openBrowse();
    notEnough.component.catalogueOffered(OFFERED_FLASH);
    expect(notEnough.editor.mustCheck()).toBe(true);
  });

  it('closes without touching the form behind it', () => {
    /** The browse window has its own provider signal. Looking at a list must not edit a
     *  half-finished declaration underneath — and the two would otherwise be one field. */
    const { component, editor, fixture } = setup();

    editor.add();
    editor.chooseProvider('vertex');
    component.openBrowse();
    fixture.detectChanges();
    component.closeBrowse();

    expect(editor.provider()).toBe('vertex');
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
    const { component, editor } = setup({ providers: throwError(() => ({ status: 503 })) });

    component.openBrowse();
    component.catalogueOffered(OFFERED_FLASH);

    expect(editor.showAdd()).toBe(true);
    expect(editor.name()).toBe('gemini-flash-latest');
    expect(editor.provider()).toBe('');
  });

  it("carries an embedding verb and the vendor's own description", () => {
    const { component, editor, fixture, text } = setup({
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

    expect(editor.capabilities()).toEqual(['embed']);
    expect(text()).toContain('distributed representation');
  });

  it("reads the editor's own provider when a model is filled in outside the window", () => {
    /** `useOffered` is reachable from the editor too, and "is cataloguing enough to reach this"
     *  has to be answered about the provider on the *form* then — not about a window that is not
     *  open. */
    const { editor } = setup();

    editor.add();
    editor.chooseProvider('generative-language');
    editor.useOffered(OFFERED_FLASH);

    expect(editor.mustCheck()).toBe(false);
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
 * The three declaration blocks (`FRD-114`): `thinking`, `embedding` and `attachments`, which the
 * API accepts and the console must be able to write — a capability with no way in reads as a
 * design decision. The API upserts and leaves omitted fields alone, so these tests are about what
 * the form can *say*.
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
    /** `approved` is what makes a model callable (`FRD-307`), and it starts **off**. Behind a tab,
     *  somebody fills in three screens, saves, and creates a model nothing can call — so it sits
     *  beside Save on every tab, with deprecation: both are about the model's standing.
     */
    const harness = setup();
    const catalog = harness.editor;
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
    const catalog = harness.editor;
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
    const catalog = harness.editor;
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
    const catalog = harness.editor;
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
    const catalog = harness.editor;
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

  it('a ticked media type needs no estimate, and the box says what the gateway reserves', async () => {
    /** An administrator cannot know what one PDF costs a model. Left empty it is sent as `null`,
     *  and the gateway reserves its built-in estimate — which the box shows, so empty is not read
     *  as "nothing" (`FRD-110`). */
    const harness = setup();
    const catalog = harness.editor;
    catalog.edit(DECLARED);
    catalog.editorTab.set('capabilities');
    catalog.toggleMediaType('image/png', true);
    harness.fixture.detectChanges();
    await Promise.resolve();

    const box = harness.testid('media-tokens-image/png') as HTMLInputElement;
    expect(box.placeholder).toBe('1600');
    expect(harness.testid('media-tokens-why')?.textContent).toContain('optional');
    catalog.save();
    expect(harness.saved[0].attachments?.media_types?.['image/png']).toBeNull();
  });

  it('sends the estimate that was typed beside the media type', async () => {
    /** Typed into the rendered input rather than set through the component: what is being
     *  prevented is a control that renders and sends nothing. */
    const harness = setup();
    const catalog = harness.editor;
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
    const catalog = harness.editor;
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
    const catalog = harness.editor;
    catalog.edit(DECLARED);
    catalog.toggleCapability('embed', false);
    catalog.toggleCapability('attachments', false);
    catalog.toggleCapability('thinking', false);
    catalog.save();

    const sent = harness.saved[0];

    // All three, and `null` rather than "falsy": an empty object saves, the model keeps the
    // capability, and nothing is declared about it.
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
    const catalog = harness.editor;
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
    const catalog = harness.editor;
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
    const catalog = harness.editor;
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
    const catalog = harness.editor;
    catalog.showAdd.set(true);
    catalog.capabilities.set(['embed']);
    catalog.dimensions.set('384, nonsense, 768, -1');

    expect(catalog.declaredDimensions()).toEqual([384, 768]);
  });
});

/**
 * The thinking levels are the vendor's own words (`ADR-0021`), not a token table: no vendor
 * publishes a budget per level, and a guessed one goes upstream as a **ceiling on the model's
 * reasoning**. Free text has to earn its place, so the model itself says whether a word works.
 */
describe('ModelCatalog — thinking levels are the vendor’s own words', () => {
  it('takes a word, lower-cases it, and refuses a duplicate quietly', () => {
    const { editor } = setup();
    editor.add();

    editor.levelDraft.set('Low');
    editor.addLevel();
    editor.levelDraft.set('  low  ');
    editor.addLevel();
    editor.levelDraft.set('high');
    editor.addLevel();

    // `Low` and `low` are the same instruction to every vendor, so two chips saying so would be
    // two chips the reader has to reconcile.
    expect(editor.thinkingLevels()).toEqual(['low', 'high']);
    expect(editor.levelDraft()).toBe('');
  });

  it('refuses a thinking mode typed as though it were a vendor word', () => {
    /** `disabled`, `auto` and `limited` are settings the gateway translates per dialect, not words
     *  a vendor takes. A model listing one as a level would send that string upstream and mean
     *  something else by it — and it is a mistake somebody makes precisely because the two lists
     *  sit one above the other. */
    const { editor, feedback } = setup();
    editor.add();

    editor.levelDraft.set('auto');
    editor.addLevel();

    expect(editor.thinkingLevels()).toEqual([]);
    expect(feedback.error()).toContain('thinking mode');
  });

  it('offers modes and levels together as a default', () => {
    /** A default is whatever the model does when nobody says, and that is as likely to be a level
     *  as a mode. Offering only the modes would make a level undeclarable without saying so. */
    const { editor } = setup();
    editor.add();
    editor.toggleThinkingMode('auto', true);
    editor.levelDraft.set('high');
    editor.addLevel();

    expect(editor.declarableDefaults()).toEqual(['auto', 'high']);
  });

  it('asks the model and marks each word with what it answered', async () => {
    const harness = setup();
    const { component, editor } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    editor.thinkingLevels.set(['low']);
    // Typed but not committed: pressing the button without leaving the box would otherwise check
    // everything *except* the word the reader is looking at.
    editor.levelDraft.set('medium');
    editor.checkLevels();

    // **With where the form says it lives**: both checks ask about the editor's state, not the
    // saved row being replaced.
    expect(harness.askedLevels).toEqual([
      {
        model: 'gemini-2.5-flash',
        levels: ['low', 'medium'],
        where: { provider: FLASH.provider ?? '', publisher: '', region: '' },
        modes: editor.thinkingModes(),
      },
    ]);
    expect(editor.levelVerdict('low')).toEqual({ ok: true, detail: 'The model accepted it.' });
    // **The provider's own words**, which is the whole value of the button: no rule here could
    // have said `thinking_level is not supported by this model` as precisely, or stayed as true.
    expect(editor.levelVerdict('medium')?.ok).toBe(false);
    expect(editor.levelVerdict('medium')?.detail).toContain('thinking_level is not supported');
  });

  it('marks a thinking mode the model’s own dialect cannot express', () => {
    // `auto` ticked for a model on an OpenAI-dialect endpoint would be accepted in silence and fail
    // every thinking request after. The adapters declare which modes their wire format can
    // express; this reads that declaration.
    const { component, editor } = setup();
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    editor.thinkingModes.set(['disabled', 'auto']);

    editor.checkLevels();

    expect(editor.modeVerdicts()['disabled'].ok).toBe(true);
    expect(editor.modeVerdicts()['auto'].ok).toBe(false);
    // The sentence says what declaring it would *do*. The reader is deciding whether to leave a
    // box ticked, and "unsupported" alone does not tell them.
    expect(editor.modeVerdicts()['auto'].detail).toContain('never reached');
  });

  it('shows the verdict beside the box it is about, and the sentence only for a refusal', () => {
    // The DOM, not the signal: what an administrator acts on is a mark next to a checkbox. A green
    // one needs no explanation under it — a red one is the whole point, so its sentence is
    // rendered rather than hidden in a tooltip.
    const harness = setup();
    const catalog = harness.editor;
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
    harness.editor.thinkingModes.set(['disabled', 'auto']);
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[data-testid^="mode-verdict-"]')).toBeNull();
  });

  it('clears the marks when a later question fails, rather than leaving stale ones', () => {
    // `FRD-506`: a red mark says "this model refuses it", and an unreachable gateway says nothing
    // about it. **The first press has to succeed**, or there are no marks to leave behind and this
    // asserts nothing.
    const { component, editor } = setup({
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
    editor.thinkingModes.set(['auto']);

    editor.checkLevels();
    expect(editor.modeVerdicts()['auto'].ok).toBe(false);

    editor.checkLevels();

    expect(editor.modeVerdicts()).toEqual({});
  });

  it('clears the previous answers when the question itself fails', () => {
    /** A red chip says *the model refused this word*; an unreachable gateway says nothing about
     *  the word, and the **last** answers must not stand beside a failed question.
     *
     *  **Asked twice on purpose**: with a single failing call there is nothing to clear, and the
     *  assertion would pass against code that clears nothing. */
    const harness = setup({
      levelChecks: [
        of({
          model: 'gemini-2.5-flash',
          results: [{ region: '', level: 'low', accepted: true, detail: 'The model accepted it.' }],
        }),
        throwError(() => ({ status: 503 })),
      ],
    });
    const { component, editor, feedback } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    editor.thinkingLevels.set(['low']);

    editor.checkLevels();
    expect(editor.levelVerdict('low')?.ok).toBe(true);

    editor.checkLevels();
    expect(editor.levelVerdict('low')).toBeNull();
    expect(feedback.error()).toContain('Could not ask');
  });

  it('removes a word, its verdict, and a default naming it', () => {
    const harness = setup();
    const { component, editor } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-flash' });
    editor.thinkingLevels.set(['low']);
    editor.checkLevels();
    editor.thinkingDefault.set('low');
    expect(editor.levelVerdict('low')).not.toBeNull();

    editor.removeLevel('low');

    expect(editor.thinkingLevels()).toEqual([]);
    expect(editor.levelVerdict('low')).toBeNull();
    // Or the save fails against a validator refusing a default nothing declares, for a reason the
    // reader cannot see on screen.
    expect(editor.thinkingDefault()).toBe('');
  });
});

/**
 * Residency, refused where the region is typed rather than where it is used (`ADR-0012` §6).
 *
 * The gateway enforces `AIRA_ALLOWED_REGIONS` when it *addresses* a request — correct, and weeks
 * late for whoever catalogued the model. The console refuses earlier from the list the gateway
 * publishes, never from a copy of its own: one policy, owned by one plane.
 */
describe('ModelCatalog — a region this installation does not permit', () => {
  it('refuses one that is not on the gateway’s list, and names what is', () => {
    const { editor } = setup({ allowedRegions: ['eu', 'europe-west1'] });
    editor.add();
    editor.name.set('gemini-3.5-flash');
    editor.regions.set(['global']);

    expect(editor.regionAllowed('global')).toBe(false);
    expect(editor.forbiddenRegions()).toEqual(['global']);
    expect(editor.formError()).toContain("Region 'global' is not permitted by this installation");
    // The remedy names where the policy lives, because it is not changed here.
    expect(editor.formError()).toContain('AIRA_ALLOWED_REGIONS');
    expect(editor.canSave()).toBe(false);
  });

  it('permits one that is on the list', () => {
    const { editor } = setup({ allowedRegions: ['eu', 'europe-west1'] });
    editor.add();
    editor.name.set('m');
    editor.regions.set(['europe-west1']);

    expect(editor.regionAllowed('europe-west1')).toBe(true);
    expect(editor.forbiddenRegions()).toEqual([]);
    // Named, so the region rule is the one being asked rather than "a model id is required".
    expect(editor.formError()).toBeNull();
  });

  it('has no opinion about an empty region', () => {
    /** Empty is the correct answer for a platform addressed by model name alone — AI Studio, an
     *  OpenAI-compatible server — where a region would be a claim about routing nothing acts on. */
    const { editor } = setup({ allowedRegions: ['eu'] });
    editor.add();
    editor.name.set('m');
    editor.regionDraft.set('   ');
    editor.addRegion();

    expect(editor.regions()).toEqual([]);
    expect(editor.forbiddenRegions()).toEqual([]);
    expect(editor.formError()).toBeNull();
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
    const { editor } = setup({ allowedRegions: [] });
    editor.add();
    editor.name.set('m');
    editor.regions.set(['somewhere-else']);

    expect(editor.regionAllowed('somewhere-else')).toBeNull();
    expect(editor.forbiddenRegions()).toEqual([]);
    expect(editor.formError()).toBeNull();
  });

  it('offers the permitted regions without confining the field to them', () => {
    /** A `datalist`, not a `<select>`: somebody widening the policy on the gateway should be able
     *  to type the new region before this console has been reloaded, and a region the list does
     *  not carry deserves the refusal above rather than silent absence. */
    const { editor, fixture, html } = setup({ allowedRegions: ['eu', 'europe-west1'] });
    editor.add();
    fixture.detectChanges();

    const offered = [...html().querySelectorAll('#allowed-regions option')].map(
      (option) => (option as HTMLOptionElement).value,
    );
    expect(offered).toEqual(['eu', 'europe-west1']);
    expect(html().querySelector('#model-region')?.tagName).toBe('INPUT');
  });

  it('says so beside the field, not only in the footer', () => {
    const { editor, fixture, html } = setup({ allowedRegions: ['eu'] });
    editor.add();
    editor.regions.set(['global']);
    fixture.detectChanges();

    const note = html().querySelector('[data-testid="region-not-permitted"]');
    expect(note).not.toBeNull();
    expect(note!.textContent).toContain('eu');
  });

  it('permits a region the gateway allows this model by name — and no other model', () => {
    /** `global` names no region, and the open models on Agent Platform answer nowhere else: an
     *  installation permits it **for them** (`AIRA_ALLOWED_MODEL_REGIONS`), not for every model.
     *  The editor follows the gateway's decision both ways — refusing it for the named model would
     *  block the very entry the decision was made for, and accepting it for another would be a
     *  second, wider policy. */
    const { editor } = setup({
      allowedRegions: ['eu', 'europe-west1'],
      modelRegions: { 'glm-4.7-maas': ['global'] },
    });
    editor.add();
    editor.name.set('glm-4.7-maas');
    editor.regions.set(['global']);

    expect(editor.regionAllowed('global')).toBe(true);
    expect(editor.forbiddenRegions()).toEqual([]);
    expect(editor.formError()).toBeNull();

    editor.name.set('gemini-3.5-flash');

    expect(editor.regionAllowed('global')).toBe(false);
    expect(editor.forbiddenRegions()).toEqual(['global']);
    expect(editor.formError()).toContain("Region 'global' is not permitted by this installation");
    // Both places the policy can be widened, since this one is a decision about one model.
    expect(editor.formError()).toContain('AIRA_ALLOWED_MODEL_REGIONS');
    expect(editor.canSave()).toBe(false);
  });

  it('offers a model the regions it is permitted by name, and names them as allowed', () => {
    const { editor, fixture, html } = setup({
      allowedRegions: ['eu'],
      modelRegions: { 'deepseek-v3.2-maas': ['global'] },
    });
    editor.add();
    editor.name.set('deepseek-v3.2-maas');
    fixture.detectChanges();

    const offered = () =>
      [...html().querySelectorAll('#allowed-regions option')].map(
        (option) => (option as HTMLOptionElement).value,
      );
    expect(offered()).toEqual(['eu', 'global']);
    expect(editor.permittedRegions()).toEqual(['eu', 'global']);

    editor.name.set('another-model');
    fixture.detectChanges();
    expect(offered()).toEqual(['eu']);
  });

  it('still has no opinion when the gateway did not say, whatever it permits by name', () => {
    /** A permission by name widens a list; it does not stand in for one. Without the list the
     *  console has nothing to measure against, exactly as before. */
    const { editor } = setup({ allowedRegions: [], modelRegions: { m: ['global'] } });
    editor.add();
    editor.name.set('m');
    editor.regions.set(['somewhere-else']);

    expect(editor.regionAllowed('somewhere-else')).toBeNull();
    expect(editor.forbiddenRegions()).toEqual([]);
  });
});

/**
 * Several regions, in the order they should be tried, each checkable (`FRD-609`).
 *
 * Order is the meaning, not the presentation: the first entry is where an ordinary request goes,
 * and the rest are what the gateway falls back to when a region cannot serve.
 */
describe('ModelCatalog — a model in several regions', () => {
  it('keeps the order they were typed in', () => {
    const { editor } = setup();
    editor.add();

    for (const region of ['europe-west4', 'europe-west1', 'eu']) {
      editor.regionDraft.set(region);
      editor.addRegion();
    }

    expect(editor.regions()).toEqual(['europe-west4', 'europe-west1', 'eu']);
  });

  it('drops a repeat rather than queueing the same failure twice', () => {
    const { editor } = setup();
    editor.add();
    editor.regionDraft.set('eu');
    editor.addRegion();
    editor.regionDraft.set('  eu  ');
    editor.addRegion();

    expect(editor.regions()).toEqual(['eu']);
  });

  it('sends them as a list, and null when there are none', () => {
    /** `{regions: [...]}` is the shape both planes read. A model addressed by name alone — an
     *  OpenAI-compatible server, AI Studio — sends `null`, because an empty object would be a
     *  claim about routing that nothing acts on. */
    const harness = setup({ allowedRegions: ['europe-west1', 'europe-west4'] });
    const { editor } = harness;
    editor.add();
    editor.name.set('m');
    editor.regions.set(['europe-west1', 'europe-west4']);
    harness.lookFirst();
    editor.save();

    expect(harness.saved[0].addressing).toEqual({
      regions: ['europe-west1', 'europe-west4'],
    });

    editor.add();
    editor.name.set('m2');
    harness.lookFirst();
    editor.save();
    expect(harness.saved[1].addressing).toBeNull();
  });

  it('reads a model saved with the old single-region shape', () => {
    /** Rows written before a model could name several still carry `{region: "x"}`, and a
     *  redelivered event can carry it after a rollback. One reader for both spellings, in the same
     *  place the gateway has one. */
    const { component, editor } = setup();
    component.edit({ ...FLASH, addressing: { region: 'europe-west3' } });

    expect(editor.regions()).toEqual(['europe-west3']);
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
    const { component, editor } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-pro' });
    editor.regions.set(['europe-west1', 'europe-west4']);
    harness.lookFirst();

    expect(editor.regionVerdict('europe-west1')).toEqual({
      ok: false,
      detail: 'Vertex returned 404.',
    });
    expect(editor.regionVerdict('europe-west4')?.ok).toBe(true);
  });

  it('says why a check failed, once per cause, with the regions that gave it', () => {
    /** A ✗ with the cause in its tooltip is where the cause goes unread. The cause is a sentence
     *  that says what to change, and the same cause in two regions is one thing to fix
     *  (`FRD-141`). */
    const enable = 'The API aiplatform.googleapis.com is not enabled in project 4711.';
    const harness = setup({
      check: of({
        model: 'gemini-2.5-pro',
        declared: true,
        served: true,
        reachable: false,
        detail: 'Not reachable (403 SERVICE_DISABLED: ...).',
        reason: 'SERVICE_DISABLED',
        explanation: enable,
        regions: [
          {
            region: 'europe-west1',
            reachable: false,
            detail: 'x',
            reason: 'SERVICE_DISABLED',
            explanation: enable,
          },
          {
            region: 'europe-west4',
            reachable: false,
            detail: 'x',
            reason: 'SERVICE_DISABLED',
            explanation: enable,
          },
          { region: 'europe-west3', reachable: false, detail: 'TimeoutError' },
        ],
      }),
    });
    const { component, editor } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-pro' });
    editor.regions.set(['europe-west1', 'europe-west4', 'europe-west3']);
    harness.lookFirst();

    const causes = harness.html().querySelectorAll('[data-testid="check-causes"] li');
    expect(causes).toHaveLength(1);
    expect(causes[0].textContent).toContain('SERVICE_DISABLED');
    expect(causes[0].textContent).toContain('europe-west1, europe-west4');
    expect(causes[0].textContent).toContain(enable);
  });

  it('says the cause of a check that names no region', () => {
    const harness = setup({
      check: of({
        model: 'm',
        declared: true,
        served: true,
        reachable: false,
        detail: 'Not reachable.',
        reason: 'CERTIFICATE_NOT_TRUSTED',
        explanation: 'Point SSL_CERT_FILE at your bundle.',
        regions: [{ region: '', reachable: false, detail: 'x' }],
      }),
    });
    harness.component.edit({ ...FLASH, name: 'm' });
    harness.lookFirst();

    expect(harness.testid('cause-CERTIFICATE_NOT_TRUSTED')?.textContent).toContain('SSL_CERT_FILE');
  });

  it('shows no cause for a model that answered', () => {
    const harness = setup();
    harness.component.edit({ ...FLASH, name: 'gemini-2.5-pro' });
    harness.lookFirst();

    expect(harness.testid('check-causes')).toBeNull();
  });

  it('asks about every region it holds', () => {
    const harness = setup();
    const { component, editor } = harness;
    component.edit({ ...FLASH, name: 'gemini-2.5-pro' });
    editor.regions.set(['europe-west1', 'europe-west4']);
    editor.thinkingLevels.set(['low']);
    editor.checkLevels();

    // Comma-separated on the wire, because a query parameter is a string; one shape, split by the
    // one reader that knows what it means.
    expect(harness.askedLevels[0].where.region).toBe('europe-west1,europe-west4');
  });

  it('refuses only the regions the installation does not permit, and names them', () => {
    const { editor } = setup({ allowedRegions: ['eu', 'europe-west1'] });
    editor.add();
    editor.name.set('m');
    editor.regions.set(['europe-west1', 'global', 'us-central1']);

    expect(editor.forbiddenRegions()).toEqual(['global', 'us-central1']);
    expect(editor.formError()).toContain("'global', 'us-central1'");
    expect(editor.formError()).toContain('are not permitted');
    // The permitted one is not implicated.
    expect(editor.regionAllowed('europe-west1')).toBe(true);
  });

  it('removes a region and the verdict that was about it', () => {
    const { editor } = setup();
    editor.add();
    editor.regions.set(['eu', 'europe-west1']);
    editor.regionVerdicts.set({ eu: { ok: true, detail: 'answered' } });

    editor.removeRegion('eu');

    expect(editor.regions()).toEqual(['europe-west1']);
    expect(editor.regionVerdict('eu')).toBeNull();
  });
});

/** Type into a control the way a person does — `ngModel` inside a `<form>` registers on a
 *  microtask, so the caller awaits one before typing. */
function typeInto(harness: ReturnType<typeof setup>, selector: string, value: string): void {
  const input = harness.html().querySelector<HTMLInputElement>(selector)!;
  input.value = value;
  input.dispatchEvent(new Event('input'));
  harness.fixture.detectChanges();
}

function submit(harness: ReturnType<typeof setup>): HTMLButtonElement {
  return harness.html().querySelector<HTMLButtonElement>('button[type="submit"]')!;
}

describe('ModelCatalog — adding never replaces a model the catalog already has', () => {
  /** A save is an upsert by name on the server, so an "add" carrying a catalogued name is a full
   *  blank declaration applied over the stored one: approval, price, capabilities and KIRA id
   *  gone, under a banner saying it was saved. Every entrance but the vendor listing reached that. */
  const STORED: CatalogModel = { ...FLASH, approved: true, numeric_id: 9001 };

  it('refuses a catalogued name typed into the empty form, and offers the stored declaration', async () => {
    const harness = setup({ providers: of([]), models: of([STORED, UNPRICED]) });
    harness.click('[data-testid="add-model"]');
    harness.click('[data-testid="add-by-name"]');
    await Promise.resolve();
    typeInto(harness, '#model-name', 'gemini-2.0-flash');
    harness.lookFirst();

    expect(harness.editor.formError()).toContain("'gemini-2.0-flash' is already in the catalog");
    expect(submit(harness).disabled).toBe(true);
    submit(harness).click();
    harness.editor.save();
    expect(harness.saved).toEqual([]);

    // The way forward is the declaration on file, with its price and its release.
    harness.click('[data-testid="open-stored"]');
    expect(harness.editor.editing()).toBe('gemini-2.0-flash');
    expect(harness.editor.inputPrice()).toBe('0.075');
    expect(harness.editor.approved()).toBe(true);
  });

  it('opens the stored declaration for a served model the catalog took in meanwhile', () => {
    /** The served list is the gateway's answer, and its `airaDeclared` lags behind the event bus:
     *  after one save it still offers the model it just catalogued. */
    let catalog: CatalogModel[] = [FLASH];
    const harness = setup({
      models: defer(() => of(catalog)),
      served: of([
        {
          name: 'new-x',
          airaDeclared: false,
          airaProvider: 'generative-language',
          airaPublisher: 'google',
        },
      ]),
    });
    harness.discovery.discover();
    harness.fixture.detectChanges();
    harness.click('[data-testid="import-new-x"]');
    harness.editor.inputPrice.set('1.00');
    harness.editor.outputPrice.set('2.00');
    harness.editor.approved.set(true);
    harness.lookFirst();
    catalog = [
      FLASH,
      {
        name: 'new-x',
        input_price_per_million: '1.00',
        output_price_per_million: '2.00',
        is_priced: true,
        approved: true,
      },
    ];
    harness.editor.save();
    harness.fixture.detectChanges();

    expect(harness.saved).toHaveLength(1);
    // No longer offered as missing — the catalog behind the window says otherwise.
    expect(harness.html().querySelector('[data-testid="import-new-x"]')).toBeNull();

    // And a stale row, if one is used, corrects the stored model instead of blanking it.
    harness.component.importServed({ name: 'new-x', airaDeclared: false });
    expect(harness.editor.editing()).toBe('new-x');
    expect(harness.editor.inputPrice()).toBe('1.00');
    expect(harness.editor.approved()).toBe(true);
  });
});

describe('ModelCatalog — a level word as the default', () => {
  it('sends a level chosen as the default as that word', async () => {
    /** The gateway sends whatever word is declared as the default (`thinking._default_for`), and a
     *  level is as likely a default as a mode. Pinned here so the console's half of that contract
     *  cannot drift while Management's validator catches up. */
    const harness = setup();
    harness.editor.add();
    harness.editor.name.set('qwen3:4b');
    harness.editor.toggleCapability('thinking', true);
    harness.editor.levelDraft.set('high');
    harness.editor.addLevel();
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    await Promise.resolve();

    const select = harness.html().querySelector<HTMLSelectElement>('#think-default')!;
    select.value = 'high';
    select.dispatchEvent(new Event('change'));
    harness.fixture.detectChanges();
    harness.lookFirst();
    harness.editor.save();

    expect(harness.saved[0].thinking?.default).toEqual({ mode: 'high' });
    expect(harness.saved[0].thinking?.levels).toEqual(['high']);
  });
});

describe('ModelCatalog — what goes wrong in the window is said in the window', () => {
  it('shows a refused save inside the window the reader is looking at', () => {
    const harness = setup({
      save: throwError(() => ({
        status: 400,
        error: {
          error: {
            message: 'Request failed.',
            details: { numeric_id: ["KIRA id 9001 already belongs to 'gemini-2.0-flash'."] },
          },
        },
      })),
    });
    harness.editor.add();
    harness.editor.name.set('m-1');
    harness.lookFirst();
    submit(harness).click();
    harness.fixture.detectChanges();

    const alert = harness.html().querySelector('[data-testid="model-editor"] [role="alert"]');
    expect(alert?.textContent).toContain("KIRA id 9001 already belongs to 'gemini-2.0-flash'");
    // One report, where the reader is — not a second copy behind the backdrop.
    expect(harness.html().querySelectorAll('[role="alert"]').length).toBe(1);
  });

  it('shows a refused level word inside the window, and lets it go with the window', () => {
    const harness = setup();
    harness.editor.add();
    harness.editor.toggleCapability('thinking', true);
    harness.editor.editorTab.set('capabilities');
    harness.editor.levelDraft.set('auto');
    harness.editor.addLevel();
    harness.fixture.detectChanges();

    const alert = harness.html().querySelector('[data-testid="model-editor"] [role="alert"]');
    expect(alert?.textContent).toContain('thinking mode');

    [...harness.html().querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === 'Cancel')!
      .click();
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('[role="alert"]')).toBeNull();
  });

  it('starts a window without the last window’s report', () => {
    const harness = setup({ save: throwError(() => ({ status: 503 })) });
    harness.editor.add();
    harness.editor.name.set('m-1');
    harness.lookFirst();
    harness.editor.save();
    expect(harness.feedback.error()).toBeTruthy();

    harness.component.edit(FLASH);
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('[data-testid="model-editor"] [role="alert"]')).toBeNull();
  });
});

describe('ModelCatalog — the way in while the gateway cannot be asked', () => {
  it('still lets a model be declared by name, with the provider typed', () => {
    /** The catalog is Management's: an unreachable gateway degrades to a typed provider rather
     *  than locking an administrator out (`FRD-507` FR-6) — and that needs a way into the editor. */
    const harness = setup({ providers: throwError(() => ({ status: 502 })) });
    harness.click('[data-testid="add-model"]');
    expect(harness.testid('browse-providers-error')).not.toBeNull();

    harness.click('[data-testid="add-by-name"]');

    expect(harness.component.showBrowse()).toBe(false);
    expect(harness.editor.showAdd()).toBe(true);
    expect(harness.testid('provider-typed')).not.toBeNull();
  });

  it('asks the gateway again rather than remembering a failure for good', () => {
    let attempts = 0;
    const harness = setup({
      providers: defer(() =>
        ++attempts === 1 ? throwError(() => ({ status: 502 })) : of([STUDIO, VERTEX]),
      ),
    });
    harness.click('[data-testid="add-model"]');
    expect(harness.testid('browse-providers-error')).not.toBeNull();

    harness.click('[data-testid="browse-providers-retry"]');

    expect(attempts).toBe(2);
    expect(harness.testid('browse-providers-error')).toBeNull();
    expect(harness.component.askable().map((p) => p.name)).toEqual(['generative-language']);
  });
});

describe('ModelCatalog — an answer lands only on the subject it was asked about', () => {
  it('never lists one provider’s models under another', () => {
    const studio = new Subject<OfferedModel[]>();
    let calls = 0;
    const harness = setup({
      providers: of([STUDIO, { ...STUDIO, name: 'ollama', label: 'ollama' }]),
      offerings: defer(() =>
        ++calls === 1 ? studio : of([{ ...OFFERED_EMBED, name: 'llama-local' }]),
      ),
    });
    harness.component.openBrowse();
    harness.component.chooseBrowseProvider('generative-language');
    harness.component.chooseBrowseProvider('ollama');

    studio.next([{ ...OFFERED_FLASH, name: 'gemini-only-on-studio' }]);
    studio.complete();

    expect(harness.component.offerings()?.map((m) => m.name)).toEqual(['llama-local']);
  });

  it('does not put one row’s reachability verdict under another', () => {
    const pending = new Subject<ModelCheck>();
    const harness = setup({
      check: pending,
      models: of([FLASH, { ...FLASH, name: 'zz-other', display_name: '' }]),
    });
    harness.click('[data-testid="open-model-gemini-2.0-flash"]');
    harness.click('[data-testid="check-gemini-2.0-flash"]');
    harness.click('[data-testid="open-model-zz-other"]');

    pending.next({ ...CHECK, detail: 'gemini-2.0-flash answered' });
    pending.complete();
    harness.fixture.detectChanges();

    expect(harness.component.check()).toBeNull();
    expect(harness.testid('check-detail')).toBeNull();
    const button = harness.testid('check-zz-other') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.textContent).toContain('Check reachability');
  });

  it('forgets a check still in flight when the window closes', () => {
    const pending = new Subject<ModelCheck>();
    const harness = setup({ check: pending });
    harness.component.edit(FLASH);
    harness.fixture.detectChanges();
    harness.click('[data-testid="editor-check"]');
    [...harness.html().querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === 'Cancel')!
      .click();
    harness.fixture.detectChanges();

    harness.component.edit({ ...FLASH, name: 'zz-other' });
    harness.fixture.detectChanges();
    const button = harness.testid('editor-check') as HTMLButtonElement;
    expect(button.textContent).toContain('Check reachability');
    expect(button.disabled).toBe(false);

    pending.next(CHECK);
    pending.complete();
    harness.fixture.detectChanges();
    expect(harness.editor.check()).toBeNull();
    expect(harness.editor.checkedName()).toBeNull();
    expect(harness.testid('editor-verdict')).toBeNull();
  });

  it('forgets a question to the model still in flight when the window closes', () => {
    const pending = new Subject<ThinkingLevelCheck>();
    const harness = setup({ levelChecks: [pending] });
    const thinker: CatalogModel = {
      ...FLASH,
      capabilities: ['thinking'],
      thinking: { modes: [], levels: ['low'] },
    };
    harness.component.edit(thinker);
    harness.editor.checkLevels();
    [...harness.html().querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === 'Cancel')
      ?.click();
    harness.fixture.detectChanges();
    harness.component.edit({ ...thinker, name: 'zz-other' });

    pending.next({
      model: 'gemini-2.0-flash',
      modes: [],
      results: [
        { region: '', level: 'low', accepted: false, detail: 'refused by gemini-2.0-flash' },
      ],
    });
    pending.complete();

    expect(harness.editor.levelVerdict('low')).toBeNull();
  });

  it('forgets a verdict when the provider it was asked about changes', () => {
    const harness = setup();
    harness.editor.add();
    harness.editor.name.set('m-1');
    harness.editor.chooseProvider('vertex');
    harness.lookFirst();
    expect(harness.editor.mustCheck()).toBe(false);

    harness.editor.chooseProvider('generative-language');

    expect(harness.editor.check()).toBeNull();
    expect(harness.editor.mustCheck()).toBe(true);
  });
});

describe('ModelCatalog — removing a model says what it costs', () => {
  it('names the refusals and the lost releases before removing', () => {
    /** A model the catalog does not hold is refused (`FRD-307`), and deleting the row deletes every
     *  use case's release of it — which adding it back does not restore. */
    const harness = setup();
    const ask = vi.spyOn(TestBed.inject(ConfirmService), 'ask');
    harness.openFirst();
    harness.click('[data-testid="remove-gemini-2.0-flash"]');

    const question = ask.mock.calls[0][0];
    expect(question).toContain('refused');
    expect(question).toContain('release');
    expect(question).not.toContain('no longer be priced');
  });
});

describe('ModelCatalog — the KIRA id of a catalogued model', () => {
  it('will not quietly take a model off the KIRA surface by clearing its id', async () => {
    /** The server assigns an id only on create; an edit sending `null` stores `null`, and the model
     *  stops answering on the KIRA surface while looking fully configured. */
    const harness = setup();
    harness.component.edit({ ...FLASH, numeric_id: 9001 });
    harness.fixture.detectChanges();
    harness.click('[data-testid="change-provenance"]');
    await Promise.resolve();
    harness.type('model-kira-id', '');

    expect(harness.editor.kiraId()).toBeNull();
    expect(harness.editor.formError()).toContain('KIRA');
    expect(submit(harness).disabled).toBe(true);
    harness.editor.save();
    expect(harness.saved).toEqual([]);
  });

  it('still leaves the id of a new model to the server', () => {
    const harness = setup();
    harness.editor.add();
    harness.editor.name.set('fresh-1');
    harness.lookFirst();
    expect(harness.editor.formError()).toBeNull();
  });
});

describe('ModelCatalog — asking the gateway about a model needs operations.diagnose', () => {
  it('offers the reachability check only to a reader who may diagnose', () => {
    const reader = setup({ permissions: ['usecase.read_all', 'report.read_all'] });
    reader.openFirst();
    expect(reader.html().querySelector('[data-testid^="check-gemini"]')).toBeNull();

    const diagnoser = setup({ permissions: ['operations.diagnose'] });
    diagnoser.openFirst();
    expect(diagnoser.html().querySelector('[data-testid^="check-gemini"]')).not.toBeNull();
  });

  it('does not demand a check from somebody who cannot run one, and says why', () => {
    const harness = setup({ permissions: ['catalog.write'] });
    harness.editor.add();
    harness.editor.name.set('m-1');
    harness.fixture.detectChanges();

    expect(harness.testid('editor-check')).toBeNull();
    expect(harness.editor.mustCheck()).toBe(false);
    expect(submit(harness).disabled).toBe(false);
    expect(harness.text()).toContain('operations.diagnose');

    harness.editor.toggleCapability('thinking', true);
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    expect(harness.testid('check-levels')).toBeNull();
  });
});

describe('ModelCatalog — where a model lives, copied the same way by every entrance', () => {
  it('copies the region an adapter names, through the served list, the provider and the listing', () => {
    const harness = setup({
      providers: of([{ ...STUDIO, region: 'europe-west1' }, VERTEX]),
      served: of([
        {
          name: 'served-1',
          airaDeclared: false,
          airaProvider: 'vertex',
          airaPublisher: 'google',
          airaRegion: 'europe-west1',
        },
      ]),
    });
    const { component, editor, discovery } = harness;

    discovery.discover();
    component.importServed(discovery.notCatalogued()[0]);
    expect(editor.regions()).toEqual(['europe-west1']);
    expect(editor.platform()).toBe('vertex');

    component.openBrowse();
    component.chooseBrowseProvider('vertex');
    component.addManually();
    expect(editor.provider()).toBe('vertex');
    expect(editor.platform()).toBe('vertex');
    expect(editor.regions()).toEqual(['europe-west1']);

    component.openBrowse();
    component.chooseBrowseProvider('generative-language');
    component.catalogueOffered(OFFERED_FLASH);
    expect(editor.platform()).toBe('generative-language');
    expect(editor.regions()).toEqual(['europe-west1']);
  });
});

describe('ModelCatalog — asking a model about its modes alone', () => {
  it('asks about the ticked modes even when no level word is declared', () => {
    /** `gemini-2.5-flash` has modes and no levels — the example the levels hint itself names. */
    const harness = setup();
    harness.editor.add();
    harness.editor.name.set('gemini-2.5-flash');
    harness.editor.toggleCapability('thinking', true);
    harness.editor.toggleThinkingMode('auto', true);
    harness.editor.toggleThinkingMode('limited', true);
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();

    const button = harness.testid('check-levels') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    button.click();

    expect(harness.askedLevels[0].modes).toEqual(['auto', 'limited']);
  });
});

describe('ModelCatalog — a later success clears an earlier failure', () => {
  it('for a row’s reachability check', () => {
    let calls = 0;
    const harness = setup({
      check: defer(() => (++calls === 1 ? throwError(() => ({ status: 502 })) : of(CHECK))),
    });
    harness.openFirst();
    harness.click('[data-testid="check-gemini-2.0-flash"]');
    expect(harness.html().querySelector('[role="alert"]')).not.toBeNull();

    harness.click('[data-testid="check-gemini-2.0-flash"]');
    expect(harness.html().querySelector('[role="alert"]')).toBeNull();
    expect(harness.testid('check-verdict')?.textContent).toContain('Reachable');
  });

  it('for the editor’s check and its question to the model', () => {
    let calls = 0;
    const harness = setup({
      check: defer(() => (++calls === 1 ? throwError(() => ({ status: 502 })) : of(CHECK))),
      levelChecks: [throwError(() => ({ status: 502 }))],
    });
    harness.editor.add();
    harness.editor.name.set('m-1');
    harness.fixture.detectChanges();
    harness.click('[data-testid="editor-check"]');
    expect(harness.html().querySelector('[role="alert"]')).not.toBeNull();
    harness.click('[data-testid="editor-check"]');
    expect(harness.html().querySelector('[role="alert"]')).toBeNull();

    harness.editor.thinkingLevels.set(['low']);
    harness.editor.checkLevels();
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('[role="alert"]')).not.toBeNull();
    harness.editor.checkLevels();
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('[role="alert"]')).toBeNull();
  });

  it('for asking the gateway what it serves', () => {
    let calls = 0;
    const harness = setup({
      served: defer(() =>
        ++calls === 1
          ? throwError(() => ({ status: 503 }))
          : of([{ name: 'flash', airaDeclared: true }]),
      ),
    });
    harness.click('[data-testid="discover-models"]');
    expect(harness.html().querySelector('[role="alert"]')).not.toBeNull();
    harness.click('[data-testid="discover-models"]');
    expect(harness.html().querySelector('[role="alert"]')).toBeNull();
  });
});

describe('ModelCatalog — a gateway that serves nothing', () => {
  it('says the gateway serves no models, rather than that the catalog has them all', () => {
    const harness = setup({ served: of([]) });
    harness.click('[data-testid="discover-models"]');

    expect(harness.text()).toContain('serves no models');
    expect(harness.text()).not.toContain('Every model the gateway serves is in the catalog');
  });
});

describe('ModelCatalog — the browse window is a window', () => {
  it('closes on Escape and takes the keyboard when it opens', () => {
    const harness = setup();
    harness.click('[data-testid="add-model"]');
    const window = harness.html().querySelector<HTMLElement>('[role="dialog"]')!;
    expect(window.contains(document.activeElement)).toBe(true);

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    harness.fixture.detectChanges();

    expect(harness.component.showBrowse()).toBe(false);
    expect(harness.html().querySelector('[role="dialog"]')).toBeNull();
  });
});

describe('ModelCatalog — a value the catalog cannot store is refused by name', () => {
  it('names the price field and the limit it breaks', () => {
    const harness = setup();
    harness.editor.add();
    harness.editor.name.set('m-1');
    harness.editor.outputPrice.set('1');

    harness.editor.inputPrice.set('0.1234567');
    expect(harness.editor.formError()).toContain('Input price');
    expect(harness.editor.formError()).toContain('6 decimal');

    harness.editor.inputPrice.set('1234567');
    expect(harness.editor.formError()).toContain('Input price');
    expect(harness.editor.formError()).toContain('6 digits');

    harness.editor.inputPrice.set('999999.999999');
    expect(harness.editor.formError()).toBeNull();
  });

  it('refuses a width it cannot read instead of dropping it', () => {
    const harness = setup();
    harness.editor.add();
    harness.editor.name.set('embed-1');
    harness.editor.toggleCapability('embed', true);
    harness.editor.dimensions.set('768, 1O24');

    expect(harness.editor.formError()).toContain("'1O24'");
    expect(harness.editor.canSave()).toBe(false);
  });

  it('refuses what the server would refuse about caps and thinking budgets', () => {
    const harness = setup();
    const { editor } = harness;
    editor.add();
    editor.name.set('m-1');

    editor.contextWindow.set(4096);
    editor.maxOutput.set(8192);
    expect(editor.formError()).toContain('context window');
    editor.contextWindow.set(null);

    editor.toggleCapability('thinking', true);
    editor.thinkingMin.set(4096);
    editor.thinkingMax.set(1024);
    expect(editor.formError()).toContain('smallest thinking budget');

    editor.thinkingMin.set(null);
    editor.thinkingMax.set(8192);
    expect(editor.formError()).toContain('below the output cap');

    editor.thinkingMax.set(null);
    editor.toggleThinkingMode('limited', true);
    expect(editor.formError()).toContain('largest thinking budget');
  });
});

describe('ModelCatalog — an edit keeps what the form does not show', () => {
  it('keeps addressing keys besides the regions', () => {
    /** The editor offers the regions; anything else in the block — a deployment, an endpoint id —
     *  was written by the API or a seed and must survive a save from the console. */
    const harness = setup();
    harness.component.edit({
      ...FLASH,
      addressing: { regions: ['europe-west1'], deployment: 'prod-1' },
    });
    harness.editor.save();

    expect(harness.saved[0].addressing).toEqual({
      regions: ['europe-west1'],
      deployment: 'prod-1',
    });

    harness.component.edit({ ...FLASH, addressing: { region: 'europe-west3', deployment: 'p' } });
    harness.editor.regions.set([]);
    harness.editor.save();
    expect(harness.saved[1].addressing).toEqual({ deployment: 'p' });
  });
});

describe('ModelCatalog — the editor is a section of its own (FRD-508 FR-6)', () => {
  it('replaces the catalog while it is open, and gives it back', () => {
    /** A window bounded by the viewport compressed three tabs of fields until each scrolled inside
     *  it. The section takes the page; the catalog returns when it closes. */
    const harness = setup();
    harness.editor.add();
    harness.fixture.detectChanges();
    const q = (selector: string) => harness.html().querySelector(selector);

    expect(q('[role="dialog"]')).toBeNull();
    expect(q('[data-testid="model-editor"]')?.getAttribute('role')).toBe('region');
    expect(q('#model-editor-title')?.textContent).toContain('Add a model');
    expect(q('[data-testid="add-model"]')).toBeNull();
    // Save stays in reach: the action row is the one that sticks to the viewport's bottom.
    expect(q('.editor-page__actions button[type="submit"]')).not.toBeNull();

    harness.click('[data-testid="model-editor-close"]');
    expect(q('[data-testid="model-editor"]')).toBeNull();
    expect(q('[data-testid="add-model"]')).not.toBeNull();
  });

  it('puts its address in the URL, so Back closes it', async () => {
    const harness = setup();
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    harness.editor.add();
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    expect(router.url).toBe('/?editor=new');

    harness.editor.name.set('typo');
    await router.navigateByUrl('/');
    harness.fixture.detectChanges();

    expect(harness.editor.showAdd()).toBe(false);
    expect(harness.editor.name()).toBe('');
    expect(harness.saved).toEqual([]);
  });

  it('names the model being corrected in the URL, and closing takes it out again', async () => {
    const harness = setup();
    const router = TestBed.inject(Router);
    harness.component.edit(FLASH);
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    expect(router.url).toBe('/?editor=gemini-2.0-flash');

    harness.click('[data-testid="model-editor-close"]');
    await harness.fixture.whenStable();
    expect(router.url).toBe('/');
  });

  it('opens the model a link names, once the catalog has arrived', async () => {
    const harness = setup();
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/?editor=gemini-2.0-flash');
    harness.fixture.detectChanges();

    expect(harness.editor.showAdd()).toBe(true);
    expect(harness.editor.name()).toBe('gemini-2.0-flash');

    await router.navigateByUrl('/');
    await router.navigateByUrl('/?editor=new');
    harness.fixture.detectChanges();
    expect(harness.editor.showAdd()).toBe(true);
    expect(harness.editor.name()).toBe('');
  });

  it('ignores a link to a model the catalog does not hold', async () => {
    const harness = setup();
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/?editor=nothing-like-it');
    harness.fixture.detectChanges();

    expect(harness.editor.showAdd()).toBe(false);
  });
});

describe('ModelCatalog — the model reference (FRD-508)', () => {
  function onVertex(options: Parameters<typeof setup>[0] = {}) {
    const harness = setup(options);
    harness.component.openBrowse();
    harness.fixture.detectChanges();
    harness.component.chooseBrowseProvider('vertex');
    harness.fixture.detectChanges();
    return harness;
  }

  it('lists what the reference says about the chosen platform, and nothing about another', () => {
    /** Vertex publishes no list a credential may read; the reference is the list there is. */
    const harness = onVertex();

    expect(harness.testid('reference-row-claude-sonnet-9')).not.toBeNull();
    expect(harness.testid('reference-row-gemini-studio')).toBeNull();
    expect(harness.testid('reference-count')?.textContent).toContain(
      '2 described by https://models.dev/api.json, as of 2026-10-17',
    );
  });

  it('puts a deprecated model last and says it is deprecated', () => {
    const harness = onVertex();

    const rows = [...harness.html().querySelectorAll('[data-testid^="reference-row-"]')];
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual([
      'reference-row-claude-sonnet-9',
      'reference-row-gemini-old',
    ]);
    expect(harness.testid('reference-status-gemini-old')?.textContent).toContain('deprecated');
  });

  it('finds a model by part of its name', () => {
    const harness = onVertex();

    harness.type('reference-search', 'sonnet');

    expect(harness.testid('reference-row-gemini-old')).toBeNull();
    harness.type('reference-search', 'nothing like it');
    expect(harness.testid('reference-no-match')).not.toBeNull();
  });

  it('fills the editor with everything the list states, and says where it came from', () => {
    const harness = onVertex({ currency: 'USD' });

    harness.click('[data-testid="reference-pick-claude-sonnet-9"]');
    const { editor } = harness;

    expect(editor.name()).toBe('claude-sonnet-9');
    expect(editor.provider()).toBe('vertex');
    expect(editor.publisher()).toBe('anthropic');
    expect(editor.contextWindow()).toBe(1000000);
    expect(editor.maxOutput()).toBe(64000);
    expect(editor.capabilities()).toEqual([
      'generate',
      'tools',
      'attachments',
      'thinking',
      'prompt_caching',
    ]);
    expect(editor.thinkingModes()).toEqual(['limited']);
    expect([editor.thinkingMin(), editor.thinkingMax()]).toEqual([1024, 63999]);
    expect(editor.thinkingLevels()).toEqual(['low', 'high']);
    expect(editor.inputPrice()).toBe('3');
    expect(editor.cacheWritePrice()).toBe('3.75');
    // The region is never guessed: residency is not a guess this product may make.
    expect(editor.regions()).toEqual([]);

    const banner = harness.testid('reference-filled')?.textContent ?? '';
    expect(banner).toContain('https://models.dev/api.json, as of 2026-10-17');
    expect(banner).toContain(
      'capabilities, thinking, documents and images, fixed sampling, prices',
    );
    expect(editor.fixedSampling()).toBe(true);
    expect(banner).toContain('the thinking ceiling is the output cap less one');
  });

  it('leaves the prices to you where the installation counts in another currency', () => {
    /** A dollar figure in a euro field is a budget wrong by the exchange rate that looks right. */
    const harness = onVertex({ currency: 'EUR' });

    harness.click('[data-testid="reference-pick-claude-sonnet-9"]');

    expect(harness.editor.inputPrice()).toBe('');
    expect(harness.editor.outputPrice()).toBe('');
    expect(harness.testid('reference-prices')?.textContent).toContain(
      'input 3, output 15, cached input 0.3, cache write 3.75 USD per million tokens',
    );
    expect(harness.testid('reference-filled')?.textContent).not.toContain('images, prices');
  });

  it('saves what was filled once a region is added and looked at', () => {
    const harness = onVertex({ currency: 'USD' });
    harness.click('[data-testid="reference-pick-claude-sonnet-9"]');
    const { editor } = harness;
    editor.regions.set(['europe-west1']);
    harness.lookFirst();

    editor.save();

    const stored = harness.saved[0];
    expect(stored.publisher).toBe('anthropic');
    expect(stored.thinking).toMatchObject({
      modes: ['limited'],
      min_tokens: 1024,
      max_tokens: 63999,
      levels: ['low', 'high'],
    });
    expect(Object.keys(stored.attachments?.media_types ?? {})).toEqual([
      'application/pdf',
      'image/png',
    ]);
    expect(stored.approved).toBe(false);
  });

  it('opens a model the catalog already holds rather than drafting over it', () => {
    const harness = onVertex({
      models: of([{ ...FLASH, name: 'claude-sonnet-9', input_price_per_million: '9.99' }]),
    });

    harness.click('[data-testid="reference-pick-claude-sonnet-9"]');

    expect(harness.editor.inputPrice()).toBe('9.99');
    expect(harness.testid('reference-filled')).toBeNull();
  });

  it('says when a configured live list could not be read', () => {
    const harness = onVertex({
      reference: of({ ...REFERENCE, live_error: 'https://x could not be read (ConnectError).' }),
    });

    expect(harness.testid('reference-live-error')?.textContent).toContain('ConnectError');
  });

  it('says so when the reference cannot be loaded, and can ask again', () => {
    const harness = onVertex({
      reference: throwError(() => ({
        status: 503,
        error: { error: { message: 'Management is down.' } },
      })),
    });

    expect(harness.testid('reference-error')?.textContent).toContain('Management is down.');
  });

  it('offers no reference for a platform it does not describe', () => {
    const harness = setup({
      providers: of([{ ...STUDIO, name: 'foundry', label: 'Microsoft Foundry' }]),
    });
    harness.component.openBrowse();
    harness.fixture.detectChanges();
    harness.component.chooseBrowseProvider('foundry');
    harness.fixture.detectChanges();

    expect(harness.testid('reference-models')).toBeNull();
  });
});

describe('ModelEditor — filling a model named by hand (FRD-508)', () => {
  it('offers the reference for the id typed, and fills it keeping where the model lives', async () => {
    /** "Add a model on Vertex" and typing the id is the way in for a model nobody listed for
     *  you; the list's facts must reach that form too, not only the picker's. */
    const harness = setup({ currency: 'USD' });
    const editor = harness.editor as unknown as Editor & {
      startOn: (upstream: GatewayProvider | null) => void;
    };
    editor.startOn(VERTEX);
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    expect(harness.testid('fill-from-reference')).toBeNull();

    harness.type('model-name-input', 'claude-sonnet-9');
    expect(harness.testid('reference-match')?.textContent).toContain('claude-sonnet-9');
    harness.click('[data-testid="fill-from-reference"]');

    expect(editor.publisher()).toBe('anthropic');
    expect(editor.contextWindow()).toBe(1000000);
    expect(editor.thinkingLevels()).toEqual(['low', 'high']);
    expect(editor.inputPrice()).toBe('3');
    expect(editor.regions()).toEqual(['europe-west1']);
    expect(harness.testid('reference-filled')).not.toBeNull();
  });

  it('offers the reference ids of the chosen provider as the id is typed', () => {
    const harness = setup();
    (harness.editor as unknown as { startOn: (u: GatewayProvider) => void }).startOn(VERTEX);
    harness.fixture.detectChanges();

    const options = [...harness.html().querySelectorAll('#reference-names option')].map((o) =>
      o.getAttribute('value'),
    );
    expect(options).toEqual(['gemini-old', 'claude-sonnet-9']);
  });

  it('offers nothing over a declaration on file', () => {
    const harness = setup();
    harness.component.edit({ ...FLASH, name: 'claude-sonnet-9', provider: 'vertex' });
    harness.fixture.detectChanges();

    expect(harness.testid('fill-from-reference')).toBeNull();
  });

  it('names a model by hand without a reference that could not be loaded', async () => {
    const harness = setup({ reference: throwError(() => ({ status: 403 })) });
    (harness.editor as unknown as { startOn: (u: GatewayProvider) => void }).startOn(VERTEX);
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    harness.type('model-name-input', 'claude-sonnet-9');

    expect(harness.testid('fill-from-reference')).toBeNull();
    expect(harness.editor.name()).toBe('claude-sonnet-9');
  });
});

/**
 * The thinking default the reference proposes (`FRD-151`): what the vendor documents a model doing
 * when a request names nothing. Without it the gateway cannot know the model is thinking, and its
 * reasoning — billed either way — never comes back.
 */
describe('ModelEditor — the thinking default the reference proposes (FRD-151)', () => {
  const THINKS_UNASKED: ReferenceEntry = {
    ...SONNET,
    declaration: {
      ...SONNET.declaration,
      thinking: { modes: ['auto'], levels: ['low', 'high'], default: { mode: 'auto' } },
    },
  };
  const ON_FILE: CatalogModel = {
    ...FLASH,
    name: 'claude-sonnet-9',
    provider: 'vertex',
    capabilities: ['generate', 'thinking'],
    thinking: { modes: [], levels: ['low', 'high'] },
  };

  function editing(model: CatalogModel, entry: ReferenceEntry = THINKS_UNASKED) {
    const harness = setup({ reference: of({ ...REFERENCE, entries: [entry] }) });
    harness.component.edit(model);
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    return harness;
  }

  it('offers it beside a model on file that names none, and takes it — with `auto` — on a click', async () => {
    const harness = editing(ON_FILE);

    expect(harness.testid('thinking-default-proposed')?.textContent).toContain('auto');
    harness.click('[data-testid="adopt-thinking-default"]');
    await harness.fixture.whenStable();
    harness.fixture.detectChanges();

    expect(harness.editor.thinkingDefault()).toBe('auto');
    // The declaration on file never offered `auto`; a default it does not offer would not save.
    expect((harness.testid('mode-auto') as HTMLInputElement).checked).toBe(true);
    expect(harness.testid('thinking-default-proposed')).toBeNull();
  });

  it('proposes nothing over a default on file', () => {
    const harness = editing({
      ...ON_FILE,
      thinking: { modes: [], levels: ['low', 'high'], default: { mode: 'low' } },
    });

    expect(harness.testid('thinking-default-proposed')).toBeNull();
  });

  it('proposes no level the declaration on file does not offer', () => {
    const harness = editing(ON_FILE, {
      ...THINKS_UNASKED,
      declaration: {
        ...THINKS_UNASKED.declaration,
        thinking: { modes: [], levels: ['medium'], default: { mode: 'medium' } },
      },
    });

    expect(harness.testid('thinking-default-proposed')).toBeNull();
  });

  it('proposes a level the declaration on file offers', () => {
    const harness = editing(ON_FILE, {
      ...THINKS_UNASKED,
      declaration: {
        ...THINKS_UNASKED.declaration,
        thinking: { modes: [], levels: ['low', 'high'], default: { mode: 'high' } },
      },
    });

    harness.click('[data-testid="adopt-thinking-default"]');

    expect(harness.editor.thinkingDefault()).toBe('high');
    expect(harness.editor.thinkingModes()).toEqual([]);
  });

  it('proposes nothing for another model, nor where the reference names no default', () => {
    const another = editing({ ...ON_FILE, name: 'claude-sonnet-8' });
    const unproposed = editing(ON_FILE, SONNET);

    expect(another.testid('thinking-default-proposed')).toBeNull();
    expect(unproposed.testid('thinking-default-proposed')).toBeNull();
    // Shown at all, so the two above are absences and not a tab nobody opened.
    expect(editing(ON_FILE).testid('thinking-default-proposed')).not.toBeNull();
  });

  it('fills a new entry with it from the reference, like every other fact', async () => {
    const harness = setup({ reference: of({ ...REFERENCE, entries: [THINKS_UNASKED] }) });
    const editor = harness.editor as unknown as Editor & {
      startOn: (upstream: GatewayProvider | null) => void;
    };
    editor.startOn(VERTEX);
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();

    harness.type('model-name-input', 'claude-sonnet-9');
    harness.click('[data-testid="fill-from-reference"]');

    expect(editor.thinkingDefault()).toBe('auto');
    expect(editor.thinkingModes()).toEqual(['auto']);
  });
});

describe('ModelEditor — what a model costs this installation (FRD-509)', () => {
  const BILLED: BillingPrices = {
    model: 'claude-sonnet-9',
    region: 'europe-west1',
    billingAccount: '012345-567890-ABCDEF',
    currency: 'EUR',
    prices: {
      input: {
        perMillion: '2.9',
        currency: 'EUR',
        skuId: 'C-IN',
        skuName: 'Claude Sonnet 9 Input Tokens',
        region: 'europe-west1',
        contract: true,
      },
      output: {
        perMillion: '14.5',
        currency: 'EUR',
        skuId: 'C-OUT',
        skuName: 'Claude Sonnet 9 Output Tokens',
        region: 'europe-west1',
        contract: false,
      },
    },
    unpriced: { cached_input: 'no SKU', cache_write: 'no SKU' },
  };
  const AT_RATE: ModelReference = {
    ...REFERENCE,
    conversion: { from: 'USD', to: 'EUR', rate: '0.92' },
  };

  function picked(options: Parameters<typeof setup>[0]) {
    const harness = setup(options);
    harness.component.openBrowse();
    harness.fixture.detectChanges();
    harness.component.chooseBrowseProvider('vertex');
    harness.fixture.detectChanges();
    harness.click('[data-testid="reference-pick-claude-sonnet-9"]');
    harness.editor.editorTab.set('price');
    harness.fixture.detectChanges();
    return harness;
  }

  it("converts the list price at the installation's rate, and says so under each field", () => {
    const harness = picked({ currency: 'EUR', reference: of(AT_RATE) });

    expect(harness.editor.inputPrice()).toBe('2.76');
    expect(harness.editor.outputPrice()).toBe('13.8');
    expect(harness.editor.cacheWritePrice()).toBe('3.45');
    expect(harness.testid('price-source-input')?.textContent).toContain(
      'list price 3 USD at 0.92 EUR per USD',
    );
  });

  it("takes the billing account's price over any list, naming the SKU", () => {
    const harness = picked({ currency: 'EUR', reference: of(AT_RATE), billing: of(BILLED) });

    expect(harness.editor.inputPrice()).toBe('2.9');
    expect(harness.editor.outputPrice()).toBe('14.5');
    expect(harness.testid('price-source-input')?.textContent).toContain(
      'contract price on billing account 012345-567890-ABCDEF — Claude Sonnet 9 Input Tokens',
    );
    expect(harness.testid('price-source-output')?.textContent).toContain('list price on billing');
    // A field the account bills nothing for keeps the converted list price, and is named.
    expect(harness.editor.cachedPrice()).toBe('0.276');
    expect(harness.testid('price-source-cached_input')?.textContent).toContain('0.92 EUR per USD');
    expect(harness.testid('billing-note')?.textContent).toContain('cached input, cache write');
  });

  it('asks the billing account for the model and its first region', () => {
    const harness = picked({ currency: 'EUR', billing: of(BILLED) });
    expect(harness.billingAsked.at(-1)).toEqual({
      provider: 'vertex',
      model: 'claude-sonnet-9',
      region: '',
    });

    harness.editor.regions.set(['europe-west4']);
    harness.click('[data-testid="ask-billing"]');

    expect(harness.billingAsked.at(-1)?.region).toBe('europe-west4');
  });

  it("never fills a billing price in another currency than the installation's", () => {
    const inDollars = {
      ...BILLED,
      prices: { input: { ...BILLED.prices.input!, currency: 'USD' } },
    };
    const harness = picked({ currency: 'EUR', billing: of(inDollars) });

    expect(harness.editor.inputPrice()).toBe('');
  });

  it('says why the billing account was not asked, and fills what it can without it', () => {
    const harness = picked({ currency: 'EUR', reference: of(AT_RATE) });

    expect(harness.testid('billing-note')?.textContent).toContain('no billing account to ask');
    expect(harness.editor.inputPrice()).toBe('2.76');
  });

  it('forgets where a price came from once it is typed over', async () => {
    const harness = picked({ currency: 'EUR', reference: of(AT_RATE) });
    await harness.fixture.whenStable();

    harness.type('model-input-price', '1.5');

    expect(harness.editor.inputPrice()).toBe('1.5');
    expect(harness.testid('price-source-input')).toBeNull();
  });

  it("converts only into the installation's own currency", () => {
    const harness = picked({ currency: 'CHF', reference: of(AT_RATE) });

    expect(harness.editor.inputPrice()).toBe('');
  });

  it('converts in decimals, not in floats', () => {
    expect(convert('3', '0.92')).toBe('2.76');
    expect(convert('0.0375', '0.92')).toBe('0.0345');
    expect(convert('15', '1')).toBe('15');
    expect(convert('0.0000005', '1')).toBe('0.000001');
  });
});

describe('ModelEditor — a model that samples at its own settings', () => {
  function onCapabilities(options: Parameters<typeof setup>[0] = {}) {
    const harness = setup(options);
    harness.editor.showAdd.set(true);
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    return harness;
  }
  const box = (harness: ReturnType<typeof setup>) =>
    harness.testid('model-fixed-sampling') as HTMLInputElement | null;

  it('offers the setting unticked, with what it does to a request beside it', async () => {
    const harness = onCapabilities();
    await harness.fixture.whenStable();

    expect(harness.testid('sampling-block')?.textContent).toContain('Fixed sampling');
    expect(box(harness)?.checked).toBe(false);

    harness.click('[data-testid="info-fixed-sampling"]');
    const help = (harness.testid('help-fixed-sampling')?.textContent ?? '').replace(/\s+/g, ' ');
    expect(help).toContain('temperature other than 1');
    expect(help).toContain('next model in the chain');
    expect(help).toContain('refused with a message naming the setting');
    expect(help).toContain('model reference fills this in');
  });

  it('sends what the box says, off as well as on', async () => {
    const harness = onCapabilities();
    harness.editor.name.set('claude-opus-9');
    harness.editor.toggleCapability('generate', true);
    harness.lookFirst();
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();

    harness.editor.save();
    expect(harness.saved.at(-1)?.fixed_sampling).toBe(false);

    harness.editor.showAdd.set(true);
    harness.editor.name.set('claude-opus-9');
    harness.editor.toggleCapability('generate', true);
    harness.lookFirst();
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    const input = box(harness)!;
    input.click();
    harness.fixture.detectChanges();
    expect(harness.editor.fixedSampling()).toBe(true);

    harness.editor.save();
    expect(harness.saved.at(-1)?.fixed_sampling).toBe(true);
  });

  it('loads the setting with the declaration, and forgets it with the form', async () => {
    const fixed: CatalogModel = { ...DECLARED, fixed_sampling: true };
    const harness = setup({ models: of([fixed]) });
    harness.component.edit(fixed);
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();

    expect(box(harness)?.checked).toBe(true);

    harness.editor.add();
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    expect(box(harness)?.checked).toBe(false);
  });

  it('reads a row that never said as free sampling', () => {
    const harness = setup({ models: of([DECLARED]) });
    harness.component.edit(DECLARED);

    expect(harness.editor.fixedSampling()).toBe(false);
  });

  it('fills from a draft that does not fix the sampling as off, over a tick in the form', async () => {
    /** Filling a model named by hand keeps the form it fills, so a tick somebody set before
     *  asking would otherwise survive a draft that says the model samples freely. */
    const free: ReferenceEntry = {
      ...SONNET,
      declaration: { ...SONNET.declaration, fixed_sampling: false },
    };
    const harness = setup({ currency: 'USD', reference: of({ ...REFERENCE, entries: [free] }) });
    const editor = harness.editor as unknown as Editor & {
      startOn: (upstream: GatewayProvider | null) => void;
    };
    editor.startOn(VERTEX);
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    editor.fixedSampling.set(true);

    harness.type('model-name-input', 'claude-sonnet-9');
    harness.click('[data-testid="fill-from-reference"]');

    expect(editor.fixedSampling()).toBe(false);
    expect(harness.testid('reference-filled')?.textContent).not.toContain('fixed sampling');
  });
});

describe('ModelEditor — sampling defaults', () => {
  function onCapabilities(options: Parameters<typeof setup>[0] = {}) {
    const harness = setup(options);
    harness.editor.showAdd.set(true);
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    return harness;
  }
  const field = (harness: ReturnType<typeof setup>, which: 'p' | 'k') =>
    harness.testid(`model-default-top-${which}`) as HTMLInputElement | null;

  async function filled(harness: ReturnType<typeof setup>) {
    harness.editor.name.set('kira-chat');
    harness.editor.toggleCapability('generate', true);
    harness.lookFirst();
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
  }

  it('sends nothing on anybody’s behalf while both fields are empty', async () => {
    const harness = onCapabilities();
    await filled(harness);

    expect(field(harness, 'p')?.value).toBe('');
    expect(field(harness, 'k')?.value).toBe('');
    harness.editor.save();
    expect(harness.saved.at(-1)?.default_sampling).toBeNull();
  });

  it('sends exactly the fields that are filled in', async () => {
    const harness = onCapabilities();
    await filled(harness);
    const topK = field(harness, 'k')!;
    topK.value = '40';
    topK.dispatchEvent(new Event('input'));
    harness.fixture.detectChanges();

    harness.editor.save();
    expect(harness.saved.at(-1)?.default_sampling).toEqual({ top_k: 40 });
  });

  it('loads the defaults with the model, and forgets them with the form', async () => {
    const tuned: CatalogModel = { ...DECLARED, default_sampling: { top_p: 0.8, top_k: 40 } };
    const harness = setup({ models: of([tuned]) });
    harness.component.edit(tuned);
    harness.editor.editorTab.set('capabilities');
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();

    expect(harness.editor.defaultTopP()).toBe(0.8);
    expect(harness.editor.defaultTopK()).toBe(40);

    harness.editor.add();
    expect(harness.editor.defaultTopP()).toBeNull();
    expect(harness.editor.defaultTopK()).toBeNull();
  });

  it('says beside the fields that a caller’s own value wins and empty sends nothing', async () => {
    const harness = onCapabilities();
    await harness.fixture.whenStable();

    harness.click('[data-testid="info-sampling-defaults"]');
    const help = (harness.testid('help-sampling-defaults')?.textContent ?? '').replace(/\s+/g, ' ');
    expect(help).toContain('own value always wins');
    expect(help).toContain('nothing is sent on anybody');
  });
});
