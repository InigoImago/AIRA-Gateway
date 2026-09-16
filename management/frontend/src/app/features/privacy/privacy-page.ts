import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { PrivacyNotice } from '../../core/api/models';
import { PrivacyLanguagePicker } from '../../core/privacy/privacy-language-picker';
import { PrivacyNoticeBody } from '../../core/privacy/privacy-notice-body';
import { PrivacyNoticeService } from '../../core/privacy/privacy-notice.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * The privacy notice at its own address (`FRD-625` FR-2): readable at any time, in any language it
 * exists in, whether or not it is due. Art. 12 DSGVO wants the information easily accessible, which
 * a window seen once a month is not.
 */
@Component({
  selector: 'app-privacy-page',
  imports: [DatePipe, PrivacyLanguagePicker, PrivacyNoticeBody],
  templateUrl: './privacy-page.html',
  providers: [PageFeedback],
})
export class PrivacyPage implements OnInit {
  private readonly service = inject(PrivacyNoticeService);
  protected readonly feedback = inject(PageFeedback);
  protected readonly notice = signal<PrivacyNotice | null>(null);

  ngOnInit(): void {
    this.load();
  }

  protected load(language?: string): void {
    this.feedback.busy.set(true);
    this.service.get(language).subscribe({
      next: (notice) => {
        this.feedback.busy.set(false);
        this.feedback.clear();
        this.notice.set(notice);
      },
      error: (response: unknown) => {
        this.feedback.busy.set(false);
        this.feedback.fail(
          response,
          this.notice()?.labels['load_failed'] ?? 'The privacy notice could not be loaded.',
        );
      },
    });
  }
}
