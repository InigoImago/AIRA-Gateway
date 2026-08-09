/**
 * What a named period means as a `[from, to)` pair of local days.
 *
 * Extracted from the reporting screen when a second screen needed the same answer (`FRD-603`).
 * It is a small thing to share and an expensive one to restate: the two rules below are both
 * off-by-one bugs that only appear at certain hours or on certain days, which is to say the kind
 * nobody reproduces from a bug report.
 */

/** A period a person actually asks about, rather than two dates they have to compute. */
export type Preset =
  'today' | 'this-month' | 'last-month' | 'last-7-days' | 'last-30-days' | 'custom';

/**
 * A day as an `<input type="date">` writes it, in **local** time.
 *
 * Deliberately not `toISOString().slice(0, 10)`: that converts to UTC first, so for anyone east
 * of Greenwich "today" becomes yesterday for part of the day — an off-by-one in the period the
 * report covers, which is the kind of bug that is only ever noticed in the evening.
 */
export function isoDay(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** The `[from, to)` pair a preset means, as local days. `to` is exclusive throughout. */
export function windowFor(preset: Preset, today: Date): { from: string; to: string } {
  const day = (offset: number) =>
    new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset);
  switch (preset) {
    // Not offered by the reporting screen's picker — it is what a use case's own consumption
    // panel asks for beside the month, so that "we are burning through it right now" and "we
    // have spent this much since the first" are two figures rather than one.
    case 'today':
      return { from: isoDay(day(0)), to: isoDay(day(1)) };
    case 'last-month': {
      const first = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      return {
        from: isoDay(first),
        to: isoDay(new Date(today.getFullYear(), today.getMonth(), 1)),
      };
    }
    case 'last-7-days':
      return { from: isoDay(day(-6)), to: isoDay(day(1)) };
    case 'last-30-days':
      return { from: isoDay(day(-29)), to: isoDay(day(1)) };
    default: {
      const first = new Date(today.getFullYear(), today.getMonth(), 1);
      return {
        from: isoDay(first),
        to: isoDay(new Date(today.getFullYear(), today.getMonth() + 1, 1)),
      };
    }
  }
}
