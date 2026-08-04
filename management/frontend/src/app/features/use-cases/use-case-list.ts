import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';

@Component({
  selector: 'app-use-case-list',
  imports: [FormsModule, RouterLink],
  templateUrl: './use-case-list.html',
})
export class UseCaseList implements OnInit {
  private readonly service = inject(UseCaseService);

  protected readonly useCases = signal<UseCase[]>([]);
  protected readonly error = signal<string | null>(null);
  protected slug = '';
  protected name = '';

  ngOnInit(): void {
    this.reload();
  }

  protected reload(): void {
    this.service.list().subscribe({
      next: (list) => this.useCases.set(list),
      error: () => this.error.set('Failed to load use cases.'),
    });
  }

  protected create(): void {
    if (!this.slug || !this.name) {
      return;
    }
    this.service.create({ slug: this.slug, name: this.name }).subscribe({
      next: () => {
        this.slug = '';
        this.name = '';
        this.error.set(null);
        this.reload();
      },
      error: () => this.error.set('Could not create the use case.'),
    });
  }
}
