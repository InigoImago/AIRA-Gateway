import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { CatalogModel, FallbackChains, FallbackKind } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { FallbackChainsCard } from './fallback-chains-card';
import { canServe, chainFor } from './fallback-kinds';

/**
 * The installation's fallback chains, one per kind of request (`FRD-310`), as the Models page shows
 * them: the default is none — the error — and an embedding never falls back to a model that writes
 * text, because only a model that can serve a kind is offered for it.
 */

const CATALOGUE: CatalogModel[] = [
  { name: 'gen-1', approved: true, capabilities: ['generate'] },
  { name: 'gen-2', approved: true, capabilities: ['generate', 'tools'] },
  { name: 'speech-1', approved: true, capabilities: ['speech'] },
  { name: 'emb-1', approved: true, capabilities: ['embed'] },
  { name: 'rank-1', approved: true, capabilities: ['rerank'] },
  { name: 'withheld-1', approved: false, capabilities: ['generate'] },
  { name: 'bare-1', approved: true },
];

const NONE: FallbackChains = { generate: [], embed: [], rerank: [] };

function setup(
  options: {
    chains?: Observable<FallbackChains>;
    canEdit?: boolean;
    save?: Observable<FallbackChains>;
  } = {},
) {
  const saved: { kind: FallbackKind; models: string[] }[] = [];
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [FallbackChainsCard],
    providers: [
      PageFeedback,
      {
        provide: UseCaseService,
        useValue: {
          fallbackChains: () => options.chains ?? of(NONE),
          saveFallbackChain: (kind: FallbackKind, models: string[]) => {
            saved.push({ kind, models });
            return options.save ?? of({ ...NONE, [kind]: models });
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(FallbackChainsCard);
  fixture.componentRef.setInput('catalogued', CATALOGUE);
  fixture.componentRef.setInput('canEdit', options.canEdit ?? true);
  fixture.detectChanges();
  const html = fixture.nativeElement as HTMLElement;
  const component = fixture.componentInstance as unknown as {
    choices: (kind: FallbackKind) => { value: string; warning?: string }[];
    choose: (kind: FallbackKind, models: string[]) => void;
    save: (kind: FallbackKind) => void;
  };
  return {
    fixture,
    html,
    component,
    saved,
    feedback: TestBed.inject(PageFeedback),
    testid: (id: string) => html.querySelector(`[data-testid="${id}"]`),
  };
}

describe('FallbackChainsCard', () => {
  it('says that no chain is set, which means the error, for every kind', () => {
    const { testid } = setup();

    expect(testid('fallback-chains-summary')?.textContent).toContain(
      'No chain is set: a model that cannot answer is an error',
    );
    for (const kind of ['generate', 'embed', 'rerank']) {
      expect(testid(`fallback-${kind}`)).not.toBeNull();
    }
  });

  it('offers for each kind only the approved models that can serve it', () => {
    const { component } = setup();
    const values = (kind: FallbackKind) => component.choices(kind).map((choice) => choice.value);

    // Speech counts as generation; an undeclared model has the baseline, generation and embedding.
    expect(values('generate')).toEqual(['gen-1', 'gen-2', 'speech-1', 'bare-1']);
    expect(values('embed')).toEqual(['emb-1', 'bare-1']);
    expect(values('rerank')).toEqual(['rank-1']);
  });

  it('keeps a stored model that can no longer serve, marked so it can be taken out', () => {
    const { component } = setup({ chains: of({ ...NONE, embed: ['gen-1', 'gone-1'] }) });

    const marked = component.choices('embed').filter((choice) => choice.warning);
    expect(marked).toEqual([
      { value: 'gen-1', label: 'gen-1', warning: 'cannot serve this kind — skipped' },
      { value: 'gone-1', label: 'gone-1', warning: 'not in the catalogue' },
    ]);
  });

  it('saves one kind in the order chosen, and says what it now means', () => {
    const { component, saved, feedback, fixture, testid } = setup();

    component.choose('generate', ['gen-2', 'gen-1']);
    fixture.detectChanges();
    (testid('fallback-save-generate') as HTMLButtonElement).click();
    fixture.detectChanges();

    expect(saved).toEqual([{ kind: 'generate', models: ['gen-2', 'gen-1'] }]);
    expect(feedback.notice()).toBe('Text generation falls back to gen-2, gen-1, in that order.');
    expect(testid('fallback-chains-summary')?.textContent).toContain('1 of 3 kinds');
  });

  it('says an emptied chain means the error again', () => {
    const { component, feedback } = setup({ chains: of({ ...NONE, rerank: ['rank-1'] }) });

    component.choose('rerank', []);
    component.save('rerank');

    expect(feedback.notice()).toBe(
      'Ranking has no fallback: a model that cannot answer is an error.',
    );
  });

  it('offers Save only for a chain that changed', () => {
    const { component, fixture, testid } = setup();
    const button = () => testid('fallback-save-embed') as HTMLButtonElement;

    expect(button().disabled).toBe(true);
    component.choose('embed', ['emb-1']);
    fixture.detectChanges();
    expect(button().disabled).toBe(false);
  });

  it("warns beside the embedding chain that two models' vectors are not comparable", () => {
    const { testid } = setup();

    expect(testid('fallback-embed-warning')?.textContent).toContain(
      'Vectors from two different models are not comparable',
    );
  });

  it('shows the chains to a reader who may not change them, without a Save', () => {
    const { testid } = setup({ canEdit: false, chains: of({ ...NONE, generate: ['gen-1'] }) });

    expect(testid('fallback-save-generate')).toBeNull();
    expect(testid('fallback-chains-summary')?.textContent).toContain('1 of 3 kinds');
  });

  it('reports a failed save through the page and keeps the chain as it was', () => {
    const { component, feedback, saved } = setup({
      save: throwError(() => ({
        status: 400,
        error: {
          error: {
            code: 'invalid',
            message: "'gen-1' does not declare embedding, so it cannot answer such a request.",
          },
        },
      })),
    });

    component.choose('embed', ['gen-1']);
    component.save('embed');

    expect(saved).toHaveLength(1);
    expect(feedback.error()).toContain('does not declare embedding');
  });

  it('says where the chains could not be loaded, and loads them again on request', () => {
    let calls = 0;
    const chains = new Observable<FallbackChains>((subscriber) => {
      calls += 1;
      if (calls === 1) subscriber.error({ status: 503 });
      else subscriber.next(NONE);
    });
    const { testid, fixture } = setup({ chains });

    expect(testid('fallback-chains-error')).not.toBeNull();
    (testid('fallback-chains-error')?.querySelector('button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(testid('fallback-chains-error')).toBeNull();
    expect(testid('fallback-generate')).not.toBeNull();
  });
});

describe('the console reads the fallback rule as the server does', () => {
  it('lets a model serve a kind by its declaration, an undeclared one by the baseline', () => {
    expect(canServe({ name: 'x', capabilities: ['speech'] }, 'generate')).toBe(true);
    expect(canServe({ name: 'x', capabilities: ['embed'] }, 'generate')).toBe(false);
    expect(canServe({ name: 'x' }, 'embed')).toBe(true);
    expect(canServe({ name: 'x' }, 'rerank')).toBe(false);
  });

  it('works out a chain as `aira_common.fallback.chain_for` does', () => {
    expect(chainFor('generate', 'none', ['own'], ['inst'])).toEqual([]);
    expect(chainFor('generate', 'own', ['own'], ['inst'])).toEqual(['own']);
    expect(chainFor('embed', 'own', ['own'], ['inst'])).toEqual(['inst']);
    expect(chainFor('rerank', 'default', [], ['inst'])).toEqual(['inst']);
  });
});
