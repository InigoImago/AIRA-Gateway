import { DatePipe } from '@angular/common';
import { Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, map, tap } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { ContentRead, ContentReadKind, Me } from '../../core/api/models';
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
  // Opening a request's details on a role that reads every use case's requests (IT Steuerung).
  oversight: 'Platform role (oversight)',
  use_case_admin: 'Use case administrator',
  use_case_member: 'Use case member',
};

/**
 * What an entry records as opened, in words. A row from a gateway that predates the field is a
 * content read: that was all it recorded.
 */
export const KIND_LABELS: Record<ContentReadKind, string> = {
  content: 'prompt and response',
  metadata: 'details',
};

/**
 * Who opened which request's details or stored prompt and response (`FRD-622` FR-6): when, who, from which
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
  /** Only openings of content, only openings of details, or both (empty). */
  protected readonly kind = signal<ContentReadKind | ''>('');
  protected readonly kindLabels = KIND_LABELS;
  /**
   * One page at a time, paged at the server by cursor (`FRD-622` FR-6). An offset would show a
   * row twice when a read is recorded while somebody pages; a cursor anchors each page.
   */
  protected readonly view = new CursorTableView<ContentRead>(
    (cursor) =>
      this.service.contentReads({ ...this.query(), cursor: cursor ?? undefined }).pipe(
        // The banner reports the last load, so one that succeeded takes an earlier failure away.
        tap(() => this.feedback.clear()),
        map((page) => ({ rows: page.reads, next: page.next_cursor, count: page.count })),
      ),
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

  /** A choice, not typing: asked at once, from the first page. */
  protected setKind(value: ContentReadKind | ''): void {
    this.kind.set(value);
    this.view.reset();
  }

  /** Whether a filter narrows the list, so an empty one says which empty it is. */
  protected filtered(): boolean {
    return !!(this.useCase().trim() || this.reader().trim() || this.kind());
  }

  protected kindOf(row: ContentRead): string {
    return KIND_LABELS[row.kind ?? 'content'];
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

  private query(): {
    useCase: string;
    reader: string;
    kind: ContentReadKind | '';
    limit: number;
  } {
    return {
      useCase: this.useCase().trim(),
      reader: this.reader().trim(),
      kind: this.kind(),
      limit: PAGE,
    };
  }
}
