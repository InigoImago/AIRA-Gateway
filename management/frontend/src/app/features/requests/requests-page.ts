import { Component, inject } from '@angular/core';
import { PageFeedback } from '../../core/ui/page-feedback';
import { TracesTab } from '../use-cases/traces-tab';

/**
 * Every request, across every use case this caller may see (`FRD-505` FR-2).
 *
 * Investigation starts without knowing which use case is at fault, so here the use case is a
 * column and a filter rather than a prerequisite. The table is `TracesTab` itself, not a copy — a
 * second copy is how two implementations of one screen come to disagree (`FRD-126`, `FRD-206`).
 */
@Component({
  selector: 'app-requests-page',
  imports: [TracesTab],
  templateUrl: './requests-page.html',
  // The page owns the single banner; the table reports through it (`FRD-203`).
  providers: [PageFeedback],
})
export class RequestsPage {
  protected readonly feedback = inject(PageFeedback);
}
