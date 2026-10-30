import { Component, computed, input, signal } from '@angular/core';
import { Trace } from '../../core/api/models';
import { traceViewerUrl } from '../../core/runtime-config';
import { copyText } from '../../core/ui/clipboard';
import {
  chainLine,
  connectionFacts,
  duration,
  phaseSentence,
  providerFacts,
  sentLine,
  technicalText,
  timelineRows,
} from './technical';

/**
 * "Technical details" in an opened request (`FRD-630`): where it ended, its milestones to the
 * millisecond, what reached the caller, the provider's status word and request id, the exception
 * chain — and the trace, linked where the installation names a viewer.
 *
 * Open by default for a request that was not served: that is when a technician is reading. Codes
 * and numbers only, as the gateway recorded them, so strict data mode has nothing here to withhold.
 */
@Component({
  selector: 'app-technical-details',
  templateUrl: './technical-details.html',
  styleUrl: './technical-details.scss',
})
export class TechnicalDetails {
  readonly trace = input.required<Trace>();

  protected readonly record = computed(() => this.trace().technical ?? null);
  protected readonly failed = computed(() => this.trace().outcome !== 'served');
  protected readonly phase = computed(() => {
    const record = this.record();
    return record ? phaseSentence(record.phase) : null;
  });
  protected readonly timeline = computed(() => {
    const record = this.record();
    return record ? timelineRows(record) : [];
  });
  protected readonly connection = computed(() => {
    const record = this.record();
    return record ? connectionFacts(record) : [];
  });
  protected readonly sent = computed(() => {
    const record = this.record();
    return record ? sentLine(record) : null;
  });
  protected readonly provider = computed(() => {
    const record = this.record();
    return record ? providerFacts(record) : [];
  });
  protected readonly chain = computed(() => {
    const record = this.record();
    return record ? chainLine(record) : null;
  });
  protected readonly traceLink = computed(() => traceViewerUrl(this.trace().trace_id));
  protected readonly text = computed(() => technicalText(this.trace()));

  /** Whether the copy went through, or why it did not — never silent. */
  protected readonly copied = signal(false);
  protected readonly copyFailure = signal('');

  protected readonly duration = duration;

  protected async copy(): Promise<void> {
    if (!(await copyText(this.text()))) {
      this.copied.set(false);
      this.copyFailure.set(
        'The browser refused to write to the clipboard; select the text below and copy it.',
      );
      return;
    }
    this.copyFailure.set('');
    this.copied.set(true);
    setTimeout(() => this.copied.set(false), 2000);
  }
}
