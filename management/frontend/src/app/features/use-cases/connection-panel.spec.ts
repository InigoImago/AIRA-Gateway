import { vi } from 'vitest';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Capability, CatalogModel, KiraModel } from '../../core/api/models';
import { ClientKind, ConnectionPanel } from './connection-panel';

/**
 * The connection block builds its addresses and examples from what the use case actually has.
 *
 * The property under test is that it only ever offers what would work: every address names this
 * use case, as every model call must (`FRD-149`); never a model the use case may not call, a KIRA
 * id for a model that has none, or an example before the gateway answered.
 */
describe('ConnectionPanel', () => {
  let fixture: ComponentFixture<ConnectionPanel>;

  const KIRA_MODELS: KiraModel[] = [
    { id: 9001, name: 'chat-model', capabilities: ['Chat'] },
    { id: 9002, name: 'embed-model', capabilities: ['Embedding'] },
    { id: 9003, name: 'not-released', capabilities: ['Chat'] },
  ];

  function build(released: string[]): void {
    fixture = TestBed.createComponent(ConnectionPanel);
    fixture.componentRef.setInput('slug', 'demo-uc');
    fixture.componentRef.setInput('released', released);
    fixture.detectChanges();
  }

  /**
   * The page has answered — which is an input now, because the page loads and the panel renders.
   * The catalogue says the same as the listing unless a test says otherwise: the two disagreeing is
   * its own case below.
   */
  function answer(models: KiraModel[] = KIRA_MODELS, extra: CatalogModel[] = []): void {
    fixture.componentRef.setInput('kira', models);
    declare([
      ...models.map((model) => ({
        name: model.name,
        capabilities: [
          ...(model.capabilities.includes('Chat') ? (['generate'] as const) : []),
          ...(model.capabilities.includes('Embedding') ? (['embed'] as const) : []),
          ...(model.capabilities.includes('Reranker') ? (['rerank'] as const) : []),
        ],
      })),
      ...extra,
    ]);
  }

  /** What the model catalogue declares — the source of every example but the KIRA ones. */
  function declare(catalog: CatalogModel[] | null): void {
    fixture.componentRef.setInput('catalog', catalog);
    fixture.detectChanges();
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function testid(id: string): HTMLElement | null {
    return (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="${id}"]`);
  }

  /** Switch client. The clients are tabs, so only one is in the DOM at a time. */
  function open(client: ClientKind): void {
    testid(`conn-tab-${client}`)?.click();
    fixture.detectChanges();
  }

  const model = (name: string, ...capabilities: Capability[]): CatalogModel => ({
    name,
    capabilities,
  });

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  // ---- every address names the use case (`FRD-149`) ----------------------------------

  it('gives every interface an address that names this use case', () => {
    build(['chat-model']);
    answer([...KIRA_MODELS, { id: 3001, name: 'ranker', capabilities: ['Reranker'] }]);
    fixture.componentRef.setInput('released', ['chat-model', 'ranker']);
    fixture.detectChanges();

    expect(testid('connection-gemini-base')?.textContent?.trim()).toMatch(
      /\/gw\/uc\/demo-uc\/v1beta$/,
    );
    expect(testid('connection-kira-base')?.textContent?.trim()).toMatch(
      /\/gw\/uc\/demo-uc\/kira\/api\/external$/,
    );
    expect(testid('connection-rank-url')?.textContent).toContain('/gw/uc/demo-uc/v1/projects/');
  });

  it('builds every example from an address that names this use case', () => {
    // An example that names none is refused when pasted, whichever credential it is sent with.
    build(['chat-model', 'embed-model', 'speaker', 'ranker']);
    answer(
      [...KIRA_MODELS, { id: 3001, name: 'ranker', capabilities: ['Reranker'] }],
      [model('speaker', 'speech'), model('tool-model', 'generate', 'tools')],
    );

    const examples: string[] = [];
    for (const client of ['curl', 'python', 'typescript', 'kira'] as const) {
      open(client);
      examples.push(
        ...[...(fixture.nativeElement as HTMLElement).querySelectorAll('pre.code-block')]
          .map((node) => node.textContent ?? '')
          .filter((example) => example.includes('/gw')),
      );
    }
    expect(examples.length).toBeGreaterThan(8);
    for (const example of examples) {
      expect(example, example).toContain('/gw/uc/demo-uc');
    }
  });

  it('says in the summary that every call names the use case, and where to send it', () => {
    // The question somebody opens it with is "where do I send this": the summary answers it, with
    // the address, before anything is opened.
    build(['chat-model']);
    answer();

    const summary = (testid('connection') as HTMLElement | null)?.querySelector('summary');
    expect(summary?.textContent).toContain('Every model call names this use case');
    expect(summary?.textContent).toContain('/gw/uc/demo-uc/v1beta');
    // The claim this panel made before a key had to name its use case must not come back.
    expect(summary?.textContent).not.toContain('no use case in the URL');
    expect(text()).not.toContain('issued for one use case and carries it');
  });

  it('explains the prefix and the header, and that naming grants nothing', () => {
    build(['chat-model']);
    answer();

    const section = testid('connection-attribution')?.textContent ?? '';
    expect(section).toContain('Every model call names its use case');
    expect(section).toContain('a key too');
    expect(section).toContain('/uc/demo-uc');
    expect(section).toContain('400');
    expect(section).toContain('403');
    expect(testid('connection-header-example')?.textContent).toContain('X-AIRA-Use-Case: demo-uc');
  });

  // ---- who is calling ------------------------------------------------------------------

  // Every example asks for `<your key>`, so the block names the tab where keys are issued — in
  // words, with no second route to it (see 'offers no key-issuing control of its own').
  it('says where a key comes from, since every example asks for one', () => {
    build(['chat-model']);
    answer();

    const credentials = testid('connection-credentials');
    expect(credentials).not.toBeNull();
    expect(credentials?.textContent).toContain('API keys');
    expect(text()).toContain('x-goog-api-key');
  });

  // Credentials are common to every interface — except the query parameter, which only the Gemini
  // API reads. Said where the three forms are listed, or a KIRA client copies the third and is
  // refused.
  it('says the key in a URL is the Gemini API’s alone', () => {
    build(['chat-model']);
    answer();

    const note = testid('connection-key-url')?.textContent ?? '';
    expect(note).toContain('the Gemini API reads it');
    expect(note).toContain('KIRA API reads the two headers alone');
    expect(testid('connection-key-forms')?.textContent).toContain('?key=');
  });

  // A bearer token is how a group grant reaches the data plane, and it carries an identity, not a
  // use case — which is why the address names one.
  it('covers the Keycloak bearer, and says what it does not carry', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-bearer-example')?.textContent).toContain('Authorization: Bearer');
    expect(testid('connection-credentials')?.textContent).toContain('carries no use case');
  });

  // Configuration reaches the gateway over the event bus, so a fresh grant is answered 403 for a
  // few seconds while the console already shows it.
  it('warns that a fresh grant is not effective at the gateway instantly', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-grant-delay')?.textContent).toContain('403');
  });

  // A removed control has no other test: without this one, the next reader adds it back.
  it('offers no key-issuing control of its own', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-issue-key')).toBeNull();
    expect(text()).not.toContain('Issue a key');
  });

  // ---- the models -----------------------------------------------------------------------

  it('offers only the models this use case may call', () => {
    build(['chat-model', 'embed-model']);
    answer();

    const table = testid('connection-models')?.textContent ?? '';
    expect(table).toContain('chat-model');
    expect(table).toContain('embed-model');
    // The gateway serves it and this use case may not call it. An example naming it is an example
    // that fails when pasted, and the reader debugs their client rather than the release.
    expect(table).not.toContain('not-released');
  });

  it('says how each interface addresses a model, which is the thing that differs', () => {
    build(['chat-model']);
    answer();

    const addressing = testid('connection-addressing')?.textContent ?? '';
    expect(addressing).toContain('by name');
    expect(addressing).toContain('by number');
  });

  it('shows an id as unknown rather than absent while the gateway has not answered', () => {
    build(['chat-model']);
    // No `answer()`: `null` is "we have not looked" and `[]` is "there is none", and the two must
    // not render the same — otherwise a slow gateway looks exactly like a model with no id.
    expect(testid('connection-models')?.textContent).not.toContain('none assigned');
    expect(testid('connection-kira-id')?.textContent).toContain('…');
  });

  it('says a model without a KIRA number has none, rather than that it is unserved', () => {
    build(['chat-model']);
    answer(
      [{ id: 9001, name: 'somebody-else', capabilities: ['Chat'] }],
      [model('chat-model', 'generate')],
    );

    expect(testid('connection-kira-id')?.textContent).toContain('none assigned');
    // No KIRA example: nothing here can address that model by number.
    open('kira');
    expect(testid('connection-kira-chat')).toBeNull();
    expect(testid('connection-no-kira-chat')).not.toBeNull();
  });

  it('says a released model the catalogue does not hold is undeclared, not that it is unserved', () => {
    build(['chat-model', 'local-model']);
    answer();

    expect(testid('connection-models')?.textContent).toContain('not declared in the catalog');
  });

  it('names everything a model serves, from the catalogue', () => {
    build(['both-model', 'speaker', 'ranker']);
    answer(
      [{ id: 9005, name: 'both-model', capabilities: ['Chat', 'Embedding'] }],
      [model('speaker', 'speech'), model('ranker', 'rerank')],
    );

    const table = testid('connection-models')?.textContent ?? '';
    expect(table).toContain('generation, embeddings');
    expect(table).toContain('speech');
    expect(table).toContain('ranking');
  });

  // ---- a request per client --------------------------------------------------------------

  it('shows one client at a time, and says which one is open', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-gemini-chat')).not.toBeNull();
    expect(testid('connection-kira-chat')).toBeNull();
    expect(testid('conn-tab-curl')?.getAttribute('aria-selected')).toBe('true');

    open('kira');

    expect(testid('connection-kira-chat')).not.toBeNull();
    expect(testid('connection-gemini-chat')).toBeNull();
    expect(testid('conn-tab-kira')?.getAttribute('aria-selected')).toBe('true');
  });

  it('gives curl a generation, a stream, an embedding and the budget, from models that do each', () => {
    build(['chat-model', 'embed-model']);
    answer();

    expect(testid('connection-gemini-chat')?.textContent).toContain(
      '/models/chat-model:generateContent',
    );
    expect(testid('connection-gemini-stream')?.textContent).toContain(
      '/models/chat-model:streamGenerateContent?alt=sse',
    );
    // From a model that embeds, not from the first one released.
    expect(testid('connection-gemini-embed')?.textContent).toContain(
      '/models/embed-model:embedContent',
    );
    expect(testid('connection-budget-check')?.textContent).toContain('/v1beta/me/budget');
  });

  it('offers speech and ranking only where a released model declares them', () => {
    build(['chat-model']);
    answer();
    expect(testid('connection-gemini-speech')).toBeNull();
    expect(testid('connection-gemini-rank')).toBeNull();
    expect(testid('connection-rank-url')).toBeNull();

    build(['chat-model', 'speaker', 'ranker']);
    answer(KIRA_MODELS, [model('speaker', 'speech'), model('ranker', 'rerank')]);
    expect(testid('connection-gemini-speech')?.textContent).toContain(
      '/models/speaker:generateContent',
    );
    expect(testid('connection-gemini-speech')?.textContent).toContain(
      '"responseModalities":["AUDIO"]',
    );
    expect(testid('connection-gemini-rank')?.textContent).toContain('"model":"ranker"');
  });

  it('gives Python the SDK the use case is verified with, its base URL naming the use case', () => {
    build(['chat-model', 'embed-model']);
    answer();
    open('python');

    const python = testid('connection-python')?.textContent ?? '';
    expect(python).toContain('from google import genai');
    // google-genai adds `/v1beta` itself, so its base is the use case's address without it.
    expect(python).toMatch(/base_url="[^"]*\/gw\/uc\/demo-uc", api_version="v1beta"/);
    expect(python).toContain('model="chat-model"');
    expect(python).toContain('embed_content(model="embed-model"');
  });

  it('gives TypeScript the AI SDK, whose base URL carries /v1beta', () => {
    build(['chat-model']);
    answer();
    open('typescript');

    const typescript = testid('connection-typescript')?.textContent ?? '';
    expect(typescript).toContain("from '@ai-sdk/google'");
    expect(typescript).toMatch(/baseURL: '[^']*\/gw\/uc\/demo-uc\/v1beta'/);
    expect(typescript).toContain("aira('chat-model')");
  });

  it('points OpenCode at the configuration the API keys tab writes, with the models it can use', () => {
    build(['chat-model', 'tool-model']);
    answer(KIRA_MODELS, [model('tool-model', 'generate', 'tools')]);
    open('opencode');

    expect(testid('connection-opencode-where')?.textContent).toContain('API keys tab');
    const config = JSON.parse(testid('connection-opencode')?.textContent ?? '{}');
    expect(config.provider.aira.options.baseURL).toMatch(/\/gw\/uc\/demo-uc\/v1beta$/);
    // Only a model that calls tools: an assistant cannot work with one that answers in prose.
    expect(Object.keys(config.provider.aira.models)).toEqual(['tool-model']);
    expect(config.model).toBe('aira/tool-model');
  });

  it('says when the use case has tool calling switched off, and only from an answer', () => {
    // OpenCode's first turn declares tools, and a use case that carries none refuses it by name:
    // the file would be handed out working and the client would fail at once.
    build(['tool-model']);
    answer(KIRA_MODELS, [model('tool-model', 'generate', 'tools')]);
    open('opencode');
    expect(testid('connection-tools-off')).toBeNull();

    fixture.componentRef.setInput('toolsEnabled', false);
    fixture.detectChanges();
    expect(testid('connection-tools-off')?.textContent).toContain('Capabilities');

    fixture.componentRef.setInput('toolsEnabled', true);
    fixture.detectChanges();
    expect(testid('connection-tools-off')).toBeNull();
  });

  it('says so when nothing released can serve OpenCode', () => {
    build(['chat-model']);
    answer();
    open('opencode');

    expect(testid('connection-opencode')).toBeNull();
    expect(testid('connection-no-tools')?.textContent).toContain('calling tools');
  });

  it('carries the KIRA id into the KIRA examples, reranking included', () => {
    build(['chat-model', 'embed-model', 'ranker']);
    answer([...KIRA_MODELS, { id: 3001, name: 'ranker', capabilities: ['Reranker'] }]);
    open('kira');

    // The one field a migrating client actually has to fill in, and the one nobody could see: it
    // lives in the catalog, which an administrator of a use case may not read.
    expect(testid('connection-kira-chat')?.textContent).toContain('"model_id":9001');
    expect(testid('connection-kira-embed')?.textContent).toContain('"model_id":9002');
    expect(testid('connection-kira-rerank')?.textContent).toContain('"model_id":3001');
  });

  // ---- what is not there ----------------------------------------------------------------

  it('says why it is empty when nothing is released', () => {
    build([]);
    answer();

    expect(testid('connection-nothing-released')?.textContent).toContain('every request would be');
    expect(testid('connection-gemini-chat')).toBeNull();
  });

  it('withholds a generation example when nothing released can generate', () => {
    build(['embed-model']);
    answer();

    expect(testid('connection-gemini-chat')).toBeNull();
    expect(testid('connection-no-chat')?.textContent).toContain('generating text');
  });

  it('takes the Gemini examples from the catalogue, so a failed KIRA listing costs only KIRA', () => {
    // The KIRA listing failed and the page passed `[]`: a released chat model must still have its
    // Gemini examples, which do not depend on that listing.
    build(['chat-model']);
    fixture.componentRef.setInput('kira', []);
    fixture.componentRef.setInput('failure', 'The gateway could not be asked for its model ids.');
    declare([model('chat-model', 'generate')]);

    expect(testid('connection-gemini-chat')?.textContent).toContain('chat-model:generateContent');
    expect(testid('connection-no-chat')).toBeNull();
    expect(testid('connection-failure')).not.toBeNull();
  });

  it('says nothing about what generates before the catalogue has answered', () => {
    build(['chat-model']);
    fixture.componentRef.setInput('kira', KIRA_MODELS);
    declare(null);

    expect(testid('connection-no-chat')).toBeNull();
    expect(testid('connection-gemini-chat')).toBeNull();
  });

  it('names the address as the console proxy rather than presenting it as the gateway', () => {
    build(['chat-model']);
    answer();

    // A URL that is right here and wrong everywhere else is worse than one that explains itself.
    expect(testid('connection-base-note')?.textContent).toContain('this console');
    expect(testid('connection-base-note')?.textContent).toContain('/uc/');
  });

  // ---- copying ----------------------------------------------------------------------------

  it('copies an example and says that it did', async () => {
    const written: string[] = [];
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: (text: string) => (written.push(text), Promise.resolve()) },
      configurable: true,
    });
    build(['chat-model']);
    answer();
    open('kira');

    testid('copy-kira-chat')?.click();
    await fixture.whenStable();
    fixture.detectChanges();

    // What was copied, not merely that something was. A button wired to the wrong example is a
    // button that works and hands the reader somebody else's command.
    expect(written[0]).toContain('"model_id":9001');
    expect(testid('copy-kira-chat')?.textContent).toContain('Copied');
  });

  it('says so when the browser refuses the clipboard, rather than claiming success', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.reject(new Error('denied')) },
      configurable: true,
    });
    build(['chat-model']);
    answer();

    testid('copy-gemini-chat')?.click();
    await fixture.whenStable();
    fixture.detectChanges();

    // A copy button that reports success on a refusal sends the reader to paste nothing.
    expect(testid('connection-copy-failure')).not.toBeNull();
    expect(testid('copy-gemini-chat')?.textContent).toContain('Copy');
    // And it is not the gateway being unreachable: two different things from two places.
    expect(testid('connection-failure')).toBeNull();
  });

  it('copies every address and every example it offers', async () => {
    const written: string[] = [];
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: (text: string) => (written.push(text), Promise.resolve()) },
      configurable: true,
    });
    build(['chat-model', 'embed-model', 'tool-model']);
    answer(KIRA_MODELS, [model('tool-model', 'generate', 'tools')]);

    // Every button against the text beside it: a copy control wired to the wrong string still
    // works and hands over somebody else's command.
    const pairs: [string, string][] = [
      ['copy-gemini-base', 'connection-gemini-base'],
      ['copy-kira-base', 'connection-kira-base'],
      ['copy-gemini-chat', 'connection-gemini-chat'],
      ['copy-gemini-stream', 'connection-gemini-stream'],
      ['copy-gemini-embed', 'connection-gemini-embed'],
      ['copy-budget-check', 'connection-budget-check'],
    ];
    const tabs: [ClientKind, string, string][] = [
      ['python', 'copy-python', 'connection-python'],
      ['typescript', 'copy-typescript', 'connection-typescript'],
      ['opencode', 'copy-opencode', 'connection-opencode'],
      ['kira', 'copy-kira-chat', 'connection-kira-chat'],
      ['kira', 'copy-kira-embed', 'connection-kira-embed'],
    ];
    for (const [button, shown] of pairs) {
      testid(button)?.click();
      await fixture.whenStable();
      expect(written.at(-1), button).toBe(testid(shown)?.textContent?.trim());
    }
    for (const [client, button, shown] of tabs) {
      open(client);
      testid(button)?.click();
      await fixture.whenStable();
      expect(written.at(-1), button).toBe(testid(shown)?.textContent?.trim());
    }
    expect(written).toHaveLength(pairs.length + tabs.length);
  });

  it('stops saying Copied after a moment, so the label describes the last press', async () => {
    vi.useFakeTimers();
    try {
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: () => Promise.resolve() },
        configurable: true,
      });
      build(['chat-model']);
      answer();

      testid('copy-gemini-chat')?.click();
      await vi.advanceTimersByTimeAsync(0);
      fixture.detectChanges();
      expect(testid('copy-gemini-chat')?.textContent).toContain('Copied');

      // A label stuck on "Copied" describes a press that happened a page-view ago, and the reader
      // cannot tell whether the one they just made worked.
      await vi.advanceTimersByTimeAsync(2500);
      fixture.detectChanges();
      expect(testid('copy-gemini-chat')?.textContent).toContain('Copy');
      expect(testid('copy-gemini-chat')?.textContent).not.toContain('Copied');
    } finally {
      vi.useRealTimers();
    }
  });

  // ---- folded shut -------------------------------------------------------------------
  // A reference, not a status: read once when a client is wired up, so it must not fill the overview.
  it('is a disclosure that starts shut', () => {
    build(['chat-model']);
    answer();

    const panel = testid('connection') as HTMLDetailsElement | null;
    expect(panel?.tagName).toBe('DETAILS');
    expect(panel?.open).toBe(false);
  });

  it('still has everything it had, once opened', () => {
    // A fold that quietly lost content would be a worse defect than the height it fixed.
    build(['chat-model']);
    answer();
    const panel = testid('connection') as HTMLDetailsElement;
    panel.open = true;
    fixture.detectChanges();

    for (const step of ['Where to send it', 'Who is calling', 'Which models', 'A request']) {
      expect(text()).toContain(step);
    }
  });
});
