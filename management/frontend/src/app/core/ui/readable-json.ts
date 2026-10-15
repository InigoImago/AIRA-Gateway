/**
 * A stored request or response, as a person reads it.
 *
 * Pretty-printed JSON, with one difference that matters for prompts: a line break **inside a
 * string** is shown as a line break. JSON writes it as `\n`, so a system prompt of three hundred
 * lines arrived as one unbroken paragraph with `\n` scattered through it — readable by a parser,
 * not by somebody investigating what a model was told. Tabs are shown as tabs for the same reason.
 * Every other escape stays as JSON writes it, and an escaped backslash is never read as the start of
 * one: `\\n` is a backslash and an `n`, and stays that way.
 *
 * Display only: the text is no longer valid JSON once a string spans lines, which is why nothing
 * copies or parses it.
 */
export function readableJson(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  // One pass over the escapes, left to right, so `\\` is consumed whole before an `n` after it
  // could be mistaken for part of `\n`.
  return JSON.stringify(value, null, 2).replace(/\\(.)/g, (escape, character: string) =>
    character === 'n' ? '\n' : character === 't' ? '\t' : escape,
  );
}
