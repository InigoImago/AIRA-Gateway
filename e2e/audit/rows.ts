/**
 * What takes part in a row's alignment — **one definition** for the audit's `ragged-row` and the
 * layout tests' `expectFormControlsAligned`, which are two measurements of the same property.
 *
 * Two definitions would drift apart, and then one measurement reports a radio group in an inline
 * form ragged — 13 px beside 42 px controls — while the other passes it.
 */

/**
 * Not a row control at all: a checkbox or a radio is sized by its glyph, and anything inside a
 * `<label>` — an info hint's trigger, a wrapped choice — sits at label height by construction.
 */
export const NOT_A_ROW_CONTROL = 'input[type=checkbox], input[type=radio], label *';

/** Small on purpose: compared by where it starts, never by its height. */
export const SMALL_BY_DESIGN = '.btn--sm';
