import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, model, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiKey, CatalogModel, IssuedApiKey } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { openCodeModel } from './opencode-config';

/** The model an OpenCode configuration names when the catalogue offers none that calls tools. */
const FALLBACK_OPENCODE_MODEL = 'qwen2.5:3b';

/** The clipboard, or `undefined` outside a secure context. */
function clipboard(): Clipboard | undefined {
  return typeof navigator === 'undefined' ? undefined : navigator.clipboard;
}

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

  /** Models the catalogue declares able to call tools — the only ones an assistant can use. */
  private readonly toolModels = computed(() =>
    this.models().filter((m) => (m.capabilities ?? []).includes('tools')),
  );

  protected issueKey(): void {
    if (this.feedback.busy()) {
      return;
    }
    const days = Number(this.keyExpiresInDays());
    this.feedback.run(
      this.service.issueApiKey(
        this.slug(),
        this.keyLabel(),
        days > 0 ? days : null,
        this.keyOwner().trim() || null,
      ),
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
            options: { baseURL: `${window.location.origin}/gw/v1beta`, apiKey: issued.api_key },
            models: Object.fromEntries(
              this.toolModels().map((m) => [m.name, openCodeModel(m, this.currency())]),
            ),
          },
        },
        model: `aira/${this.toolModels()[0]?.name ?? FALLBACK_OPENCODE_MODEL}`,
      },
      null,
      2,
    );
  }

  protected downloadOpenCodeConfig(issued: IssuedApiKey): void {
    const blob = new Blob([this.openCodeConfig(issued)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'opencode.json';
    link.click();
    URL.revokeObjectURL(url);
  }

  protected copyOpenCodeConfig(issued: IssuedApiKey): void {
    const board = clipboard();
    if (!board) {
      this.copyFailed.set(true);
      return;
    }
    void board.writeText(this.openCodeConfig(issued)).then(
      () => this.configCopied.set(true),
      () => this.copyFailed.set(true),
    );
  }

  protected dismissIssued(): void {
    this.issued.set(null);
    this.copied.set(false);
    this.configCopied.set(false);
    this.copyFailed.set(false);
  }

  /** Without a clipboard the key is still on screen, so the panel says "copy it manually". */
  protected copyKey(value: string): void {
    const board = clipboard();
    if (!board) {
      this.copyFailed.set(true);
      return;
    }
    void board.writeText(value).then(
      () => {
        this.copied.set(true);
        this.copyFailed.set(false);
      },
      () => this.copyFailed.set(true),
    );
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
