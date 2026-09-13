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
  const messages = Object.entries(details as Record<string, unknown>).map(([field, value]) => {
    const text = Array.isArray(value) ? value.join(' ') : String(value);
    return field === 'non_field_errors' ? text : `${field}: ${text}`;
  });
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
  if (body && typeof body === 'object' && 'error' in body) {
    const envelope = (body as ErrorEnvelope).error ?? {};
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
