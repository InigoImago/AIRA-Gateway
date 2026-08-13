import { Component, computed, input, signal } from '@angular/core';
import { KiraModel } from '../../core/api/models';
import { InfoHint } from '../../core/ui/info-hint';

/**
 * How to actually call this use case — the half the console never said.
 *
 * Everything else on this page is about *governing* a use case: who reaches it, what it may spend,
 * which models it may call, what is kept and for how long. Nothing anywhere said where to send a
 * request. Somebody issued a key, had it shown once, and had no address to put it against — which
 * is `FRD-206`'s complaint from the other side. A control that refuses when used announces itself;
 * a missing instruction never does, and the reader concludes the feature is unfinished or that
 * they have missed a page.
 *
 * **Everything here is derived, and that is the point.** The base URL is the one this console
 * demonstrably reaches the gateway at — the reader's own browser proves it on every other panel —
 * rather than a value written down somewhere that goes stale on the first deployment that moves.
 * The models are this use case's **release** (`FRD-308`) intersected with what the gateway serves,
 * so the block cannot offer a model that would be refused. The KIRA numbers come from the gateway,
 * because that is the field a migrating client has to fill in and it is the one nobody could see.
 *
 * The two things it refuses to invent:
 *
 * - **A model this use case may not call.** An example naming one is an example that fails when
 *   pasted, which is worse than none: the reader debugs their client.
 * - **A KIRA id for a model that has none.** Not every catalogued model carries a number, and a
 *   made-up one addresses somebody else's model or nothing at all.
 *
 * And one thing it deliberately does **not** claim. The gateway omits a model from its KIRA
 * listing when the catalog assigned it no number — *"without an id a KIRA client cannot address it,
 * and listing it would offer something that cannot be called"* — so a model missing from that
 * listing is either unserved **or** served without a number, and this panel cannot tell which. It
 * said "the gateway does not serve this model", which is a confident answer to a question it
 * cannot answer, and wrong for exactly the reader it is written for: somebody whose model works
 * fine over the Gemini API and has no KIRA id yet.
 */
interface Reachable {
  /** The name the Gemini surface addresses, which is the model's own name. */
  name: string;
  /** The integer the compatibility surface addresses, where one is assigned. */
  kiraId: number | null;
  /** Whether it generates, embeds, or both — decides which example it belongs in. */
  generates: boolean;
  embeds: boolean;
}

@Component({
  selector: 'app-connection-panel',
  imports: [InfoHint],
  templateUrl: './connection-panel.html',
})
export class ConnectionPanel {
  readonly slug = input.required<string>();
  /** The release, straight from the use case — the same list the release panel shows. */
  readonly released = input.required<string[]>();
  /**
   * The gateway's KIRA listing, or `null` while it has not answered.
   *
   * **An input, because the page loads and the panel renders** (`CLAUDE.md` §3). The first version
   * fetched it here, which broke every one of the parent's tests at once: a child that reaches for
   * a service the parent's harness does not provide makes the parent untestable, and a panel that
   * loads is a panel the page cannot say anything about. `consumption-panel` and
   * `model-release-panel` are the same shape for the same reason.
   *
   * `null` is not `[]`. One means the gateway has not answered and the other means it serves
   * nothing under a number — and the table says something different for each, because "we have not
   * looked" and "there is none" are the two answers this repository keeps insisting are distinct.
   */
  readonly kira = input<KiraModel[] | null>(null);
  /** Why the listing could not be read, if it could not. Empty otherwise. */
  readonly failure = input('');

  /**
   * Which surface the reader is looking at.
   *
   * **Tabs rather than one stacked list**, because the two are alternatives and not steps: a
   * migrating KIRA client will never send a model *name*, and a new Gemini client will never send
   * an *id*. Stacked, every reader had to read both and work out which half applied — and the
   * addressing, which is the one thing that genuinely differs, was the easiest thing to take from
   * the wrong column.
   */
  protected readonly surface = signal<'gemini' | 'kira'>('gemini');

  protected readonly copied = signal('');
  /**
   * The panel's **own** failure, which is not the page's.
   *
   * `failure` above is why the gateway could not be asked; this is why the clipboard refused. They
   * arrive from different places and mean different things, and putting the second into the first
   * would have a browser permission read as a broken gateway.
   */
  protected readonly copyFailure = signal('');

  /**
   * Where this console reaches the gateway.
   *
   * `/gw` is the console's own proxy and forwards the whole gateway, not only the paths this SPA
   * uses — so it is a working base URL for any client that can reach the console, and the reader
   * can verify it without being told to trust anything. It is deliberately **not** presented as
   * *the* gateway address: a client on another machine uses whatever address the operator
   * publishes, and this console has no way to know that. Saying so is cheaper than printing a URL
   * that is right here and wrong everywhere else.
   */
  protected readonly base = computed(() => `${window.location.origin}/gw`);
  protected readonly geminiBase = computed(() => `${this.base()}/v1beta`);
  protected readonly kiraBase = computed(() => `${this.base()}/kira/api/external`);

  /** The models this use case can actually call, with what each surface needs to address them. */
  protected readonly reachable = computed<Reachable[]>(() => {
    const listing = this.kira();
    const byName = new Map((listing ?? []).map((model) => [model.name, model]));
    return this.released().map((name) => {
      const entry = byName.get(name);
      const capabilities = entry?.capabilities ?? [];
      return {
        name,
        // `?? null` rather than `?? 0`: a model without a number has none, and zero is a number.
        kiraId: entry?.id ?? null,
        generates: capabilities.includes('CHAT'),
        embeds: capabilities.includes('EMBEDDING'),
      };
    });
  });

  protected readonly chatModel = computed(() => this.reachable().find((m) => m.generates) ?? null);
  protected readonly embedModel = computed(() => this.reachable().find((m) => m.embeds) ?? null);

  /** True once the gateway has answered, whatever it said — so "no ids" is not shown while loading. */
  protected readonly answered = computed(() => this.kira() !== null);

  /**
   * The four builders take the model rather than looking it up and guarding.
   *
   * They each began with `const model = …; if (!model) return '';` — and every one of those guards
   * was unreachable: the template only calls them inside the `@if` that produced the model. Four
   * branches nothing could take, which is a rule the code claims and does not have. Passing the
   * model makes the impossibility structural: there is no absent case to check, because the type
   * says there is none.
   */
  protected geminiChat(model: Reachable): string {
    return [
      `curl -s ${this.geminiBase()}/models/${model.name}:generateContent \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"contents":[{"parts":[{"text":"Say OK."}]}],`,
      `       "generationConfig":{"maxOutputTokens":64}}'`,
    ].join('\n');
  }

  protected kiraChat(model: Reachable): string {
    return [
      `curl -s ${this.kiraBase()}/chat \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"request":{"parts":[{"text":"Say OK."}]},`,
      `       "model_id":${model.kiraId},"maxTokens":64}'`,
    ].join('\n');
  }

  protected geminiEmbed(model: Reachable): string {
    return [
      `curl -s ${this.geminiBase()}/models/${model.name}:embedContent \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"content":{"parts":[{"text":"hello"}]}}'`,
    ].join('\n');
  }

  protected kiraEmbed(model: Reachable): string {
    return [
      `curl -s ${this.kiraBase()}/embed \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"text":"hello","model_id":${model.kiraId}}'`,
    ].join('\n');
  }

  protected selectSurface(surface: 'gemini' | 'kira'): void {
    this.surface.set(surface);
  }

  /**
   * The path selector, shown for whichever surface the reader is on.
   *
   * Built from the same base as the examples, because the whole point of the prefix is that
   * *everything after it is unchanged* — writing it out by hand would be a second place for the
   * path to drift from the one two paragraphs above.
   */
  protected ucExample(): string {
    const base = this.base();
    const surface = this.surface() === 'gemini' ? '/v1beta' : '/kira/api/external';
    return `${base}/uc/${this.slug()}${surface}`;
  }

  /**
   * Copy, and say that it happened.
   *
   * A copy button that changes nothing visible is one people press twice and then distrust. The
   * clipboard can also be refused — an insecure origin, a permission — so the failure is shown
   * rather than swallowed into a success the reader would act on.
   */
  protected async copy(what: string, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.copied.set(what);
      setTimeout(() => this.copied.update((current) => (current === what ? '' : current)), 2000);
    } catch {
      this.copyFailure.set(
        'The browser refused to write to the clipboard; select and copy manually.',
      );
    }
  }
}
