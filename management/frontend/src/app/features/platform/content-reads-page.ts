import { DatePipe } from '@angular/common';
import { Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime } from 'rxjs';
import { ContentRead } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';

const PAGE = 50;
/** One typed filter is one question, not one round trip per letter (`FRD-208`). */
const TYPING_PAUSE_MS = 300;

/** The authority a read rested on, in words (`FRD-505` FR-6). */
export const GROUND_LABELS: Record<string, string> = {
  incident: 'Platform role (incident)',
  use_case_admin: 'Use case administrator',
  use_case_member: 'Use case member',
};

/** Organisation-wide roles, in the words the header uses. */
export const ROLE_LABELS: Record<string, string> = {
  'global-admin': 'Global administrator',
  'it-security': 'IT Security',
  'it-steuerung': 'IT Steuerung',
};

/**
 * Who opened which request's stored prompt and response (`FRD-622` FR-6): when, who, from which
 * use case, on what ground and with which roles at the time. The record outlives the content, so a
 * row may name a request whose prompt has already expired.
 */
@Component({
  selector: 'app-content-reads-page',
  imports: [DatePipe, FormsModule, RouterLink],
  templateUrl: './content-reads-page.html',
  providers: [PageFeedback],
})
export class ContentReadsPage implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly feedback = inject(PageFeedback);

  protected readonly reads = signal<ContentRead[]>([]);
  protected readonly cursor = signal<string | null>(null);
  protected readonly loading = signal(true);
  protected readonly loadingMore = signal(false);
  protected readonly useCase = signal('');
  protected readonly reader = signal('');
  private readonly typed = new Subject<void>();

  ngOnInit(): void {
    this.load();
    const typing = this.typed.pipe(debounceTime(TYPING_PAUSE_MS)).subscribe(() => this.load());
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

  protected load(): void {
    this.loading.set(true);
    this.service.contentReads(this.query()).subscribe({
      next: (page) => {
        this.reads.set(page.reads);
        this.cursor.set(page.next_cursor);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.feedback.fail(response, 'Could not load the content-read log.');
      },
    });
  }

  /** The next page, appended. A cursor from the same filters, or the page would mix two lists. */
  protected loadMore(): void {
    const next = this.cursor();
    if (!next || this.loadingMore()) return;
    this.loadingMore.set(true);
    this.service.contentReads({ ...this.query(), cursor: next }).subscribe({
      next: (page) => {
        this.reads.update((rows) => [...rows, ...page.reads]);
        this.cursor.set(page.next_cursor);
        this.loadingMore.set(false);
      },
      error: (response: unknown) => {
        this.loadingMore.set(false);
        this.feedback.fail(response, 'Could not load more reads.');
      },
    });
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

  /** The roles held when the content was read; `null` is a read from before they were kept. */
  protected roles(row: ContentRead): string {
    if (row.roles === null) return 'Not recorded';
    if (!row.roles.length) return 'None';
    return row.roles.map((role) => ROLE_LABELS[role] ?? role).join(', ');
  }

  private query(): { useCase: string; reader: string; limit: number } {
    return { useCase: this.useCase().trim(), reader: this.reader().trim(), limit: PAGE };
  }
}
