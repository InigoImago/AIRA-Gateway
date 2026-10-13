/**
 * Nano-units rendered for a reader, without a monetary figure passing through a float.
 *
 * The backend sends every amount **twice** (`FRD-403`): an exact decimal string for a human and an
 * integer in nano-units for a bar to divide. A screen that only ever shows a figure the server
 * formatted needs neither of these functions — and a chart has to **add figures up**, per bucket and
 * per band, which is where the client acquires an amount nobody formatted for it.
 *
 * So this is `aira_common.money.format_display` in TypeScript, including the part that is easy to
 * leave out: **a non-zero amount is never shown as zero.** `0.00` says nothing was spent, and in a
 * demonstration or a low-volume use case that is exactly the figure a reader would otherwise take
 * away from a month that cost a fraction of a cent.
 */

/** Nano-units in one unit of the currency. The same constant both planes use. */
const NANOS_PER_UNIT_DIGITS = 9;

/** The precisions tried, in order, until the amount stops reading as zero. Mirrors the backend. */
const ESCALATION = [4, 6, 9];

/**
 * An amount in nano-units as a decimal string.
 *
 * Rounded half-up at `places`, like an invoice — then at more places if, and only if, that would
 * otherwise print a non-zero amount as `0.00`.
 */
export function displayAmount(nanos: number, places = 2): string {
  const rendered = fixed(nanos, places);
  if (nanos === 0 || Number(rendered) !== 0) return rendered;
  for (const extra of ESCALATION) {
    const candidate = fixed(nanos, extra);
    if (Number(candidate) !== 0) return candidate;
  }
  return rendered;
}

/**
 * `nanos` at exactly `places` decimals — no escalation.
 *
 * `displayAmount` refines until a figure stops reading as zero, which is the right rule for one
 * amount and the wrong one for a **scale**: an axis whose ticks were each refined on their own
 * printed `0.0002 / 0.0001 / 0.0001 / 0.000041` on the showcase. A scale needs one precision for all
 * of its marks, chosen so they differ, and that is the caller's question to ask.
 */
export function amountAt(nanos: number, places: number): string {
  return fixed(nanos, places);
}

/**
 * `nanos` as a decimal string with exactly `places` decimals, rounded half-up.
 *
 * Done on the **digits**, not by dividing: `nanos / 1e9` is a float, and the one rule money
 * arithmetic has here is that it never becomes one (`FRD-403`).
 */
function fixed(nanos: number, places: number): string {
  const negative = nanos < 0;
  // Padded to ten digits so there is always at least one unit digit in front of the nine nano ones.
  const digits = String(Math.abs(Math.trunc(nanos))).padStart(NANOS_PER_UNIT_DIGITS + 1, '0');
  const split = digits.length - NANOS_PER_UNIT_DIGITS;
  const units = digits.slice(0, split);
  const fraction = digits.slice(split);

  // The amount as one integer scaled to `places` decimals, carried by one where the next digit
  // rounds up. String comparison on a single character: `'5' <= c <= '9'` is `c >= '5'`.
  const scaled = Number(`${units}${fraction.slice(0, places)}`) + (fraction[places] >= '5' ? 1 : 0);

  const text = String(scaled).padStart(places + 1, '0');
  const point = text.length - places;
  const body = places === 0 ? text : `${text.slice(0, point)}.${text.slice(point)}`;
  return negative ? `-${body}` : body;
}

/**
 * A decimal amount as the server writes it — a limit's `'250.000000'` — in nano-units, read on the
 * **digits**: `Number('0.1') * 1e9` is a float, and the one rule money arithmetic has here is that
 * it never becomes one. Digits past the ninth decimal are below a nano-unit and dropped. `null` for
 * anything that is not a plain decimal, so a caller shows "unknown" rather than a guessed zero.
 */
export function nanosOf(amount: string): number | null {
  const match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(amount.trim());
  if (!match) return null;
  const [, sign, units, fraction = ''] = match;
  const nanos = Number(
    units + fraction.slice(0, NANOS_PER_UNIT_DIGITS).padEnd(NANOS_PER_UNIT_DIGITS, '0'),
  );
  return sign && nanos !== 0 ? -nanos : nanos;
}

/**
 * A stored amount — a limit the server writes as `'250.000000'` — as it was set: every significant
 * decimal, and at least two. A configured figure is never rounded for display, because `0.125`
 * shown as `0.13` is a limit nobody chose. `null` for anything that is not a plain decimal, so the
 * caller shows what it was given rather than a guessed zero.
 */
export function storedAmount(amount: string): string | null {
  const nanos = nanosOf(amount);
  if (nanos === null) return null;
  const significant = (amount.trim().split('.')[1] ?? '').replace(/0+$/, '').length;
  return displayAmount(nanos, Math.min(NANOS_PER_UNIT_DIGITS, Math.max(2, significant)));
}

/**
 * A count with thousands separators, so `12840` is read rather than counted.
 *
 * `en-US` explicitly, not the browser's locale: the console is English throughout (`ADR-0001`), and
 * a figure grouped one way in a chart and another way in the table beside it looks like two figures.
 */
export function displayCount(value: number): string {
  return value.toLocaleString('en-US');
}
