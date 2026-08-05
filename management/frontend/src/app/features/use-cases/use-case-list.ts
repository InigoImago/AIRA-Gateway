import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { errorMessage } from '../../core/api/error-message';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';

/** Mirrors the server-side slug validator, so the rule is stated before the request fails. */
const SLUG_PATTERN = /^[a-z0-9-]+$/;

@Component({
  selector: 'app-use-case-list',
  imports: [FormsModule, RouterLink],
  templateUrl: './use-case-list.html',
})
export class UseCaseList implements OnInit {
  private readonly service = inject(UseCaseService);

  protected readonly useCases = signal<UseCase[]>([]);
  protected readonly loading = signal(true);
  protected readonly creating = signal(false);
  protected readonly error = signal<string | null>(null);
  // Signals, not plain fields: the app is zoneless, so clearing the form from the success
  // callback has to schedule a re-render — otherwise the inputs keep the submitted text.
  protected readonly slug = signal('');
  protected readonly name = signal('');

  /** Why the form cannot be submitted yet — shown inline instead of failing silently. */
  protected slugError(): string | null {
    if (!this.slug()) return null;
    return SLUG_PATTERN.test(this.slug()) ? null : 'Lowercase letters, digits, and hyphens only.';
  }

  protected canCreate(): boolean {
    return !!this.slug() && !!this.name() && !this.slugError() && !this.creating();
  }

  protected readonly isEmpty = computed(() => !this.loading() && this.useCases().length === 0);

  ngOnInit(): void {
    this.reload();
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
    if (!this.canCreate()) {
      return;
    }
    this.creating.set(true);
    this.service.create({ slug: this.slug(), name: this.name() }).subscribe({
      next: () => {
        this.slug.set('');
        this.name.set('');
        this.error.set(null);
        this.creating.set(false);
        this.reload();
      },
      error: (response: unknown) => {
        this.error.set(errorMessage(response, 'Could not create the use case.'));
        this.creating.set(false);
      },
    });
  }
}
