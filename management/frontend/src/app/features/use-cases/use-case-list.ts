import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { errorMessage } from '../../core/api/error-message';
import { MeService } from '../../core/api/me.service';
import { Me, UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { can } from '../../core/auth/roles';
import { Modal } from '../../core/ui/modal';
import { ServerTableView } from '../../core/ui/server-table-view';
import { TablePager } from '../../core/ui/table-pager';
import { SLUG_PATTERN, slugify } from './use-case-slug';

@Component({
  selector: 'app-use-case-list',
  // The create window is the shared `Modal`, which owns Escape, the keyboard and the backdrop.
  imports: [FormsModule, Modal, RouterLink, TablePager],
  templateUrl: './use-case-list.html',
})
export class UseCaseList implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly router = inject(Router);

  /**
   * The list, searched and paged at the server (`FRD-208`).
   *
   * Not client-side: the endpoint computes object-level permissions per row, so fetching everything
   * would pay for every row on every load. Searchable by name or technical id — somebody arriving
   * from a log line has only the second.
   */
  protected readonly view = new ServerTableView<UseCase>(
    (query, page) => this.service.listPage(query, page),
    (response) => this.error.set(errorMessage(response, 'Failed to load use cases.')),
  );
  protected readonly useCases = this.view.rows;
  protected readonly loading = this.view.loading;
  protected readonly creating = signal(false);
  /** Why the list could not be loaded — the page's banner. */
  protected readonly error = signal<string | null>(null);
  /** Why a creation was refused — said inside the window, beside the button that was pressed. */
  protected readonly createError = signal<string | null>(null);
  protected readonly me = signal<Me | null>(null);
  protected readonly showCreate = signal(false);
  protected readonly slug = signal('');
  protected readonly name = signal('');
  /** Whether the id was typed by hand. Once it was, the name stops overwriting it. */
  private readonly slugEdited = signal(false);

  /** Creating a use case needs `usecase.create`; the server refuses anyone without it, so nobody
   *  else is offered the action. */
  protected readonly canCreate = computed(() => can(this.me(), 'usecase.create'));

  /**
   * Whether this session carries no role at all.
   *
   * Roles come from the token's groups at sign-in (`ADR-0017`), so a roleless session looks like
   * an empty installation; the list says which empty it is (`FRD-206`).
   */
  protected readonly hasNoRole = computed(() => this.me() !== null && !this.me()?.roles?.length);

  /** Nothing at all — as opposed to nothing matching, which the search says in its own words. */
  protected readonly isEmpty = computed(
    () => !this.loading() && this.view.total() === 0 && !this.view.filtered(),
  );

  /** Why the form cannot be submitted yet — shown inline instead of failing silently. */
  protected slugError(): string | null {
    if (!this.slug()) return null;
    return SLUG_PATTERN.test(this.slug())
      ? null
      : 'Lowercase letters, digits, and hyphens only — no spaces.';
  }

  protected canSubmit(): boolean {
    return !!this.slug() && !!this.name() && !this.slugError() && !this.creating();
  }

  protected nameChanged(value: string): void {
    this.name.set(value);
    if (!this.slugEdited()) this.slug.set(slugify(value));
  }

  protected slugChanged(value: string): void {
    this.slug.set(value);
    this.slugEdited.set(true);
  }

  ngOnInit(): void {
    this.meService.get().subscribe({ next: (me) => this.me.set(me), error: () => undefined });
    this.view.start();
  }

  protected openCreate(): void {
    this.name.set('');
    this.slug.set('');
    this.slugEdited.set(false);
    this.createError.set(null);
    this.showCreate.set(true);
  }

  protected closeCreate(): void {
    if (!this.showCreate()) return;
    this.showCreate.set(false);
    this.name.set('');
    this.slug.set('');
    this.slugEdited.set(false);
    this.createError.set(null);
  }

  protected reload(): void {
    this.error.set(null);
    this.view.reload();
  }

  protected create(): void {
    if (!this.canSubmit()) {
      return;
    }
    this.creating.set(true);
    this.createError.set(null);
    const slug = this.slug();
    this.service.create({ slug, name: this.name() }).subscribe({
      next: () => {
        this.creating.set(false);
        this.closeCreate();
        // Straight to its settings: a use case with no members, budget or limits is not finished.
        void this.router.navigate(['/use-cases', slug]);
      },
      error: (response: unknown) => {
        this.createError.set(errorMessage(response, 'Could not create the use case.'));
        this.creating.set(false);
      },
    });
  }
}
