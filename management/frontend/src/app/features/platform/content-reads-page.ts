import { DatePipe } from '@angular/common';
import { Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, map } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { ContentRead, Me } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { CursorTableView } from '../../core/ui/cursor-table-view';
import { PageFeedback } from '../../core/ui/page-feedback';
import { TablePager } from '../../core/ui/table-pager';

/** One page of the log, which is all the browser ever holds of it. */
const PAGE = 50;
/** One typed filter is one question, not one round trip per letter (`FRD-208`). */
const TYPING_PAUSE_MS = 300;

/** The authority a read rested on, in words (`FRD-505` FR-6). */
export const GROUND_LABELS: Record<string, string> = {
  incident: 'Platform role (incident)',
  use_case_admin: 'Use case administrator',
  use_case_member: 'Use case member',
};

/**
 * Who opened which request's stored prompt and response (`FRD-622` FR-6): when, who, from which
 * use case, on what ground and with which roles at the time. The record outlives the content, so a
 * row may name a request whose prompt has already expired.
 */
@Component({
  selector: 'app-content-reads-page',
  imports: [DatePipe, FormsModule, RouterLink, TablePager],
  templateUrl: './content-reads-page.html',
  providers: [PageFeedback],
})
export class ContentReadsPage implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly feedback = inject(PageFeedback);

  /** For the roles' names (`FRD-614` FR-10); the log's rows do not depend on it. */
  protected readonly me = signal<Me | null>(null);
  protected readonly useCase = signal('');
  protected readonly reader = signal('');
  /**
   * One page at a time, paged at the server by cursor (`FRD-622` FR-6). An offset would show a
   * row twice when a read is recorded while somebody pages; a cursor anchors each page.
   */
  protected readonly view = new CursorTableView<ContentRead>(
    (cursor) =>
      this.service
        .contentReads({ ...this.query(), cursor: cursor ?? undefined })
        .pipe(map((page) => ({ rows: page.reads, next: page.next_cursor, count: page.count }))),
    (response) => this.feedback.fail(response, 'Could not load the content-read log.'),
    PAGE,
    () => this.filtered(),
  );
  private readonly typed = new Subject<void>();

  ngOnInit(): void {
    // A failure leaves the roles named by their slugs; the shell's header already reports that the
    // account could not be loaded, and a second banner here would say the same thing twice.
    this.meService.get().subscribe({ next: (me) => this.me.set(me), error: () => undefined });
    this.view.start();
    // A changed filter is a different list, so it starts again at its first page.
    const typing = this.typed
      .pipe(debounceTime(TYPING_PAUSE_MS))
      .subscribe(() => this.view.reset());
    this.destroyRef.onDestroy(() => typing.unsubscribe());
  }

  protected setUseCase(value: string): void {
    this.useCase.set(value);
    this.typed.next();
  }

  protected setReader(value: string): void {
    this.reader.set(value);
    this.typed.next();
  }

  /** Whether a filter narrows the list, so an empty one says which empty it is. */
  protected filtered(): boolean {
    return !!(this.useCase().trim() || this.reader().trim());
  }

  /**
   * Where a read's request is shown: its use case's requests, focused on that one row. The content
   * is not opened by the link — opening it is a read of its own, and is recorded (`FRD-622` FR-6).
   */
  protected requestLink(row: ContentRead): string[] {
    return row.use_case ? ['/use-cases', row.use_case] : ['/requests'];
  }

  protected requestQuery(row: ContentRead): Record<string, string> {
    return row.use_case
      ? { tab: 'traces', request: row.request_log_id }
      : { request: row.request_log_id };
  }

  protected who(row: ContentRead): string {
    return row.username || row.subject;
  }

  protected ground(row: ContentRead): string {
    return GROUND_LABELS[row.ground] ?? row.ground;
  }

  /**
   * The roles held when the content was read, in the server's names; `null` is a read from before
   * they were kept. A role deleted since is named by its slug, which is all the record still has.
   */
  protected roles(row: ContentRead): string {
    if (row.roles === null) return 'Not recorded';
    if (!row.roles.length) return 'None';
    const labels = this.me()?.role_labels ?? {};
    return row.roles.map((role) => labels[role] ?? role).join(', ');
  }

  private query(): { useCase: string; reader: string; limit: number } {
    return { useCase: this.useCase().trim(), reader: this.reader().trim(), limit: PAGE };
  }
}
