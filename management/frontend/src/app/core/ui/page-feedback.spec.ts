import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { PageFeedback } from './page-feedback';

function setup(): PageFeedback {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [PageFeedback] });
  return TestBed.inject(PageFeedback);
}

const httpError = (status: number, body?: unknown) =>
  throwError(() => ({ status, error: body ?? { error: { message: 'from the backend' } } }));

describe('PageFeedback', () => {
  it('reports a success and clears any previous failure', () => {
    const feedback = setup();
    feedback.fail({ status: 500 }, 'it broke');
    expect(feedback.error()).toBeTruthy();

    feedback.run(of('done'), { failure: 'never', success: () => feedback.succeed('Saved.') });

    expect(feedback.notice()).toBe('Saved.');
    expect(feedback.error()).toBeNull();
    expect(feedback.busy()).toBe(false);
  });

  it('prefers what the backend said over the caller guess', () => {
    // The caller cannot know why a request failed; the envelope can. A generic fallback that
    // overrides a specific reason turns a fixable problem into a mystery.
    const feedback = setup();
    let ran = false;

    feedback.run(httpError(400), { failure: 'Could not save.', success: () => (ran = true) });

    expect(feedback.error()).toBe('from the backend');
    expect(ran).toBe(false);
  });

  it('falls back to the caller wording when the response carries none', () => {
    const feedback = setup();
    feedback.run(httpError(500, {}), { failure: 'Could not save.', success: () => undefined });
    expect(feedback.error()).toBe('Could not save.');
  });

  it('clears busy on failure as well as on success', () => {
    // A control left disabled after a failed save is one the user cannot retry with.
    const feedback = setup();
    feedback.run(httpError(500), { failure: 'no', success: () => undefined });
    expect(feedback.busy()).toBe(false);
  });

  it('drops the previous outcome the moment a new mutation starts', () => {
    const feedback = setup();
    feedback.succeed('Saved.');
    feedback.run(httpError(500), { failure: 'Could not save.', success: () => undefined });
    expect(feedback.notice()).toBeNull();
  });
});
