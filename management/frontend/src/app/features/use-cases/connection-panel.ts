import { Component, computed, input, signal } from '@angular/core';
import { CatalogModel, KiraModel } from '../../core/api/models';
import { gatewayBase } from '../../core/api/prefixes';
import { copyText } from '../../core/ui/clipboard';
import { InfoHint } from '../../core/ui/info-hint';

/** A released model, with what each surface needs to address it. */
interface Reachable {
  /** The name the Gemini surface addresses, which is the model's own name. */
  name: string;
  /** The integer the compatibility surface addresses, where one is assigned. */
  kiraId: number | null;
  /** What the KIRA listing says it does — the KIRA surface's examples come from this. */
  kiraGenerates: boolean;
  kiraEmbeds: boolean;
  /**
   * What the catalogue declares it does — the Gemini surface's examples come from this, because
   * that surface addresses a model by name whether or not the KIRA listing carries it. `null` where
   * the catalogue has not answered or does not hold the model.
   */
  generates: boolean | null;
  embeds: boolean | null;
}

/**
 * How to call this use case: base URLs, addressing, credentials and examples (`FRD-206`).
 *
 * Everything is derived, so nothing goes stale: the base URL is the one this console reaches the
 * gateway at, and the models are the use case's release (`FRD-308`). What each one does comes from
 * where each surface learns it — the catalogue for the Gemini surface, the gateway's KIRA listing
 * for the KIRA surface, with its ids. It never invents a model the use case may not call, nor a
 * KIRA id for a model without one — an example that fails when pasted is worse than none. A model
 * missing from the KIRA listing is either unserved or served without a number; the panel cannot
 * tell which, so it claims neither, and the Gemini surface is not affected by it.
 */
@Component({
  selector: 'app-connection-panel',
  imports: [InfoHint],
  templateUrl: './connection-panel.html',
  styles: `
    /* The whole summary row is the click target: the marker alone is small and the only way in. */
    .connect__summary {
      cursor: pointer;
      list-style-position: outside;
      padding-left: 0.4rem;
    }
    .connect__summary::marker {
      color: var(--aira-text-muted);
    }
    .connect__hint {
      display: block;
      margin-top: 0.25rem;
      font-size: 0.9rem;
      font-weight: 400;
    }
    /* Only when open, so a shut card is exactly its summary. */
    .connect[open] .connect__body {
      margin-top: 0.5rem;
    }
  `,
})
export class ConnectionPanel {
  readonly slug = input.required<string>();
  /** The release, straight from the use case — the same list the release panel shows. */
  readonly released = input.required<string[]>();
  /** The model catalogue the page loaded, or `null` until it answers. */
  readonly catalog = input<CatalogModel[] | null>(null);
  /**
   * The gateway's KIRA listing, or `null` while it has not answered. An input, because the page
   * loads and the panel renders (`CLAUDE.md` §3).
   *
   * `null` is not `[]`: "not answered yet" and "serves nothing under a number" render differently.
   */
  readonly kira = input<KiraModel[] | null>(null);
  /** Why the listing could not be read, if it could not. Empty otherwise. */
  readonly failure = input('');

  /**
   * Which surface the reader is looking at. Tabs, because the two are alternatives: a KIRA client
   * never sends a model name and a Gemini client never sends an id.
   */
  protected readonly surface = signal<'gemini' | 'kira'>('gemini');

  protected readonly copied = signal('');
  /** The clipboard's refusal — kept apart from `failure`, so it never reads as a broken gateway. */
  protected readonly copyFailure = signal('');

  /**
   * Where this console reaches the gateway. `/gw` is the console's proxy and forwards the whole
   * gateway, so it works for any client that can reach the console; it is deliberately not
   * presented as *the* gateway address, which only the operator knows.
   */
  protected readonly base = computed(() => gatewayBase());
  protected readonly geminiBase = computed(() => `${this.base()}/v1beta`);
  protected readonly kiraBase = computed(() => `${this.base()}/kira/api/external`);

  /** The models this use case can actually call, with what each surface needs to address them. */
  protected readonly reachable = computed<Reachable[]>(() => {
    const listing = this.kira();
    const byName = new Map((listing ?? []).map((model) => [model.name, model]));
    const catalog = this.catalog();
    const declared = new Map((catalog ?? []).map((model) => [model.name, model]));
    return this.released().map((name) => {
      const entry = byName.get(name);
      const capabilities = entry?.capabilities ?? [];
      const own = declared.get(name)?.capabilities;
      return {
        name,
        // `?? null` rather than `?? 0`: a model without a number has none, and zero is a number.
        kiraId: entry?.id ?? null,
        kiraGenerates: capabilities.includes('Chat'),
        kiraEmbeds: capabilities.includes('Embedding'),
        generates: own ? own.includes('generate') : null,
        embeds: own ? own.includes('embed') : null,
      };
    });
  });

  /** The Gemini examples' models: declared able to do it in the catalogue. */
  protected readonly chatModel = computed(
    () => this.reachable().find((m) => m.generates === true) ?? null,
  );
  protected readonly embedModel = computed(
    () => this.reachable().find((m) => m.embeds === true) ?? null,
  );
  /** The KIRA examples' models: listed for it by the gateway, with a number to address it by. */
  protected readonly kiraChatModel = computed(
    () => this.reachable().find((m) => m.kiraGenerates && m.kiraId !== null) ?? null,
  );
  protected readonly kiraEmbedModel = computed(
    () => this.reachable().find((m) => m.kiraEmbeds && m.kiraId !== null) ?? null,
  );

  /** True once the gateway has answered, whatever it said — so "no ids" is not shown while loading. */
  protected readonly answered = computed(() => this.kira() !== null);
  /** True once the catalogue has answered — "nothing generates" is only said from an answer. */
  protected readonly declaredKnown = computed(() => this.catalog() !== null);

  // The four builders take the model rather than looking it up: the template calls them only
  // inside the `@if` that produced it, so there is no absent case to guard.

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

  /** The path selector for the current surface, built from the same base as the examples. */
  protected ucExample(): string {
    const base = this.base();
    const surface = this.surface() === 'gemini' ? '/v1beta' : '/kira/api/external';
    return `${base}/uc/${this.slug()}${surface}`;
  }

  /** Copy, and say that it happened — or that the clipboard refused (an insecure origin, a permission). */
  protected async copy(what: string, text: string): Promise<void> {
    if (!(await copyText(text))) {
      this.copyFailure.set(
        'The browser refused to write to the clipboard; select and copy manually.',
      );
      return;
    }
    // A refusal earlier in the session is not true of this copy.
    this.copyFailure.set('');
    this.copied.set(what);
    setTimeout(() => this.copied.update((current) => (current === what ? '' : current)), 2000);
  }
}
