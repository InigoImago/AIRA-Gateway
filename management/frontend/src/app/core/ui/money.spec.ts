import { displayAmount, displayCount, nanosOf, storedAmount } from './money';

/**
 * The client-side half of `aira_common.money.format_display`.
 *
 * The cases are the ones the Python side is tested on, side by side with it, because a chart that
 * adds nano-units up has to write the sum the way the server writes its rows — otherwise one screen
 * shows `0.00` and the row beside it shows `0.0004`.
 */
describe('displayAmount', () => {
  it('writes an ordinary amount to two places', () => {
    expect(displayAmount(1_500_000_000)).toBe('1.50');
    expect(displayAmount(12_000_000_000)).toBe('12.00');
  });

  it('writes zero as zero', () => {
    expect(displayAmount(0)).toBe('0.00');
  });

  it('never shows a non-zero amount as zero', () => {
    // The whole reason this function is not `toFixed(2)`. A demo use case, or any low-volume one,
    // spends fractions of a cent — and `0.00` says nothing was spent.
    expect(displayAmount(3_674_900)).toBe('0.0037');
    expect(displayAmount(1_000)).toBe('0.000001');
    expect(displayAmount(1)).toBe('0.000000001');
  });

  it('rounds half-up, the way an invoice does', () => {
    expect(displayAmount(1_005_000_000)).toBe('1.01');
    expect(displayAmount(1_004_999_999)).toBe('1.00');
  });

  it('keeps a large amount exact', () => {
    // A month of real traffic reaches this magnitude, and `nanos / 1e9` is where it would start
    // drifting. The arithmetic here is on the digits, so it does not.
    expect(displayAmount(123_456_789_000_000)).toBe('123456.79');
  });

  it('writes a negative amount with its sign', () => {
    // No exported column is legitimately negative, which is exactly why this is pinned: a sign lost
    // in formatting turns a correction into a charge.
    expect(displayAmount(-1_500_000_000)).toBe('-1.50');
  });
});

describe('displayCount', () => {
  it('groups thousands so a figure is read rather than counted', () => {
    expect(displayCount(12840)).toBe('12,840');
    expect(displayCount(7)).toBe('7');
  });
});

describe('nanosOf', () => {
  it('reads a decimal the server sent without passing it through a float', () => {
    expect(nanosOf('250.000000')).toBe(250_000_000_000);
    expect(nanosOf('0.1')).toBe(100_000_000);
    // 1.005 is 1.00499999… as a float; on the digits it is exactly what it says.
    expect(nanosOf('1.005')).toBe(1_005_000_000);
    expect(nanosOf('7')).toBe(7_000_000_000);
    expect(nanosOf('-1.5')).toBe(-1_500_000_000);
  });

  it('drops what lies below a nano-unit', () => {
    expect(nanosOf('0.0000000019')).toBe(1);
  });

  it('refuses what is not a plain decimal, rather than guessing zero', () => {
    expect(nanosOf('')).toBeNull();
    expect(nanosOf('abc')).toBeNull();
    expect(nanosOf('1e3')).toBeNull();
  });

  it('writes a stored limit the way every other amount here is written', () => {
    // "0.00 / 250.000000" printed the limit as stored, beside a figure formatted to two places.
    expect(displayAmount(nanosOf('250.000000') ?? 0)).toBe('250.00');
  });
});

describe('storedAmount', () => {
  it('shows a configured amount as it was set: every significant decimal, at least two', () => {
    expect(storedAmount('250.000000')).toBe('250.00');
    expect(storedAmount('0.010000')).toBe('0.01');
    // Rounded to two places this read 0.13 — a limit nobody chose.
    expect(storedAmount('0.125000')).toBe('0.125');
    expect(storedAmount('0.000001')).toBe('0.000001');
    expect(storedAmount('20')).toBe('20.00');
  });

  it('hands back nothing for what is not a plain decimal, so no zero is guessed', () => {
    expect(storedAmount('')).toBeNull();
    expect(storedAmount('20,00')).toBeNull();
  });
});
