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

  it("reads the compatibility surface's flat envelope too", () => {
    // `/gw/kira/...` answers `{code, message}` rather than `{error: {…}}` (`FRD-107`). It was not
    // handled at all, so the server's own wording was replaced by the generic fallback on the one
    // surface whose entire contract is its error shape.
    const response = {
      status: 422,
      error: { code: 'MODEL_NOT_FOUND', message: 'No model with id 4711.' },
    };
    expect(errorMessage(response, 'fallback')).toBe('No model with id 4711.');
  });

  it('still prefers the nested envelope when a body carries both shapes', () => {
    // Both AIRA envelopes carry a `message`, so a flat-first check would read a nested body
    // through the wrong branch and drop its `details`.
    const response = {
      status: 400,
      error: {
        code: 'invalid_argument',
        message: 'flat',
        error: { message: 'nested', details: { period: 'is required' } },
      },
    };
    expect(errorMessage(response, 'fallback')).toBe('nested period: is required');
  });

  it("reads the compatibility surface's validation list rather than stringifying it", () => {
    // `{loc, msg}` entries, not a field map. Run through the DRF loop they come out as
    // `0: [object Object]`, which is worse than the fallback it replaced.
    const response = {
      status: 422,
      error: {
        code: 'VALIDATION_ERROR',
        message: 'Request validation failed.',
        details: [{ loc: ['model_id'], msg: 'Field required' }],
      },
    };
    expect(errorMessage(response, 'fallback')).toBe(
      'Request validation failed. model_id: Field required',
    );
  });

  it('uses a compatibility detail alone when there is no message', () => {
    const response = {
      status: 422,
      error: { code: 'VALIDATION_ERROR', details: [{ msg: 'nope' }] },
    };
    expect(errorMessage(response, 'fallback')).toBe('nope');
  });

  it('reads a malformed detail entry rather than dropping it', () => {
    // A `details` list is whatever the server sent. An entry that is not an object, or one with no
    // `loc`, still says something — and the alternative is the generic fallback, which says less.
    const bare = { status: 422, error: { code: 'X', details: ['plain string'] } };
    const noLoc = { status: 422, error: { code: 'X', details: [{ msg: 'no field named' }] } };
    const noMsg = { status: 422, error: { code: 'X', details: [{ loc: ['field'] }] } };
    expect(errorMessage(bare, 'fallback')).toBe('plain string');
    expect(errorMessage(noLoc, 'fallback')).toBe('no field named');
    expect(errorMessage(noMsg, 'fallback')).toBe('field: ');
  });

  it('falls back for a compatibility envelope that says nothing', () => {
    expect(errorMessage({ status: 500, error: { code: 'INTERNAL_SERVER_ERROR' } }, 'x')).toBe('x');
    expect(errorMessage({ status: 422, error: { code: 'X', details: [] } }, 'x')).toBe('x');
    expect(errorMessage({ status: 422, error: { code: 'X', details: ['  '] } }, 'x')).toBe('x');
  });

  it('falls back for anything it cannot interpret', () => {
    expect(errorMessage({ status: 500 }, 'Could not save.')).toBe('Could not save.');
    expect(errorMessage(undefined, 'Could not save.')).toBe('Could not save.');
    expect(errorMessage({ status: 400, error: { error: {} } }, 'Could not save.')).toBe(
      'Could not save.',
    );
  });

  it('names an answer that is not the API, rather than echoing a parser', () => {
    // A 2xx that is not JSON — an SSO proxy's login page, an HTML fallback — reaches the caller as
    // `{error: SyntaxError, text}`. Its `message` is the parser's ("Unexpected token '<'…").
    const response = {
      status: 200,
      error: {
        error: new SyntaxError('Unexpected token \'<\', "<!doctype "... is not valid JSON'),
        text: '<!doctype html>',
      },
    };
    const said = errorMessage(response, 'Could not load the use cases.');

    expect(said).not.toMatch(/Unexpected token|not valid JSON/);
    expect(said).toContain('not the AIRA API');
  });

  it('takes no error object for an envelope', () => {
    const response = { status: 500, error: { error: new Error('internal detail') } };
    expect(errorMessage(response, 'Could not save.')).toBe('Could not save.');
  });

  it('spells out nested validation details instead of printing [object Object]', () => {
    const response = {
      status: 400,
      error: {
        error: {
          message: 'Request failed.',
          details: {
            permissions: [{ name: ['This field is required.'] }],
            addressing: { regions: ['Unknown region.'] },
          },
        },
      },
    };
    const said = errorMessage(response, 'fallback');

    expect(said).not.toContain('[object Object]');
    expect(said).toBe(
      'Request failed. permissions.name: This field is required. addressing.regions: Unknown region.',
    );
  });
});
