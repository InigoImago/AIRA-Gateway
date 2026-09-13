/** Mirrors the server-side slug validator, so the rule is stated before the request fails. */
export const SLUG_PATTERN = /^[a-z0-9-]+$/;

/**
 * A name, as a technical id.
 *
 * Umlauts are transliterated rather than stripped: dropping them turns "Prüfung" into "prfung".
 */
export function slugify(name: string): string {
  const folded = name
    .toLowerCase()
    .replace(/ä/g, 'ae')
    .replace(/ö/g, 'oe')
    .replace(/ü/g, 'ue')
    .replace(/ß/g, 'ss')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
  return folded
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
}
