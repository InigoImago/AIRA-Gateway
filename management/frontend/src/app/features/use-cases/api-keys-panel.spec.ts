import { TestBed } from '@angular/core/testing';
import { Observable, of } from 'rxjs';
import { ApiKey, CatalogModel, IssuedApiKey } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { ApiKeysPanel } from './api-keys-panel';

type Writable<T> = { set: (v: T) => void; (): T };

interface Panel {
  feedback: PageFeedback;
  showIssueKey: Writable<boolean>;
  keyLabel: Writable<string>;
  keyOwner: Writable<string>;
  keyExpiresInDays: Writable<string>;
  configCopied: Writable<boolean>;
  copied: () => boolean;
  copyFailed: () => boolean;
  issued: () => IssuedApiKey | null;
  issueKey: () => void;
  revokeKey: (prefix: string) => void;
  copyKey: (value: string) => void;
  dismissIssued: () => void;
  openCodeConfig: (issued: IssuedApiKey) => string;
  copyOpenCodeConfig: (issued: IssuedApiKey) => void;
}

interface Options {
  keys?: ApiKey[];
  isMember?: boolean;
  canManage?: boolean;
  models?: unknown[];
  issueApiKey?: Observable<unknown>;
  confirm?: boolean;
}

/** The catalogue the page would pass: one model that declares tool calling. */
const MODELS = [{ name: 'qwen2.5:3b', capabilities: ['generate', 'tools'] }];

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  let changed = 0;
  const service = {
    issueApiKey: (
      _s: string,
      label: string,
      expiresInDays?: number | null,
      owner?: string | null,
    ) => {
      // The owner is appended only when one was named, so a key issued for somebody else is
      // visibly different and the other assertions keep their meaning.
      calls.push(`issueApiKey:${label}:${expiresInDays ?? 'never'}${owner ? `:for:${owner}` : ''}`);
      return (
        options.issueApiKey ??
        of({ api_key: 'aira_ab_cd', prefix: 'ab', label, use_case: 'demo-uc' })
      );
    },
    revokeApiKey: (_s: string, prefix: string) => {
      calls.push(`revokeApiKey:${prefix}`);
      return of(undefined as unknown as void);
    },
  };
  TestBed.configureTestingModule({
    imports: [ApiKeysPanel],
    providers: [
      { provide: UseCaseService, useValue: service },
      { provide: ConfirmService, useValue: { ask: () => options.confirm ?? true } },
      PageFeedback,
    ],
  });
  const fixture = TestBed.createComponent(ApiKeysPanel);
  fixture.componentRef.setInput('slug', 'demo-uc');
  fixture.componentRef.setInput('keys', options.keys ?? []);
  fixture.componentRef.setInput('isMember', options.isMember ?? true);
  fixture.componentRef.setInput('canManage', options.canManage ?? true);
  fixture.componentRef.setInput('models', (options.models ?? MODELS) as CatalogModel[]);
  fixture.componentRef.setInput('defaultKeyDays', 30);
  fixture.componentRef.setInput('maxKeyDays', 180);
  fixture.componentInstance.changed.subscribe(() => (changed += 1));
  fixture.detectChanges();
  return {
    fixture,
    calls,
    changed: () => changed,
    component: fixture.componentInstance as unknown as Panel,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    html: () => fixture.nativeElement as HTMLElement,
  };
}

function click(fixture: { nativeElement: unknown; detectChanges: () => void }, selector: string) {
  const el = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(selector);
  expect(el, `no element for ${selector}`).not.toBeNull();
  el?.click();
  fixture.detectChanges();
}

describe('ApiKeysPanel — issuing', () => {
  it('issues a key, reveals it once, and closes the form', () => {
    const { component, changed } = setup();
    component.keyLabel.set('laptop');
    component.showIssueKey.set(true);
    component.issueKey();
    expect(component.issued()).toEqual({
      api_key: 'aira_ab_cd',
      prefix: 'ab',
      label: 'laptop',
      use_case: 'demo-uc',
    });
    expect(component.showIssueKey()).toBe(false);
    expect(component.keyLabel()).toBe('');
    // The page owns the list and its count, so it is asked to reload.
    expect(changed()).toBe(1);
  });

  // No client-side default lifetime: omitting the field lets the server apply the configured one,
  // and a second definition here would be wrong the first time the setting changed.
  it('sends no lifetime of its own, leaving the server to apply the default', () => {
    const { component, calls } = setup();
    component.keyLabel.set('laptop');
    component.issueKey();
    expect(calls).toContain('issueApiKey:laptop:never');
  });

  it('passes the lifetime the reader typed', () => {
    const { component, calls } = setup();
    component.keyLabel.set('laptop');
    component.keyExpiresInDays.set('30');
    component.issueKey();
    expect(calls).toContain('issueApiKey:laptop:30');
  });

  it('clears the lifetime after issuing, so the next key is a fresh decision', () => {
    const { component } = setup();
    component.keyExpiresInDays.set('30');
    component.issueKey();
    expect(component.keyExpiresInDays()).toBe('');
  });

  it('sends the owner only when one was named', () => {
    const { component, calls } = setup();

    component.issueKey();
    expect(calls.some((c) => c.includes('issueApiKey'))).toBe(true);
    expect(calls.join('|')).not.toContain('svc-');

    component.keyOwner.set('svc-chatbot');
    component.issueKey();
    expect(calls.some((c) => c.includes('svc-chatbot'))).toBe(true);
  });

  it('asks before revoking a key', () => {
    const declined = setup({ confirm: false });
    declined.component.revokeKey('ab12');
    expect(declined.calls).toEqual([]);

    const accepted = setup();
    accepted.component.revokeKey('ab12');
    expect(accepted.calls).toContain('revokeApiKey:ab12');
    expect(accepted.component.feedback.notice()).toBe('The key was revoked.');
    expect(accepted.changed()).toBe(1);
  });
});

describe('ApiKeysPanel — the revealed key', () => {
  it('confirms a successful clipboard copy', async () => {
    const { component } = setup();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.resolve() },
      configurable: true,
    });
    component.copyKey('aira_ab_cd');
    await Promise.resolve();
    await Promise.resolve();
    expect(component.copied()).toBe(true);
    expect(component.copyFailed()).toBe(false);
  });

  it('tells the user to copy manually when the clipboard is unavailable', () => {
    const { component } = setup();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    component.copyKey('aira_ab_cd');
    expect(component.copyFailed()).toBe(true);
    expect(component.copied()).toBe(false);
  });

  it('reports a rejected clipboard write', async () => {
    const { component } = setup();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.reject(new Error('denied')) },
      configurable: true,
    });
    component.copyKey('aira_ab_cd');
    await Promise.resolve();
    await Promise.resolve();
    expect(component.copyFailed()).toBe(true);
  });

  it('clears the revealed key when dismissed', () => {
    const { component } = setup();
    component.issueKey();
    component.dismissIssued();
    expect(component.issued()).toBeNull();
    expect(component.copied()).toBe(false);
  });

  it('forgets the copied state when the key is dismissed', () => {
    const { component } = setup();
    component.issueKey();
    component.configCopied.set(true);
    component.dismissIssued();

    expect(component.configCopied()).toBe(false);
    expect(component.issued()).toBeNull();
  });
});

describe('ApiKeysPanel rendering', () => {
  const KEYS: ApiKey[] = [
    { prefix: 'ab12', label: 'laptop', owner: 'alice', is_active: true },
    { prefix: 'cd34', label: '', owner: 'bob', is_active: false, revoked_at: '2026-01-01' },
  ];

  it('distinguishes active from revoked keys', () => {
    const { text, html } = setup({ keys: KEYS });
    expect(text()).toContain('aira_ab12_…');
    expect(text()).toContain('active');
    expect(text()).toContain('revoked');
    // A revoked key offers no revoke button.
    expect(html().querySelectorAll('[aria-label^="Revoke key"]').length).toBe(1);
  });

  it('reveals an issued key once, with a copy affordance', () => {
    const harness = setup();
    harness.component.issueKey();
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('shown only once');
    expect(harness.html().querySelector('.secret')?.textContent).toContain('aira_ab_cd');
  });

  it('explains a blocked clipboard next to the key', () => {
    const harness = setup();
    harness.component.issueKey();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    harness.component.copyKey('aira_ab_cd');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('copy it manually');
  });

  it('revokes a key from its row button and copies the revealed one', () => {
    const harness = setup({ keys: [{ prefix: 'ab12', label: '', owner: 'a', is_active: true }] });
    click(harness.fixture, '[aria-label="Revoke key ab12"]');
    expect(harness.calls).toContain('revokeApiKey:ab12');

    harness.component.issueKey();
    harness.fixture.detectChanges();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    click(harness.fixture, '.callout--warning .btn--primary');
    expect(harness.component.copyFailed()).toBe(true);

    click(harness.fixture, '.callout--warning .btn--ghost');
    expect(harness.component.issued()).toBeNull();
  });

  it('shows who created a key when that is not who owns it', () => {
    // A shared credential (`FRD-604` FR-5): the owner answers for it, the issuer made it, and both
    // are on the row.
    const harness = setup({
      keys: [
        {
          prefix: 'aa',
          label: 'chatbot',
          owner: 'svc-chatbot',
          issued_by: 'vadim',
          is_active: true,
        },
        { prefix: 'bb', label: 'mine', owner: 'vadim', is_active: true },
      ] as never,
    });

    const rows = [...harness.html().querySelectorAll('tbody tr')].map((r) => r.textContent ?? '');
    expect(rows[0]).toContain('svc-chatbot');
    expect(rows[0]).toContain('issued by vadim');
    // An ordinary key says nothing extra — a distinction on every row is one nobody reads.
    expect(rows[1]).not.toContain('issued by');
  });
});

/**
 * Who answers for a key (`FRD-604`): the issuer is told, before issuing and again beside the
 * plaintext, that requests made with it carry their name.
 */
describe('ApiKeysPanel — accountability for a key', () => {
  it('says whose name the key will carry before it is issued', () => {
    const harness = setup();
    harness.component.showIssueKey.set(true);
    harness.fixture.detectChanges();

    const notice = harness.html().querySelector('[data-testid="key-responsibility"]');
    expect(notice?.textContent).toContain('your name');
    // True of a shared credential as well: it claims responsibility for the key, never that its
    // owner wrote the request.
    expect(notice?.textContent).toContain('anything you hand it to');
  });

  it('repeats it beside the plaintext, which is the last moment anybody reads it', () => {
    const harness = setup({
      issueApiKey: of({
        api_key: 'aira_ab12_secret',
        prefix: 'ab12',
        label: '',
        use_case: 'demo-uc',
      }),
    });
    harness.component.showIssueKey.set(true);
    harness.fixture.detectChanges();
    harness.component.issueKey();
    harness.fixture.detectChanges();

    const notice = harness.html().querySelector('[data-testid="key-issued-responsibility"]');
    expect(notice?.textContent).toContain('attributed to your name');
  });
});

/** The OpenCode configuration (`FRD-132`), built while the plaintext exists. */
describe('ApiKeysPanel — the OpenCode configuration', () => {
  function configFor(models?: unknown[]) {
    const { component } = setup({ models });
    component.issueKey();
    return JSON.parse(component.openCodeConfig(component.issued()!));
  }

  it('builds a configuration carrying the key that was just issued', () => {
    const config = configFor();

    expect(config.provider.aira.options.apiKey).toBe('aira_ab_cd');
    expect(config.provider.aira.options.baseURL).toContain('/gw/v1beta');
  });

  it('names only the models that declare tool calling', () => {
    // An assistant cannot use a model that answers in prose (`FRD-131`).
    const config = configFor();

    expect(Object.keys(config.provider.aira.models)).toEqual(['qwen2.5:3b']);
    expect(config.model).toBe('aira/qwen2.5:3b');
  });

  it('carries each model’s limits and prices, which OpenCode cannot ask the API for', () => {
    // `FRD-132` §11: OpenCode's context gauge is `used / limit.context`, from this file.
    const config = configFor([
      {
        name: 'qwen2.5:3b',
        capabilities: ['tools'],
        context_window: 32768,
        max_output_tokens: 4096,
        input_price_per_million: '0.10',
        output_price_per_million: '0.40',
      },
    ]);

    // The limits always; prices only in the unit OpenCode displays, and this currency is not USD.
    expect(config.provider.aira.models['qwen2.5:3b']).toEqual({
      name: 'qwen2.5:3b via AIRA',
      limit: { context: 32768, output: 4096 },
    });
  });

  it('leaves a limit out rather than writing a zero the catalog never declared', () => {
    // `0` is a full context window to a client, not "unknown".
    const config = configFor([{ name: 'qwen2.5:3b', capabilities: ['tools'] }]);

    expect(config.provider.aira.models['qwen2.5:3b']).toEqual({ name: 'qwen2.5:3b via AIRA' });
  });

  it('falls back to a named model rather than an empty provider', () => {
    // A file with no model fails with no clue why; one with a named model can be edited.
    const config = configFor([]);

    expect(config.model).toContain('aira/');
  });

  it('does not offer a model whose catalog entry declares no capabilities', () => {
    // `FRD-114`: undeclared means unsupported.
    const config = configFor([
      { name: 'silent-model' },
      { name: 'qwen2.5:3b', capabilities: ['tools'] },
    ]);

    expect(Object.keys(config.provider.aira.models)).toEqual(['qwen2.5:3b']);
  });

  it('says so when the clipboard will not take the configuration', () => {
    // The file is still downloadable, so the button reports why rather than appearing to work.
    const { component } = setup();
    component.issueKey();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });

    component.copyOpenCodeConfig(component.issued()!);

    expect(component.copyFailed()).toBe(true);
    expect(component.configCopied()).toBe(false);
  });

  it('reports a rejected write of the configuration', async () => {
    const { component } = setup();
    component.issueKey();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.reject(new Error('denied')) },
      configurable: true,
    });

    component.copyOpenCodeConfig(component.issued()!);
    await Promise.resolve();
    await Promise.resolve();

    expect(component.copyFailed()).toBe(true);
  });

  it('confirms a configuration that reached the clipboard', async () => {
    const { component } = setup();
    component.issueKey();
    let written = '';
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: (value: string) => {
          written = value;
          return Promise.resolve();
        },
      },
      configurable: true,
    });

    component.copyOpenCodeConfig(component.issued()!);
    await Promise.resolve();

    expect(component.configCopied()).toBe(true);
    expect(written).toContain('aira_ab_cd');
  });
});
