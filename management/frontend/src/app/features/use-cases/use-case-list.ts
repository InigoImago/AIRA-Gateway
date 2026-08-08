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
import { TablePager } from '../../core/ui/table-pager';
import { TableView } from '../../core/ui/table-view';

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

  protected readonly useCases = signal<UseCase[]>([]);

  /**
   * The list, searched and paged.
   *
   * Not a nicety: a live round found **801** use cases in one installation, which made this
   * overview unusable without a single line of it being wrong. A list that only grows needs a way
   * to ask for the row you want — the name *or* the technical id, since one is what a person
   * calls it and the other is what their systems quote.
   */
  protected readonly view = new TableView<UseCase>(
    this.useCases,
    (useCase) => `${useCase.name} ${useCase.slug}`,
  );
  protected readonly loading = signal(true);
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

  protected readonly isEmpty = computed(() => !this.loading() && this.useCases().length === 0);

  ngOnInit(): void {
    this.meService.get().subscribe({ next: (me) => this.me.set(me), error: () => undefined });
    this.reload();
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
    this.loading.set(true);
    this.service.list().subscribe({
      next: (list) => {
        this.useCases.set(list);
        this.error.set(null);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.error.set(errorMessage(response, 'Failed to load use cases.'));
        this.loading.set(false);
      },
    });
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
