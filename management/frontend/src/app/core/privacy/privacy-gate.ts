import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { errorMessage } from '../api/error-message';
import { PrivacyNotice } from '../api/models';
import { AuthService } from '../auth/auth.service';
import { Modal } from '../ui/modal';
import { PrivacyLanguagePicker } from './privacy-language-picker';
import { PrivacyNoticeBody } from './privacy-notice-body';
import { PrivacyNoticeService } from './privacy-notice.service';

/** What the shell says before a notice exists to take the words from. */
const LOAD_FAILED = 'The privacy notice could not be loaded.';

/**
 * The privacy notice as a window that must be acknowledged before working (`FRD-625` FR-1).
 *
 * **The server decides whether it opens** (`due`), from `AIRA_PRIVACY_NOTICE_MODE` and this
 * person's acknowledgements; the console only asks. The window cannot be dismissed: its one way out
 * is acknowledging. Changing the language keeps it open — the version covers every language, so
 * one acknowledgement answers for all of them.
 *
 * A notice that cannot be **loaded** does not lock the console: it is said as an alert above the
 * page with a retry. Refusing every screen because one endpoint failed would turn an outage of the
 * notice into an outage of the product, and the notice stays reachable from the footer.
 */
@Component({
  selector: 'app-privacy-gate',
  imports: [DatePipe, Modal, PrivacyLanguagePicker, PrivacyNoticeBody],
  templateUrl: './privacy-gate.html',
})
export class PrivacyGate implements OnInit {
  private readonly service = inject(PrivacyNoticeService);
  private readonly auth = inject(AuthService);

  protected readonly notice = signal<PrivacyNotice | null>(null);
  protected readonly open = signal(false);
  protected readonly busy = signal(false);
  protected readonly loadError = signal<string | null>(null);
  protected readonly acknowledgeError = signal<string | null>(null);

  /** Why the window opened, in the notice's language. */
  protected readonly reason = computed(() => {
    const notice = this.notice();
    return notice?.due ? (notice.labels[`due_${notice.due}`] ?? '') : '';
  });

  ngOnInit(): void {
    if (this.auth.isAuthenticated()) this.load();
  }

  /** Load the notice — in `language` when the reader chose one — and open it when it is due. */
  protected load(language?: string): void {
    this.busy.set(true);
    this.service.get(language).subscribe({
      next: (notice) => {
        this.busy.set(false);
        this.loadError.set(null);
        this.notice.set(notice);
        if (notice.due) this.open.set(true);
      },
      error: (response: unknown) => {
        this.busy.set(false);
        const fallback = this.notice()?.labels['load_failed'] ?? LOAD_FAILED;
        if (this.open()) {
          this.acknowledgeError.set(errorMessage(response, fallback));
        } else {
          this.loadError.set(errorMessage(response, fallback));
        }
      },
    });
  }

  protected acknowledge(): void {
    const notice = this.notice();
    if (!notice) return;
    this.busy.set(true);
    this.acknowledgeError.set(null);
    this.service.acknowledge(notice.version, notice.language).subscribe({
      next: (acknowledged) => {
        this.busy.set(false);
        this.notice.set({ ...notice, due: null, acknowledged_at: acknowledged.last_at });
        this.open.set(false);
      },
      error: (response: unknown) => {
        this.busy.set(false);
        this.acknowledgeError.set(errorMessage(response, notice.labels['acknowledge_failed']));
        // The notice changed while it was open: show the current one, and keep asking.
        if ((response as { status?: number } | null)?.status === 409) this.load(notice.language);
      },
    });
  }
}
