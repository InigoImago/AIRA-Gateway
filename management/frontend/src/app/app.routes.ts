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
  // The screen's name before `ADR-0020`, kept as a redirect: it is in bookmarks and documentation.
  { path: 'model-tests', redirectTo: 'pipeline-tests', pathMatch: 'full' },
  {
    // The register of processing activities (`FRD-608`): its own route rather than a reporting tab,
    // because it answers "what is processed, on what terms" for a different audience than spend.
    path: 'register',
    canActivate: [authGuard],
    loadComponent: () => import('./features/governance/register-page').then((m) => m.RegisterPage),
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
