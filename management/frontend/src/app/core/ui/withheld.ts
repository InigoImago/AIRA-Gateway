/**
 * What a screen says in place of a person or a key that strict data mode withholds
 * (`AIRA_STRICT_DATA_MODE`).
 *
 * The server decides and marks the row (`person_withheld`, `target_withheld`); the console only
 * words the gap. Said rather than left blank: an empty cell reads as "nobody", which is a
 * different fact from "somebody you may not see".
 */

/** Why the name is missing, for a hover and a screen reader beside the words. */
export const WITHHELD_REASON =
  'Withheld: this installation runs strict data mode, so who made it is shown only to Global ' +
  'Administrators and IT Security.';

/** The person a request row belongs to, when the reader may not see them. */
export const ANOTHER_MEMBER = 'another member';

/** Whom a finding or a stop is about, in words — the value itself, or what kind of thing it is. */
export function targetName(row: {
  target: string;
  target_value: string | null;
  target_withheld?: boolean;
}): string {
  if (row.target_withheld) return row.target === 'credential' ? 'a key' : 'a person';
  return row.target_value ?? '—';
}

/**
 * The same, to open a sentence: "A person is stopped here." A value is left as it is — a username
 * is spelt the way it is spelt, at the start of a sentence too.
 */
export function targetAtStart(row: {
  target: string;
  target_value: string | null;
  target_withheld?: boolean;
}): string {
  const name = targetName(row);
  return row.target_withheld ? name.charAt(0).toUpperCase() + name.slice(1) : name;
}
