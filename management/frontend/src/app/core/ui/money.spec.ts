import { displayAmount, displayCount } from './money';

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
