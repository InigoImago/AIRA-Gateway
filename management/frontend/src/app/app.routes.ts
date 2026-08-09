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
  {
    path: 'models',
    canActivate: [authGuard],
    loadComponent: () => import('./features/models/model-catalog').then((m) => m.ModelCatalog),
  },
  {
    path: 'reporting',
    canActivate: [authGuard],
    loadComponent: () => import('./features/reporting/reporting-page').then((m) => m.ReportingPage),
  },
  {
    path: 'requests',
    canActivate: [authGuard],
    loadComponent: () => import('./features/requests/requests-page').then((m) => m.RequestsPage),
  },
  {
    path: 'model-tests',
    canActivate: [authGuard],
    loadComponent: () => import('./features/smoketests/smoke-tests').then((m) => m.SmokeTests),
  },
  {
    path: 'security',
    canActivate: [authGuard],
    loadComponent: () => import('./features/security/security-page').then((m) => m.SecurityPage),
  },
  {
    path: 'use-cases/:slug/pipeline',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/pipelines/pipeline-editor').then((m) => m.PipelineEditor),
  },
];
