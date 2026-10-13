/** Both AIRA APIs' error envelope: `{"error": {"code", "message", "details"}}`. */
interface ErrorEnvelope {
  error?: { code?: string; message?: string; details?: unknown };
}

/** The compatibility surface's flat envelope (`FRD-107`): `{code, message}`, not `{error: {…}}`. */
interface KiraEnvelope {
  code?: string;
  message?: string;
  details?: unknown;
}

interface HttpErrorLike {
  status?: number;
  error?: ErrorEnvelope | KiraEnvelope | string | null;
}

/**
 * Said for a successful status whose body is not JSON — what an SSO proxy's login page or an HTML
 * error page from something between the browser and AIRA looks like to the client.
 */
const NOT_THE_API =
  'The server answered with something that is not the AIRA API — typically a sign-in or error ' +
  'page from something in between. Reload the page; if it stays, the proxy in front of AIRA needs ' +
  'a look.';

/** A server-written envelope: a plain object. An `Error` is something the client made up. */
function isEnvelope(
  value: unknown,
): value is { code?: string; message?: string; details?: unknown } {
  return !!value && typeof value === 'object' && !Array.isArray(value) && !(value instanceof Error);
}

/** `field: text`, or the text alone where there is no field to name. */
const labelled = (field: string, text: string) => (field ? `${field}: ${text}` : text);

/**
 * A DRF detail tree as `field.sub: message` entries. A nested serializer nests its errors — a list
 * of objects, or an object of lists — and printing those whole is `[object Object]`.
 */
function flatten(value: unknown, field: string): string[] {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) {
    const texts = value.filter((item) => item === null || typeof item !== 'object').map(String);
    const nested = value
      .filter((item) => item !== null && typeof item === 'object')
      .flatMap((item) => flatten(item, field));
    return [...(texts.length ? [labelled(field, texts.join(' '))] : []), ...nested];
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).flatMap(([key, inner]) =>
      flatten(inner, key === 'non_field_errors' ? field : field ? `${field}.${key}` : key),
    );
  }
  return [labelled(field, String(value))];
}

/**
 * `details` in either shape: DRF's map of field → messages, or the compatibility surface's list of
 * `{loc, msg}`. Read apart, because one loop over both prints `0: [object Object]`.
 */
function fromDetails(details: unknown): string | null {
  if (!details || typeof details !== 'object') return null;
  if (Array.isArray(details)) {
    const listed = details
      .map((entry) => {
        if (!entry || typeof entry !== 'object') return String(entry);
        const { loc, msg } = entry as { loc?: unknown; msg?: unknown };
        const where = Array.isArray(loc) ? loc.join('.') : '';
        return where ? `${where}: ${String(msg ?? '')}` : String(msg ?? '');
      })
      .filter((text) => text.trim());
    return listed.length ? listed.join(' ') : null;
  }
  const messages = flatten(details, '');
  return messages.length ? messages.join(' ') : null;
}

/**
 * Turn a failed HTTP call into a message worth showing.
 *
 * The server's own wording — message and validation details — beats a generic fallback, so every
 * load and mutation in the console reports through here. Only a 0 status (the request never
 * arrived) and an empty 401/403 are translated rather than echoed.
 */
export function errorMessage(response: unknown, fallback: string): string {
  const failure = (response ?? {}) as HttpErrorLike;

  if (failure.status === 0) {
    return 'The server could not be reached. Check that it is running and try again.';
  }

  const body = failure.error;
  const status = failure.status ?? 0;
  // A 2xx the client could not parse arrives as `{error: SyntaxError, text}`; the parser's message
  // ("Unexpected token '<'…") is no answer for a reader.
  if (
    status >= 200 &&
    status < 300 &&
    body &&
    typeof body === 'object' &&
    (body as { error?: unknown }).error instanceof Error
  ) {
    return NOT_THE_API;
  }

  if (body && typeof body === 'object' && 'error' in body && isEnvelope(body.error)) {
    const envelope = body.error as NonNullable<ErrorEnvelope['error']>;
    const detail = fromDetails(envelope.details);
    if (envelope.message && detail) return `${envelope.message} ${detail}`;
    if (envelope.message) return envelope.message;
    if (detail) return detail;
  }

  // The flat shape, checked after the nested one and never instead of it: both carry a `message`,
  // and testing for that first would read `{"error": {...}}` through the wrong branch.
  if (body && typeof body === 'object' && !('error' in body) && 'code' in body) {
    const kira = body as KiraEnvelope;
    const detail = fromDetails(kira.details);
    if (kira.message && detail) return `${kira.message} ${detail}`;
    if (kira.message) return kira.message;
    if (detail) return detail;
  }

  if (failure.status === 401) {
    return 'Your session is not valid (any more). Reload the page to sign in again.';
  }
  if (failure.status === 403) {
    return 'You do not have permission to do that.';
  }
  return fallback;
}
