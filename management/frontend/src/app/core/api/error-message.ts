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

interface HttpErrorLike {
  status?: number;
  error?: ErrorEnvelope | string | null;
}

function fromDetails(details: unknown): string | null {
  if (!details || typeof details !== 'object') return null;
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

  if (failure.status === 401) {
    return 'Your session is not valid (any more). Reload the page to sign in again.';
  }
  if (failure.status === 403) {
    return 'You do not have permission to do that.';
  }
  return fallback;
}
