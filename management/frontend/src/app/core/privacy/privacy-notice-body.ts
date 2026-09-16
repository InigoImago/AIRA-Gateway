import { Component, input } from '@angular/core';
import { PrivacyNotice } from '../api/models';

/**
 * The notice's text: sections, and under `activities` one block per processing activity with its
 * five statements. Shared by the window and the page, so the two cannot come to say different
 * things. Everything shown is text from the server — no markup is interpreted.
 */
@Component({
  selector: 'app-privacy-notice-body',
  templateUrl: './privacy-notice-body.html',
})
export class PrivacyNoticeBody {
  readonly notice = input.required<PrivacyNotice>();
}
