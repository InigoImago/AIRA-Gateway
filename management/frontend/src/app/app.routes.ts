import { Routes } from '@angular/router';
import { authGuard } from './core/auth/auth.guard';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'use-cases' },
  {
    path: 'use-cases',
    canActivate: [authGuard],
    loadComponent: () => import('./features/use-cases/use-case-list').then((m) => m.UseCaseList),
  },
  {
    path: 'use-cases/:slug',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/use-cases/use-case-detail').then((m) => m.UseCaseDetail),
  },
];
