/**
 * Write `text` to the clipboard, and say whether that worked.
 *
 * It can fail in two ways a panel must tell the reader about: outside a secure context there is no
 * clipboard at all, and a browser may refuse the permission. Neither is an error of the console's,
 * so it is a `false` to word, not an exception to catch at every call site.
 */
export async function copyText(text: string): Promise<boolean> {
  const board = typeof navigator === 'undefined' ? undefined : navigator.clipboard;
  if (!board) return false;
  try {
    await board.writeText(text);
    return true;
  } catch {
    return false;
  }
}
