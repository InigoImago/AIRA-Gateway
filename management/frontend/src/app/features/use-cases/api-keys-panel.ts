import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, model, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiKey, CatalogModel, IssuedApiKey } from '../../core/api/models';
import { gatewayBase } from '../../core/api/prefixes';
import { UseCaseService } from '../../core/api/use-case.service';
import { copyText } from '../../core/ui/clipboard';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { saveBlob } from '../../core/ui/save-blob';
import { ANOTHER_MEMBER, WITHHELD_REASON } from '../../core/ui/withheld';
import { openCodeModel } from './opencode-config';
import { OPENCODE_BUDGET_PLUGIN } from './opencode-plugin.generated';

/** The model an OpenCode configuration names when nothing is released to the use case at all. */
const FALLBACK_OPENCODE_MODEL = 'qwen2.5:3b';

/**
 * The use case's API keys: issuing, revealing once, revoking (`FRD-604`).
 *
 * Issuing needs **membership** and revoking needs management — seeing a use case grants neither
 * (`ADR-0007`). The parent loads the list, because the tab bar counts it.
 */
@Component({
  selector: 'app-api-keys-panel',
  imports: [DatePipe, FormsModule, InfoHint, Modal],
  templateUrl: './api-keys-panel.html',
})
export class ApiKeysPanel {
  readonly slug = input.required<string>();
  /** The keys the parent loaded. */
  readonly keys = input<ApiKey[]>([]);
  readonly canManage = input(false);
  readonly isMember = input(false);
  /** The model catalogue, for the OpenCode configuration. */
  readonly models = input<CatalogModel[]>([]);
  /** What this use case may call (`FRD-308`) — the gateway refuses every other model by name. */
  readonly released = input<string[]>([]);
  readonly currency = input('');
  /** The installation's key policy, as `/me` states it; the server decides (`ADR-0015`). */
  readonly defaultKeyDays = input.required<number>();
  readonly maxKeyDays = input.required<number>();
  /**
   * The key just issued, while its plaintext is on screen. A model rather than local state: the
   * plaintext is shown once, so it must outlive this panel when the reader switches tabs.
   */
  readonly issued = model<IssuedApiKey | null>(null);
  /** Raised after a key is issued or revoked, so the parent reloads the list it owns. */
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);
  /** The words in place of a key's person that strict data mode withholds. */
  protected readonly anotherMember = ANOTHER_MEMBER;
  protected readonly withheldReason = WITHHELD_REASON;

  protected readonly showIssueKey = signal(false);
  protected readonly keyLabel = signal('');
  /**
   * Issue the key **on behalf of** somebody else (`FRD-604` FR-5); empty means "me".
   *
   * Typed rather than picked from the membership list: the rule is "an identity with access to this
   * use case", which a group grant can satisfy without a membership row, and the server refuses a
   * wrong name by name.
   */
  protected readonly keyOwner = signal('');
  /** Days until the new key stops working; empty leaves the server to apply its default. */
  protected readonly keyExpiresInDays = signal('');
  protected readonly copied = signal(false);
  protected readonly configCopied = signal(false);
  protected readonly copyFailed = signal(false);

  /**
   * The models an assistant with this key can actually use: released to this use case, approved
   * for the installation, and able to call tools. A configuration listing anything else names
   * models the gateway refuses — and its first request fails.
   */
  private readonly toolModels = computed(() => {
    const released = new Set(this.released());
    return this.models().filter(
      (m) =>
        released.has(m.name) && m.approved !== false && (m.capabilities ?? []).includes('tools'),
    );
  });

  /** Whom the key will answer to: the typed owner, or the reader. */
  protected readonly ownerToBe = computed(() => this.keyOwner().trim());

  /**
   * Whether a key has lapsed. Kept apart from revocation, as the server keeps it: "it lapsed as
   * planned" and "we took it away" are different answers to an audit, and neither is "active".
   */
  protected expired(key: ApiKey): boolean {
    return !!key.expires_at && new Date(key.expires_at).getTime() <= Date.now();
  }

  /** Open the window afresh: an earlier failure on the page is not this window's outcome. */
  protected openIssueKey(): void {
    this.feedback.clear();
    this.showIssueKey.set(true);
  }

  protected issueKey(): void {
    if (this.feedback.busy()) {
      return;
    }
    // Whatever was typed is sent, and the server refuses what it will not issue in its own words
    // ("at least 1", "at most 180"); only an empty field leaves the lifetime to its default.
    const typed = this.keyExpiresInDays() as string | number | null;
    const days = typed === null || String(typed).trim() === '' ? null : Number(typed);
    this.feedback.run(
      this.service.issueApiKey(this.slug(), this.keyLabel(), days, this.keyOwner().trim() || null),
      {
        failure: 'Could not issue the key.',
        success: (issued: IssuedApiKey) => {
          this.keyLabel.set('');
          this.keyOwner.set('');
          this.keyExpiresInDays.set('');
          this.copied.set(false);
          this.copyFailed.set(false);
          this.issued.set(issued);
          this.showIssueKey.set(false);
          this.changed.emit();
        },
      },
    );
  }

  /**
   * The OpenCode configuration for the key that was just issued (`FRD-132`).
   *
   * Built while the plaintext exists: it is never retrievable later, so a config offered on any
   * other screen could only carry a placeholder. The key is written into the file rather than
   * referenced from the environment because the file goes to one developer's workstation; the
   * banner says so.
   */
  protected openCodeConfig(issued: IssuedApiKey): string {
    return JSON.stringify(
      {
        $schema: 'https://opencode.ai/config.json',
        provider: {
          aira: {
            npm: '@ai-sdk/google',
            name: 'AIRA Gateway',
            options: { baseURL: `${gatewayBase()}/v1beta`, apiKey: issued.api_key },
            models: Object.fromEntries(
              this.toolModels().map((m) => [m.name, openCodeModel(m, this.currency())]),
            ),
          },
        },
        model: `aira/${this.toolModels()[0]?.name ?? this.released()[0] ?? FALLBACK_OPENCODE_MODEL}`,
      },
      null,
      2,
    );
  }

  protected downloadOpenCodeConfig(issued: IssuedApiKey): void {
    saveBlob(
      new Blob([this.openCodeConfig(issued)], { type: 'application/json' }),
      'opencode.json',
    );
  }

  /**
   * The budget plugin, as the file a developer saves into `.opencode/plugins/aira-budget/`
   * (`FRD-144`).
   *
   * Offered **here** because this is where somebody gets their key, and a feature whose discovery
   * depends on reading a README in this repository is one most people never meet. The plugin shows
   * what is left of the allowance in OpenCode's sidebar by itself, which is the difference between
   * a figure a developer has and a command they would have to know about.
   *
   * No key is written into it: it uses the key OpenCode already sends to the gateway. So this file,
   * unlike the configuration, is not a credential.
   */
  protected downloadBudgetPlugin(): void {
    saveBlob(new Blob([OPENCODE_BUDGET_PLUGIN], { type: 'text/plain' }), 'tui.tsx');
  }

  protected copyOpenCodeConfig(issued: IssuedApiKey): void {
    void copyText(this.openCodeConfig(issued)).then((done) => {
      this.configCopied.set(done);
      this.copyFailed.set(!done);
    });
  }

  protected dismissIssued(): void {
    this.issued.set(null);
    this.copied.set(false);
    this.configCopied.set(false);
    this.copyFailed.set(false);
  }

  /** Without a clipboard the key is still on screen, so the panel says "copy it manually". */
  protected copyKey(value: string): void {
    void copyText(value).then((done) => {
      this.copied.set(done);
      this.copyFailed.set(!done);
    });
  }

  protected revokeKey(prefix: string): void {
    const question = `Revoke key aira_${prefix}_…? Any client still using it will start failing.`;
    if (!this.confirmService.ask(question)) {
      return;
    }
    this.feedback.run(this.service.revokeApiKey(this.slug(), prefix), {
      failure: 'Could not revoke the key.',
      success: () => {
        this.feedback.succeed('The key was revoked.');
        this.changed.emit();
      },
    });
  }
}
