/**
 * The installation's clock (`AIRA_TIME_ZONE`, `FRD-629`) as this console shows it.
 *
 * Every timestamp is printed on it and every named period begins on it — never on the reader's
 * browser, whose zone is not the one a budget resets on: a request at 00:30 must sit beside the
 * budget day it was counted in. The zone reaches the console through `/v1/me`
 * ({@link InstallationClock}); before that answers, the clock is UTC, the one an installation that
 * sets nothing counts on.
 *
 * A moment is formatted by the browser's own time-zone database (`Intl.DateTimeFormat`), which
 * knows the days the clocks change. A **calendar date** — `2026-10-28`, no time — names a day
 * rather than a moment, and is shown as it is: read as a moment it would be midnight UTC, the
 * previous day anywhere west of it.
 */

import { Injectable, signal } from '@angular/core';

/** The clock before `/v1/me` has answered, and the one an installation that sets nothing is on. */
export const DEFAULT_CLOCK = 'UTC';

/**
 * Which clock the console is on, as one signal every screen reads.
 *
 * `MeService` sets it from `/v1/me`; everything that shows a time reads it here rather than from
 * `MeService`, because showing a time needs no HTTP client — and a screen whose only use of the
 * account is a timestamp should not need one to render.
 */
@Injectable({ providedIn: 'root' })
export class InstallationClock {
  /** An IANA name such as `Europe/Berlin`, or `UTC` until `/v1/me` has answered. */
  readonly zone = signal(DEFAULT_CLOCK);
}

/** How much of a moment is shown: its date, to the minute, or to the second. */
export type Precision = 'day' | 'minute' | 'second';

/** A calendar date without a time. */
const CALENDAR_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** One formatter per zone: building one is the expensive part, and a table formats hundreds. */
const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(zone: string): Intl.DateTimeFormat {
  let found = formatters.get(zone);
  if (!found) {
    found = new Intl.DateTimeFormat('en-GB', {
      timeZone: zone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      // `h23`, not `hour12: false`: some engines write midnight as `24:00` under the latter.
      hourCycle: 'h23',
    });
    formatters.set(zone, found);
  }
  return found;
}

/** The wall-clock parts of `moment` on `zone`. */
function partsOf(moment: Date, zone: string): Record<string, string> {
  const parts: Record<string, string> = {};
  for (const part of formatter(zone).formatToParts(moment)) {
    parts[part.type] = part.value;
  }
  return parts;
}

/**
 * ``value`` on the installation's clock — `2026-10-28 14:05`, or its date or seconds — or `''` for
 * nothing. A value the browser cannot read as a moment is shown as nothing rather than as
 * `Invalid Date`.
 */
export function inZone(
  value: string | number | Date | null | undefined,
  zone: string,
  precision: Precision = 'minute',
): string {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'string' && CALENDAR_DATE.test(value)) return value;
  const moment = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(moment.getTime())) return '';
  const part = partsOf(moment, zone || DEFAULT_CLOCK);
  const day = `${part['year']}-${part['month']}-${part['day']}`;
  if (precision === 'day') return day;
  const minute = `${day} ${part['hour']}:${part['minute']}`;
  return precision === 'second' ? `${minute}:${part['second']}` : minute;
}

/** The date ``moment`` falls on, on ``zone``'s clock, as `YYYY-MM-DD` — what the gateway reads a
 *  bare date as. */
export function dayIn(moment: Date, zone: string): string {
  return inZone(moment, zone, 'day');
}
