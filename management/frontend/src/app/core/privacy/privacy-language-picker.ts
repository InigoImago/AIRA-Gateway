import { Component, input, output } from '@angular/core';
import { PrivacyNotice } from '../api/models';

/** The languages the notice exists in, each by its own name, the current one pressed. */
@Component({
  selector: 'app-privacy-language-picker',
  template: `
    <div class="privacy-languages" role="group" [attr.aria-label]="notice().labels['language']">
      @for (language of notice().languages; track language.code) {
        <button
          type="button"
          class="btn btn--sm"
          [class.btn--ghost]="language.code !== notice().language"
          [attr.aria-pressed]="language.code === notice().language"
          [attr.lang]="language.code"
          [attr.data-testid]="'privacy-language-' + language.code"
          [disabled]="busy()"
          (click)="chosen.emit(language.code)"
        >
          {{ language.name }}
        </button>
      }
    </div>
  `,
})
export class PrivacyLanguagePicker {
  readonly notice = input.required<PrivacyNotice>();
  readonly busy = input(false);
  readonly chosen = output<string>();
}
