/**
 * Turn a failed HTTP call into a message worth showing.
 *
 * Both AIRA APIs answer with the same envelope — `{"error": {"code", "message", "details"}}` —
 * so the server's own wording is almost always more useful than a generic fallback ("Only
 * members of this use case may issue API keys." beats "Something went wrong."). Validation
 * failures additionally carry a `details` map of field → messages, which is where DRF puts the
 * specific reason, so those are folded in too.
 *
 * The two cases that need translating rather than echoing are the ones where the server has no
 * useful wording: a 0 status (the request never arrived) and an unauthenticated/forbidden
 * response with an empty body.
 */

interface ErrorEnvelope {
  error?: { code?: string; message?: string; details?: unknown };
}

/**
 * The compatibility surface's envelope (`FRD-107`): a flat `{code, message}`, not `{error: {…}}`.
 *
 * It was not handled at all, so every refusal from `/gw/kira/...` — which the connection panel
 * calls to list the models a migrating client would see — fell through to the generic fallback.
 * The server had said exactly what was wrong and the reader was shown "Something went wrong",
 * which is the failure `core/api/error-message.ts` exists to prevent: *no silent failures in the
 * UI* means the server's own wording reaches the screen.
 */
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
 * DRF's `details`: a map of field to messages.
 *
 * The compatibility surface sends a **list** of `{loc, msg}` instead — a different shape with the
 * same name. `Object.entries` over it yields `0: [object Object]`, which is worse than saying
 * nothing, so the two are read apart rather than run through one loop.
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

  // The compatibility surface's flat shape. Checked after the nested one and never instead of it:
  // both AIRA envelopes carry a `message` too, so testing for `message` first would read a
  // `{"error": {...}}` body through the wrong branch and lose its `details`.
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
