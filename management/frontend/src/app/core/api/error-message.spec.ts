import { errorMessage } from './error-message';

describe('errorMessage', () => {
  it('prefers the server envelope message over the fallback', () => {
    const response = {
      status: 403,
      error: { error: { code: 'permission_denied', message: 'Only members may issue API keys.' } },
    };
    expect(errorMessage(response, 'fallback')).toBe('Only members may issue API keys.');
  });

  it('appends field details from a validation error', () => {
    const response = {
      status: 400,
      error: {
        error: {
          code: 'invalid_argument',
          message: 'Request failed.',
          details: { slug: ['Use lowercase letters, digits, and hyphens only.'] },
        },
      },
    };
    expect(errorMessage(response, 'fallback')).toBe(
      'Request failed. slug: Use lowercase letters, digits, and hyphens only.',
    );
  });

  it('uses details alone when there is no message', () => {
    const response = { status: 400, error: { error: { details: { period: 'is required' } } } };
    expect(errorMessage(response, 'fallback')).toBe('period: is required');
  });

  it('does not prefix non-field errors with a field name', () => {
    const response = {
      status: 400,
      error: { error: { details: { non_field_errors: ['Set at least one limit.'] } } },
    };
    expect(errorMessage(response, 'fallback')).toBe('Set at least one limit.');
  });

  it('explains an unreachable server rather than echoing status 0', () => {
    expect(errorMessage({ status: 0 }, 'fallback')).toContain('could not be reached');
  });

  it('explains 401 and 403 when the body carries nothing useful', () => {
    expect(errorMessage({ status: 401, error: null }, 'fallback')).toContain('session');
    expect(errorMessage({ status: 403, error: 'plain text' }, 'fallback')).toContain('permission');
  });

  it('falls back for anything it cannot interpret', () => {
    expect(errorMessage({ status: 500 }, 'Could not save.')).toBe('Could not save.');
    expect(errorMessage(undefined, 'Could not save.')).toBe('Could not save.');
    expect(errorMessage({ status: 400, error: { error: {} } }, 'Could not save.')).toBe(
      'Could not save.',
    );
  });
});
