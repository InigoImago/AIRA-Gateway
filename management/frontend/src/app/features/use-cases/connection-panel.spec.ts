import { vi } from 'vitest';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { KiraModel } from '../../core/api/models';
import { ConnectionPanel } from './connection-panel';

/**
 * The connection block builds its examples from what the use case actually has.
 *
 * The defect it fixes is an absence: the console said everything about *governing* a use case and
 * nothing about *using* one, so somebody who had just issued a key had no address to put it
 * against. An absent instruction announces itself through nothing — the reader concludes the
 * feature is unfinished, or that they missed a page.
 *
 * Which makes the property under test not "does it render" but **does it only ever offer what
 * would work**. Every case here is a way the block could be confidently wrong: a model the use case
 * may not call, a KIRA id for a model that has none, an example built while the gateway has not
 * answered yet.
 */
describe('ConnectionPanel', () => {
  let fixture: ComponentFixture<ConnectionPanel>;

  const KIRA_MODELS: KiraModel[] = [
    { id: 9001, name: 'chat-model', capabilities: ['CHAT'] },
    { id: 9002, name: 'embed-model', capabilities: ['EMBEDDING'] },
    { id: 9003, name: 'not-released', capabilities: ['CHAT'] },
  ];

  function build(released: string[]): void {
    fixture = TestBed.createComponent(ConnectionPanel);
    fixture.componentRef.setInput('slug', 'demo-uc');
    fixture.componentRef.setInput('released', released);
    fixture.detectChanges();
  }

  /** The page has answered — which is an input now, because the page loads and the panel renders. */
  function answer(models: KiraModel[] = KIRA_MODELS): void {
    fixture.componentRef.setInput('kira', models);
    fixture.detectChanges();
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function testid(id: string): HTMLElement | null {
    return (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="${id}"]`);
  }

  /**
   * Switch surface.
   *
   * The two are tabs now, so only one is in the DOM at a time — which is why the assertions below
   * changed rather than being added to. A test that still expected both at once would have been
   * asserting the old layout.
   */
  function open(surface: 'gemini' | 'kira'): void {
    testid(`conn-tab-${surface}`)?.click();
    fixture.detectChanges();
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  /**
   * There is no "Issue a key" button here, and that is the decision rather than an omission.
   *
   * It was added with the block and did nothing: a `routerLink` carrying `fragment="api-keys"`,
   * where the page selects its tab from a **query parameter** and calls it `keys` — and behind
   * both, the parent reads that parameter from the route *snapshot*, so navigating to the same
   * route with a different one changes the URL and moves nothing. Offered as "make it work or take
   * it out", the owner chose out: the panel's job is to say how to call the use case, and the tab
   * bar two centimetres above already leads to where keys are issued. A second route to the same
   * place is a second thing that can rot.
   *
   * Asserted because a **removal has no other counterpart**. Nothing fails when a control comes
   * back, so without this the next person to read "connecting a client, but where do I get a key?"
   * adds one, and the answer to that question is the tab bar.
   */
  /**
   * Every example said `<your key>` and nothing said where one comes from — an instruction with no
   * destination, and the gap left behind when the "Issue a key" button was removed. Naming the tab
   * is the answer, because that is where keys are issued and a second route to it is a second
   * thing that can rot.
   */
  it('says where a key comes from, since every example asks for one', () => {
    build(['chat-model']);
    answer();

    const credentials = testid('connection-credentials');
    expect(credentials).not.toBeNull();
    expect(credentials?.textContent).toContain('API keys');
    expect(text()).toContain('x-goog-api-key');
  });

  /**
   * Bearer tokens were absent outright, and they are not an edge case: they are how a person or a
   * service account calls the gateway with no key minted, and the only way a Keycloak group grant
   * reaches the data plane. Measured on the running stack before it was written here.
   *
   * The sentence that matters is the *difference* — a token carries an identity and **not** a use
   * case — because it is what sends a reader to the section below instead of to a 403 they cannot
   * explain.
   */
  it('covers the OIDC bearer, and says what it does not carry', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-bearer-example')?.textContent).toContain('Authorization: Bearer');
    expect(testid('connection-credentials')?.textContent).toContain('carries no use');
  });

  /**
   * The measured number. Configuration reaches the gateway over the event bus, so a grant is
   * effective in this console immediately and at the gateway a few seconds later — during which a
   * caller the page shows as a member is answered 403. Written down because the alternative is
   * somebody concluding the grant did not work.
   */
  it('warns that a fresh grant is not effective at the gateway instantly', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-grant-delay')?.textContent).toContain('403');
  });

  it('offers no key-issuing control of its own', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-issue-key')).toBeNull();
    expect(text()).not.toContain('Issue a key');
  });

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

  it('carries the KIRA id into the compatibility example', () => {
    build(['chat-model']);
    answer();

    // Gemini first, because that is the tab a reader lands on.
    expect(testid('connection-gemini-chat')?.textContent).toContain(
      '/models/chat-model:generateContent',
    );

    open('kira');
    // The one field a migrating client actually has to fill in, and the one nobody could see: it
    // lives in the catalog, which an administrator of a use case may not read.
    expect(testid('connection-kira-chat')?.textContent).toContain('"model_id":9001');
  });

  it('shows one surface at a time, and says which one is open', () => {
    build(['chat-model']);
    answer();

    // The point of the split: a KIRA client will never send a model name and a Gemini client will
    // never send an id, so showing both at once made every reader discard half of what they saw.
    expect(testid('connection-gemini-base')).not.toBeNull();
    expect(testid('connection-kira-base')).toBeNull();
    expect(testid('conn-tab-gemini')?.getAttribute('aria-selected')).toBe('true');

    open('kira');

    expect(testid('connection-kira-base')).not.toBeNull();
    expect(testid('connection-gemini-base')).toBeNull();
    expect(testid('conn-tab-kira')?.getAttribute('aria-selected')).toBe('true');
  });

  it('says how each surface addresses a model, which is the thing that differs', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-gemini-addressing')?.textContent).toContain('by name');
    open('kira');
    expect(testid('connection-kira-addressing')?.textContent).toContain('integer id');
  });

  it('does not claim a model is unserved when it may simply have no KIRA number', () => {
    build(['chat-model']);
    answer([{ id: 9001, name: 'somebody-else', capabilities: ['CHAT'] }]);
    open('kira');

    // The gateway omits a model from its KIRA listing when the catalog gave it no id, so an absent
    // entry is *either* no id *or* not served. This said "the gateway does not serve this model" —
    // a confident answer to a question the panel cannot answer, and wrong for the reader whose
    // model works fine over the Gemini API and has no number yet.
    expect(testid('connection-models')?.textContent).toContain("not in the gateway's KIRA listing");
    expect(testid('connection-models')?.textContent).not.toContain('does not serve');
    // No example either way: nothing here knows what that model can do.
    expect(testid('connection-kira-chat')).toBeNull();
  });

  it('builds the embedding example from a model that embeds, not from the first one released', () => {
    build(['chat-model', 'embed-model']);
    answer();

    expect(testid('connection-gemini-embed')?.textContent).toContain(
      '/models/embed-model:embedContent',
    );
    open('kira');
    expect(testid('connection-kira-embed')?.textContent).toContain('"model_id":9002');
  });

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
    expect(testid('connection-no-chat')?.textContent).toContain('generates text');
  });

  it('reports a gateway that cannot be asked instead of showing every id as absent', () => {
    build(['chat-model']);
    fixture.componentRef.setInput('kira', []);
    fixture.componentRef.setInput('failure', 'The gateway could not be asked for its model ids.');
    fixture.detectChanges();

    // Swallowed, this reads as a deployment where no model has a number — which is a real state
    // and a different one. The two must not look alike.
    expect(testid('connection-failure')).not.toBeNull();
  });

  it('names the base URL as the console proxy rather than presenting it as the gateway', () => {
    build(['chat-model']);
    answer();

    expect(testid('connection-gemini-base')?.textContent).toContain('/gw/v1beta');
    open('kira');
    expect(testid('connection-kira-base')?.textContent).toContain('/gw/kira/api/external');
    // A URL that is right here and wrong everywhere else is worse than one that explains itself.
    expect(testid('connection-base-note')?.textContent).toContain('this console');
  });

  it('shows the id as unknown rather than absent while the page has not answered', () => {
    build(['chat-model']);
    // No `answer()`: `null` is "we have not looked" and `[]` is "there is none", and the two must
    // not render the same — otherwise a slow gateway looks exactly like a model with no id.
    expect(fixture.nativeElement.textContent).not.toContain('Gemini API only');
  });

  it('says the use case comes from the key rather than from the address', () => {
    build(['chat-model']);
    answer();

    // The question that prompted this panel. An example with no explanation sends a caller looking
    // for a per-use-case URL that does not exist.
    expect(text()).toContain('issued for one use case');
  });
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
  });

  it('keeps a clipboard refusal apart from the gateway being unreachable', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.reject(new Error('denied')) },
      configurable: true,
    });
    build(['chat-model']);
    answer();

    testid('copy-gemini-chat')?.click();
    await fixture.whenStable();
    fixture.detectChanges();

    // Two different things arriving from two different places. Folding one into the other would
    // have a browser permission read as a broken gateway.
    expect(testid('connection-failure')).toBeNull();
  });

  it('puts a model that both generates and embeds into both examples', () => {
    build(['both-model']);
    answer([{ id: 9005, name: 'both-model', capabilities: ['CHAT', 'EMBEDDING'] }]);

    expect(testid('connection-gemini-chat')?.textContent).toContain('both-model:generateContent');
    expect(testid('connection-gemini-embed')?.textContent).toContain('both-model:embedContent');
    expect(testid('connection-models')?.textContent).toContain('generation and embeddings');
  });

  it('gives no KIRA embedding example for an embedding model without an id', () => {
    build(['embed-model']);
    answer([{ id: 0, name: 'other', capabilities: ['CHAT'] }]);
    open('kira');

    // The embedding model is released and the gateway knows nothing about it, so neither example
    // can be built — and the table says which of the two is missing rather than showing a blank.
    expect(testid('connection-kira-embed')).toBeNull();
    expect(testid('connection-models')?.textContent).toContain("not in the gateway's KIRA listing");
  });
  it('copies every example and every base URL it offers', async () => {
    const written: string[] = [];
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: (text: string) => (written.push(text), Promise.resolve()) },
      configurable: true,
    });
    build(['chat-model', 'embed-model']);
    answer();

    // Every button on both tabs, because a copy control wired to the wrong string is one that
    // works and hands the reader somebody else's command — and the only way to see that is to
    // press each one. Walked per surface now that they are tabs; a loop over all six against one
    // tab would have found three of them absent and pressed nothing.
    for (const surface of ['gemini', 'kira'] as const) {
      open(surface);
      for (const id of [`copy-${surface}-base`, `copy-${surface}-chat`, `copy-${surface}-embed`]) {
        testid(id)?.click();
        await fixture.whenStable();
      }
    }
    fixture.detectChanges();

    expect(written).toHaveLength(6);
    expect(written[0]).toContain('/gw/v1beta');
    expect(written[1]).toContain('chat-model:generateContent');
    expect(written[2]).toContain('embed-model:embedContent');
    expect(written[3]).toContain('/gw/kira/api/external');
    expect(written[4]).toContain('"model_id":9001');
    expect(written[5]).toContain('"model_id":9002');
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
  it('explains the path prefix, and builds it from the same base as the examples', () => {
    build(['chat-model']);
    answer();

    // The prefix had no explanation at all — it was half a sentence inside a hover hint, which is
    // where somebody who already suspects there is something to know goes looking. A caller with a
    // client that can set a URL and not a header does not suspect it.
    const section = testid('connection-attribution')?.textContent ?? '';
    expect(section).toContain('/uc/');
    expect(section).toContain('403');
    expect(section).toContain('header');

    // Built, not written: the whole claim about the prefix is that everything after it is the URL
    // shown above, so the two must come from one place.
    expect(testid('connection-uc-example')?.textContent).toContain('/uc/demo-uc/v1beta');
    expect(testid('connection-header-example')?.textContent).toContain('X-AIRA-Use-Case: demo-uc');
  });

  it('shows the path prefix for whichever surface is open', () => {
    build(['chat-model']);
    answer();
    expect(testid('connection-uc-example')?.textContent).toContain('/uc/demo-uc/v1beta');

    open('kira');

    // A prefix example that kept naming the other surface would be the one thing on this panel a
    // reader is most likely to paste unread.
    expect(testid('connection-uc-example')?.textContent).toContain('/uc/demo-uc/kira/api/external');
  });
});
