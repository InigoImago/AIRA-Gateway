/**
 * Hand the reader a file the console fetched — an export, a run's results, a key file.
 *
 * Fetched as a blob through the API client rather than offered as a plain link, because a link
 * carries no bearer token and would be answered with a 401 (`FRD-602`).
 *
 * The object URL is released as soon as the anchor has been clicked: an object URL held open pins
 * the whole blob in memory for the life of the page, and an export can be megabytes.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
