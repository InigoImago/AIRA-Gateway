import { Injectable } from '@angular/core';
import { withAccess } from './clients/access';
import { ApiClient } from './clients/base';
import { withCatalog } from './clients/catalog';
import { withIncidents } from './clients/incidents';
import { withLimits } from './clients/limits';
import { withPipelines } from './clients/pipelines';
import { withReporting } from './clients/reporting';
import { withRoles } from './clients/roles';
import { withSmokeTests } from './clients/smoke-tests';
import { withUseCases } from './clients/use-cases';

export type { Provenance } from './clients/catalog';

/**
 * The console's client for both AIRA APIs, composed from one mixin per resource (`clients/`).
 *
 * One injectable under one name, because every screen injects it and every spec stubs it by this
 * token; the calls themselves live in the resource modules.
 */
@Injectable({ providedIn: 'root' })
export class UseCaseService extends withRoles(
  withSmokeTests(
    withIncidents(
      withReporting(withCatalog(withLimits(withPipelines(withAccess(withUseCases(ApiClient)))))),
    ),
  ),
) {}
