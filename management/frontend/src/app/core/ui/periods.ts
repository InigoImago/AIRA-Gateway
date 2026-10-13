/**
 * What a named period means as a `[from, to)` pair of **UTC** days (`FRD-601`, `FRD-603`).
 *
 * UTC because every figure these windows select is counted in UTC: a budget or a per-person
 * allowance resets at UTC midnight and on the first of the UTC month, the gateway buckets its
 * series in UTC, and it reads a bare date as a UTC day. A window taken from the reader's own
 * calendar is a different window from the one the gateway counts against — after midnight east of
 * Greenwich, "today" would be a day that has not started for the counter that refuses. So the
 * window is the gateway's, and the screens say which clock it is on ({@link PERIOD_CLOCK}).
 *
 * Shared by the reporting screen, the register and a use case's overview, because the rules below
 * are off-by-one bugs that only appear at certain hours or on certain days.
 */

/** The clock every period, bucket and budget on this console is counted in — for a label. */
export const PERIOD_CLOCK = 'UTC';

/** A period a person actually asks about, rather than two dates they have to compute. */
export type Preset =
  'today' | 'this-month' | 'last-month' | 'last-7-days' | 'last-30-days' | 'custom';

/**
 * A calendar date written the way an `<input type="date">` writes it, from a `Date` built out of
 * local parts — `new Date(year, month, day)` — so the date is the one that was constructed.
 *
 * For calendar arithmetic on a date the reader typed. Not for "now": the day it is *now* is the
 * UTC day ({@link utcDay}), the one the gateway counts.
 */
export function isoDay(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** The UTC day a moment falls on, as `YYYY-MM-DD` — what the gateway reads a bare date as. */
export function utcDay(moment: Date): string {
  return moment.toISOString().slice(0, 10);
}

/** The `[from, to)` pair a preset means at `now`, as UTC days. `to` is exclusive throughout. */
export function windowFor(preset: Preset, now: Date): { from: string; to: string } {
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth();
  const date = now.getUTCDate();
  const day = (offset: number) => utcDay(new Date(Date.UTC(year, month, date + offset)));
  const firstOf = (offset: number) => utcDay(new Date(Date.UTC(year, month + offset, 1)));
  switch (preset) {
    // Not in the reporting picker: the consumption panel shows it beside the month, so "burning
    // through it right now" and "spent since the first" are two figures.
    case 'today':
      return { from: day(0), to: day(1) };
    case 'last-month':
      return { from: firstOf(-1), to: firstOf(0) };
    // Today included: a report of "the last 7 days" that stopped at midnight would omit what
    // happened today, which is what people look at first.
    case 'last-7-days':
      return { from: day(-6), to: day(1) };
    case 'last-30-days':
      return { from: day(-29), to: day(1) };
    default:
      return { from: firstOf(0), to: firstOf(1) };
  }
}

/**
 * A `[from, to)` window of days as a period screen holds it — reporting and the register
 * (`FRD-601`, `FRD-608`) — so a window is checked and read out the same way on both.
 */
export interface DayWindow {
  from: string;
  to: string;
}

/**
 * What is wrong with a typed window, in the reader's words, or `null` when nothing is.
 *
 * Checked before anything is asked: a window that ends before it starts is refused by the gateway
 * with a 400, and a Show button that produces one is a control that reports a fault it caused.
 */
export function windowError(window: DayWindow): string | null {
  if (!window.from || !window.to) return 'Choose a start and an end date.';
  if (window.to <= window.from) return 'The end date must be after the start date.';
  return null;
}

/**
 * The last day a window includes — the day before its exclusive end — which is how a person reads
 * a period and how a file for it is named. `''` for no end.
 */
export function lastDayOf(to: string): string {
  if (!to) return '';
  const [year, month, day] = to.split('-').map(Number);
  return isoDay(new Date(year, month - 1, day - 1));
}

/** A window as a person reads it — first and last day included, and the clock they are days on —
 *  or `''` before there is one. */
export function periodText(window: DayWindow | null): string {
  return window ? `${window.from} to ${lastDayOf(window.to)} (${PERIOD_CLOCK})` : '';
}
