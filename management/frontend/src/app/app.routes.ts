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
    path: 'pipeline-tests',
    canActivate: [authGuard],
    loadComponent: () => import('./features/smoketests/smoke-tests').then((m) => m.SmokeTests),
  },
  // The screen was "model tests" until `ADR-0020` made a run about a use case's pipeline. The old
  // path stays as a redirect rather than being deleted: it is in people's bookmarks and in the
  // documentation, and a 404 for a screen that moved teaches a reader that the console is unstable.
  { path: 'model-tests', redirectTo: 'pipeline-tests', pathMatch: 'full' },
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
