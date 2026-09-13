/**
 * What a named period means as a `[from, to)` pair of local days (`FRD-603`).
 *
 * Shared by the reporting screen and a use case's consumption panel, because both rules below are
 * off-by-one bugs that only appear at certain hours or on certain days.
 */

/** A period a person actually asks about, rather than two dates they have to compute. */
export type Preset =
  'today' | 'this-month' | 'last-month' | 'last-7-days' | 'last-30-days' | 'custom';

/**
 * A day as an `<input type="date">` writes it, in **local** time.
 *
 * Not `toISOString().slice(0, 10)`: that converts to UTC first, so east of Greenwich "today" is
 * yesterday for part of the day.
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
    // Not in the reporting picker: the consumption panel shows it beside the month, so "burning
    // through it right now" and "spent since the first" are two figures.
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
