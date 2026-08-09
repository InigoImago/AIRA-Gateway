import { Component, inject } from '@angular/core';
import { PageFeedback } from '../../core/ui/page-feedback';
import { TracesTab } from '../use-cases/traces-tab';

/**
 * Every request, across every use case this caller may see (`FRD-505` FR-2).
 *
 * The complaint that produced it: the request view lived inside one use case's detail page, so
 * investigating meant picking a use case first — from a screen somebody opens precisely because
 * they do not yet know where the problem is. IT Security and Global Administrators work across use
 * cases; here the use case is a column and a filter rather than a prerequisite.
 *
 * **A summary panel used to sit above the table and has been removed.** It grouped the window by
 * key, caller and machine, which answered a real question — you cannot filter by a key you have
 * never seen — but it pushed the requests themselves below the fold, and the first person to open
 * the screen asked where their traces had gone. A discovery aid that hides the thing being
 * discovered is a net loss. The same job is done inside the table now: the values are in the rows,
 * and the filters are beside them.
 *
 * The table is `TracesTab`, unchanged and unduplicated — a second copy is how the two API surfaces
 * of `FRD-126` and the two consoles of `FRD-206` came to disagree with each other.
 */
@Component({
  selector: 'app-requests-page',
  imports: [TracesTab],
  templateUrl: './requests-page.html',
  // The page owns the single banner; the table reports through it (`FRD-203`: one banner per page,
  // not one per panel).
  providers: [PageFeedback],
})
export class RequestsPage {
  protected readonly feedback = inject(PageFeedback);
}
