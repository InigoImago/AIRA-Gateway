import { Pipe, PipeTransform, inject } from '@angular/core';
import { InstallationClock, Precision, inZone } from './clock';

/**
 * A moment on the installation's clock (`FRD-629`): `{{ row.created_at | zoned }}` prints
 * `2026-10-28 14:05`, `| zoned: 'second'` adds the seconds and `| zoned: 'day'` keeps the date.
 *
 * In place of Angular's `date` pipe, which knows the browser's zone and fixed offsets but not an
 * IANA name, and so cannot follow a clock that changes in summer.
 *
 * **Impure**, because the zone is a signal that arrives with `/v1/me`: a pure pipe keeps the answer
 * it gave for the same value, so a timestamp rendered before `/v1/me` answered would stay in UTC.
 * The formatter is cached per zone (`clock.ts`), which is what makes re-running it cheap.
 */
@Pipe({ name: 'zoned', pure: false })
export class ZonedPipe implements PipeTransform {
  private readonly clock = inject(InstallationClock).zone;

  transform(
    value: string | number | Date | null | undefined,
    precision: Precision = 'minute',
  ): string {
    return inZone(value, this.clock(), precision);
  }
}
