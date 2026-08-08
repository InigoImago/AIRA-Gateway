import {
  Component,
  ElementRef,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { errorMessage } from '../../core/api/error-message';
import { Me, UseCase } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ServerTableView } from '../../core/ui/server-table-view';
import { TablePager } from '../../core/ui/table-pager';

/** Mirrors the server-side slug validator, so the rule is stated before the request fails. */
const SLUG_PATTERN = /^[a-z0-9-]+$/;

/**
 * A name, as a technical id.
 *
 * People should not have to know what a slug is to create a use case, and the ones who do should
 * not have to type the same thing twice. Umlauts are transliterated rather than stripped: dropping
 * them turns "Prüfung" into "prfung", which reads as a typo forever after.
 */
export function slugify(name: string): string {
  const folded = name
    .toLowerCase()
    .replace(/ä/g, 'ae')
    .replace(/ö/g, 'oe')
    .replace(/ü/g, 'ue')
    .replace(/ß/g, 'ss')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '');
  return folded
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
}

@Component({
  selector: 'app-use-case-list',
  imports: [FormsModule, RouterLink, TablePager],
  templateUrl: './use-case-list.html',
  host: { '(document:keydown.escape)': 'closeCreate()' },
})
export class UseCaseList implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly router = inject(Router);

  /**
   * The list, searched and paged **at the server** (`FRD-208`).
   *
   * Not a nicety: a live round found **801** use cases in one installation, which made this
   * overview unusable without a single line of it being wrong. And not client-side either — this
   * endpoint computes object-level permissions per row, so fetching everything and slicing it in
   * the browser leaves every one of those computations happening on every load. The reader waits
   * exactly as long and then sees twenty-five rows.
   *
   * Searchable by name *or* technical id: one is what a person calls it, the other is what their
   * systems quote, and somebody arriving from a log line has only the second.
   */
  protected readonly view = new ServerTableView<UseCase>(
    (query, page) => this.service.listPage(query, page),
    (response) => this.error.set(errorMessage(response, 'Failed to load use cases.')),
  );
  protected readonly useCases = this.view.rows;
  /** Whether a page is in flight. Owned by the view, since it is the thing doing the fetching. */
  protected readonly loading = this.view.loading;
  protected readonly creating = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly me = signal<Me | null>(null);
  protected readonly showCreate = signal(false);
  // Signals, not plain fields: the app is zoneless, so clearing the form from the success
  // callback has to schedule a re-render — otherwise the inputs keep the submitted text.
  protected readonly slug = signal('');
  protected readonly name = signal('');
  /** Whether the id was typed by hand. Once it was, the name stops overwriting it. */
  private readonly slugEdited = signal(false);
  private readonly dialog = viewChild<ElementRef<HTMLElement>>('dialog');

  constructor() {
    effect(() => {
      if (this.showCreate()) this.dialog()?.nativeElement.focus();
    });
  }

  /**
   * Who may create one. Offering the action to somebody the backend will refuse is the same
   * defect as showing member controls to a reader: the console says yes and the server says no.
   */
  protected readonly canCreate = computed(() => {
    const roles = this.me()?.roles ?? [];
    return roles.includes('global-admin') || roles.includes('use-case-admin');
  });

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

  /** Nothing at all — as opposed to nothing *matching*, which the search says in its own words. */
  protected readonly isEmpty = computed(
    () => !this.loading() && this.view.total() === 0 && !this.view.filtered(),
  );

  ngOnInit(): void {
    this.meService.get().subscribe({ next: (me) => this.me.set(me), error: () => undefined });
    this.view.start();
  }

  protected openCreate(): void {
    this.name.set('');
    this.slug.set('');
    this.slugEdited.set(false);
    this.showCreate.set(true);
  }

  protected closeCreate(): void {
    if (!this.showCreate()) return;
    this.showCreate.set(false);
    this.name.set('');
    this.slug.set('');
    this.slugEdited.set(false);
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
    const slug = this.slug();
    this.service.create({ slug, name: this.name() }).subscribe({
      next: () => {
        this.error.set(null);
        this.creating.set(false);
        this.closeCreate();
        // Straight to its settings: a use case with no members, no budget and no limits is not
        // finished, and returning to the list is what makes it look like it is.
        void this.router.navigate(['/use-cases', slug]);
      },
      error: (response: unknown) => {
        this.error.set(errorMessage(response, 'Could not create the use case.'));
        this.creating.set(false);
      },
    });
  }
}
