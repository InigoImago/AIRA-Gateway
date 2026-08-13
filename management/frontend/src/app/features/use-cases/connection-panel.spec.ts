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

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
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

    // The one field a migrating client actually has to fill in, and the one nobody could see: it
    // lives in the catalog, which an administrator of a use case may not read.
    expect(testid('connection-kira-chat')?.textContent).toContain('"model_id":9001');
    expect(testid('connection-gemini-chat')?.textContent).toContain(
      '/models/chat-model:generateContent',
    );
  });

  it('does not claim a model is unserved when it may simply have no KIRA number', () => {
    build(['chat-model']);
    answer([{ id: 9001, name: 'somebody-else', capabilities: ['CHAT'] }]);

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

    // Every button, because a copy control wired to the wrong string is one that works and hands
    // the reader somebody else's command — and the only way to see that is to press each one.
    for (const id of [
      'copy-gemini-base',
      'copy-kira-base',
      'copy-gemini-chat',
      'copy-kira-chat',
      'copy-gemini-embed',
      'copy-kira-embed',
    ]) {
      testid(id)?.click();
      await fixture.whenStable();
    }
    fixture.detectChanges();

    expect(written).toHaveLength(6);
    expect(written[0]).toContain('/gw/v1beta');
    expect(written[1]).toContain('/gw/kira/api/external');
    expect(written[2]).toContain('chat-model:generateContent');
    expect(written[3]).toContain('"model_id":9001');
    expect(written[4]).toContain('embed-model:embedContent');
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
});
