import { Component, computed, input, signal } from '@angular/core';
import { Capability, CatalogModel, KiraModel } from '../../core/api/models';
import { gatewayBase } from '../../core/api/prefixes';
import { copyText } from '../../core/ui/clipboard';
import { InfoHint } from '../../core/ui/info-hint';

/** A released model, with what each interface needs to address it. */
interface Reachable {
  /** The name the Gemini API addresses, which is the model's own name. */
  name: string;
  /** The integer the KIRA API addresses, where one is assigned. */
  kiraId: number | null;
  /** What the KIRA listing says it does — the KIRA examples come from this. */
  kiraGenerates: boolean;
  kiraEmbeds: boolean;
  kiraRanks: boolean;
  /**
   * What the catalogue declares it does — every other example comes from this, because the Gemini
   * API addresses a model by name whether or not the KIRA listing carries it. `null` where the
   * catalogue has not answered or does not hold the model.
   */
  generates: boolean | null;
  embeds: boolean | null;
  speaks: boolean | null;
  ranks: boolean | null;
  /** Whether it can call tools, which a coding assistant needs before anything else. */
  callsTools: boolean | null;
}

/** The client kinds the panel has a request for. Tabs, because a reader wires up one of them. */
export type ClientKind = 'curl' | 'python' | 'typescript' | 'opencode' | 'kira';

/** The tabs, in the order a reader is most likely to need them. */
const CLIENTS: readonly { kind: ClientKind; label: string }[] = [
  { kind: 'curl', label: 'curl' },
  { kind: 'python', label: 'Python' },
  { kind: 'typescript', label: 'TypeScript' },
  { kind: 'opencode', label: 'OpenCode' },
  { kind: 'kira', label: 'KIRA client' },
];

/**
 * How to call this use case: the address of each interface, the credentials, the models and a
 * request for every kind of client (`FRD-206`, `FRD-149`).
 *
 * **Every address names the use case.** A model call names its use case whatever the credential,
 * so each base URL here carries `/uc/<slug>` and every example is built from one — pasted, it works
 * with a key from the *API keys* tab or a Keycloak token alike.
 *
 * Everything is derived, so nothing goes stale: the base is the one this console reaches the
 * gateway at, and the models are the use case's release (`FRD-308`). What each one does comes from
 * where each interface learns it — the catalogue for the Gemini API and its clients, the gateway's
 * KIRA listing for the KIRA API, with its ids. It never invents a model the use case may not call,
 * nor a KIRA id for a model without one — an example that fails when pasted is worse than none.
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
    /* An address is the longest unbreakable string on the page; let it break rather than push. */
    .connect__address {
      overflow-wrap: anywhere;
    }
    .connect__step {
      margin-top: 1.5rem;
    }
    /* The address and its button on one line where it fits, the button never pushed off it. */
    .connect__address-row {
      gap: 0.75rem;
      margin-top: 0.25rem;
      flex-wrap: nowrap;
      align-items: flex-start;
    }
    .connect__address-row > code {
      min-width: 0;
      flex: 1 1 auto;
    }
    .connect__address-row > button {
      flex: 0 0 auto;
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
   * Whether the use case carries tool calls (`FRD-131`) — what a coding assistant needs before
   * anything else. `null` while the use case has not loaded: nothing is said from no answer.
   */
  readonly toolsEnabled = input<boolean | null>(null);

  protected readonly clients = CLIENTS;
  /** Which client's requests are on screen. */
  protected readonly client = signal<ClientKind>('curl');

  protected readonly copied = signal('');
  /** The clipboard's refusal — kept apart from `failure`, so it never reads as a broken gateway. */
  protected readonly copyFailure = signal('');

  /**
   * Where this console reaches the gateway. `/gw` is the console's proxy and forwards the whole
   * gateway, so it works for any client that can reach the console; it is deliberately not
   * presented as *the* gateway address, which only the operator knows.
   */
  protected readonly base = computed(() => gatewayBase());
  /** The gateway's address for this use case: what goes in front of every interface's path. */
  protected readonly useCaseBase = computed(() => `${this.base()}/uc/${this.slug()}`);
  protected readonly geminiBase = computed(() => `${this.useCaseBase()}/v1beta`);
  protected readonly kiraBase = computed(() => `${this.useCaseBase()}/kira/api/external`);
  /**
   * Google's ranking path. Its project, location and configuration select nothing here — the
   * gateway ranks for the use case the request names — so they are written as Google's own
   * defaults, which a client of Google's ranking API already sends.
   */
  protected readonly rankUrl = computed(
    () =>
      `${this.useCaseBase()}/v1/projects/aira/locations/global/rankingConfigs/` +
      'default_ranking_config:rank',
  );

  /** The models this use case can actually call, with what each interface needs to address them. */
  protected readonly reachable = computed<Reachable[]>(() => {
    const listing = this.kira();
    const byName = new Map((listing ?? []).map((model) => [model.name, model]));
    const catalog = this.catalog();
    const declared = new Map((catalog ?? []).map((model) => [model.name, model]));
    return this.released().map((name) => {
      const entry = byName.get(name);
      const capabilities = entry?.capabilities ?? [];
      const own = declared.get(name)?.capabilities;
      const says = (capability: Capability) => (own ? own.includes(capability) : null);
      return {
        name,
        // `?? null` rather than `?? 0`: a model without a number has none, and zero is a number.
        kiraId: entry?.id ?? null,
        kiraGenerates: capabilities.includes('Chat'),
        kiraEmbeds: capabilities.includes('Embedding'),
        kiraRanks: capabilities.includes('Reranker'),
        generates: says('generate'),
        embeds: says('embed'),
        speaks: says('speech'),
        ranks: says('rerank'),
        callsTools: says('tools'),
      };
    });
  });

  /** The examples' models, each declared able to do it in the catalogue. */
  protected readonly chatModel = computed(
    () => this.reachable().find((m) => m.generates === true) ?? null,
  );
  protected readonly embedModel = computed(
    () => this.reachable().find((m) => m.embeds === true) ?? null,
  );
  protected readonly speechModel = computed(
    () => this.reachable().find((m) => m.speaks === true) ?? null,
  );
  protected readonly rankModel = computed(
    () => this.reachable().find((m) => m.ranks === true) ?? null,
  );
  /** What a coding assistant can use: released and able to call tools. */
  protected readonly toolModels = computed(() =>
    this.reachable().filter((m) => m.callsTools === true),
  );
  /** The KIRA examples' models: listed for it by the gateway, with a number to address it by. */
  protected readonly kiraChatModel = computed(
    () => this.reachable().find((m) => m.kiraGenerates && m.kiraId !== null) ?? null,
  );
  protected readonly kiraEmbedModel = computed(
    () => this.reachable().find((m) => m.kiraEmbeds && m.kiraId !== null) ?? null,
  );
  protected readonly kiraRankModel = computed(
    () => this.reachable().find((m) => m.kiraRanks && m.kiraId !== null) ?? null,
  );

  /** True once the gateway has answered, whatever it said — so "no ids" is not shown while loading. */
  protected readonly answered = computed(() => this.kira() !== null);
  /** True once the catalogue has answered — "nothing generates" is only said from an answer. */
  protected readonly declaredKnown = computed(() => this.catalog() !== null);

  /** What a model serves, in words, from the catalogue; `null` while that is not known. */
  protected serves(model: Reachable): string | null {
    const kinds = [
      model.generates ? 'generation' : null,
      model.embeds ? 'embeddings' : null,
      model.speaks ? 'speech' : null,
      model.ranks ? 'ranking' : null,
    ].filter((kind): kind is string => kind !== null);
    if (kinds.length) return kinds.join(', ');
    return model.generates === false ? 'none of these, as the catalog declares it' : null;
  }

  // The builders take the model rather than looking it up: the template calls them only inside the
  // `@if` that produced it, so there is no absent case to guard.

  protected geminiChat(model: Reachable): string {
    return [
      `curl -s ${this.geminiBase()}/models/${model.name}:generateContent \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"contents":[{"parts":[{"text":"Say OK."}]}],`,
      `       "generationConfig":{"maxOutputTokens":64}}'`,
    ].join('\n');
  }

  protected geminiStream(model: Reachable): string {
    return [
      `curl -sN '${this.geminiBase()}/models/${model.name}:streamGenerateContent?alt=sse' \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"contents":[{"parts":[{"text":"Count to three."}]}]}'`,
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

  protected geminiSpeech(model: Reachable): string {
    return [
      `curl -s ${this.geminiBase()}/models/${model.name}:generateContent \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"contents":[{"parts":[{"text":"Good morning."}]}],`,
      `       "generationConfig":{"responseModalities":["AUDIO"],`,
      `         "speechConfig":{"voiceConfig":{"prebuiltVoiceConfig":{"voiceName":"Kore"}}}}}'`,
    ].join('\n');
  }

  protected geminiRank(model: Reachable): string {
    return [
      `curl -s ${this.rankUrl()} \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"model":"${model.name}","query":"Which one is about an invoice?",`,
      `       "records":[{"id":"1","content":"Invoice for October"},`,
      `                  {"id":"2","content":"Minutes of the team meeting"}]}'`,
    ].join('\n');
  }

  /** What is left of the caller's own allowance — the request a client makes before a refusal. */
  protected budgetCheck(): string {
    return `curl -s ${this.geminiBase()}/me/budget -H 'x-goog-api-key: <your key>'`;
  }

  protected python(chat: Reachable | null, embed: Reachable | null): string {
    const lines = [
      'from google import genai',
      'from google.genai import types',
      '',
      'client = genai.Client(',
      '    api_key="<your key>",',
      `    http_options=types.HttpOptions(base_url="${this.useCaseBase()}", api_version="v1beta"),`,
      ')',
    ];
    if (chat) {
      lines.push(
        '',
        `answer = client.models.generate_content(model="${chat.name}", contents="Say OK.")`,
        'print(answer.text)',
      );
    }
    if (embed) {
      lines.push(
        '',
        `vectors = client.models.embed_content(model="${embed.name}", contents="hello")`,
        'print(len(vectors.embeddings[0].values))',
      );
    }
    return lines.join('\n');
  }

  protected typescript(chat: Reachable): string {
    return [
      "import { createGoogleGenerativeAI } from '@ai-sdk/google';",
      "import { generateText } from 'ai';",
      '',
      'const aira = createGoogleGenerativeAI({',
      `  baseURL: '${this.geminiBase()}',`,
      "  apiKey: '<your key>',",
      '});',
      '',
      `const { text } = await generateText({ model: aira('${chat.name}'), prompt: 'Say OK.' });`,
      'console.log(text);',
    ].join('\n');
  }

  /** OpenCode's provider block, as the *API keys* tab writes it — without the key. */
  protected opencode(models: Reachable[]): string {
    const entries = Object.fromEntries(
      models.map((model) => [model.name, { name: `${model.name} via AIRA` }]),
    );
    return JSON.stringify(
      {
        $schema: 'https://opencode.ai/config.json',
        provider: {
          aira: {
            npm: '@ai-sdk/google',
            name: 'AIRA Gateway',
            options: { baseURL: this.geminiBase(), apiKey: '<your key>' },
            models: entries,
          },
        },
        model: `aira/${models[0].name}`,
      },
      null,
      2,
    );
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

  protected kiraEmbed(model: Reachable): string {
    return [
      `curl -s ${this.kiraBase()}/embed \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"text":"hello","model_id":${model.kiraId}}'`,
    ].join('\n');
  }

  protected kiraRerank(model: Reachable): string {
    return [
      `curl -s ${this.kiraBase()}/rerank \\`,
      `  -H 'x-goog-api-key: <your key>' \\`,
      `  -H 'content-type: application/json' \\`,
      `  -d '{"model_id":${model.kiraId},"query":"Which one is about an invoice?",`,
      `       "records":[{"id":"1","title":"October","content":"Invoice for October"},`,
      `                  {"id":"2","title":"Meeting","content":"Minutes of the team meeting"}]}'`,
    ].join('\n');
  }

  protected selectClient(kind: ClientKind): void {
    this.client.set(kind);
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
