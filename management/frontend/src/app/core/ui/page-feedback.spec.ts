import { TestBed } from '@angular/core/testing';
import { EMPTY, Subject, of, throwError } from 'rxjs';
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

  it('stays busy until every overlapping mutation has settled', () => {
    // Two panels saving at once: the first to finish re-enabled the other's controls while its
    // request was still in flight, which is a double submit waiting to happen.
    const feedback = setup();
    const first = new Subject<string>();
    const second = new Subject<string>();
    feedback.run(first, { failure: 'no', success: () => undefined });
    feedback.run(second, { failure: 'no', success: () => undefined });

    first.next('done');
    expect(feedback.busy()).toBe(true);

    second.error({ status: 500 });
    expect(feedback.busy()).toBe(false);
  });

  it('is not left busy by a request that completes without an answer', () => {
    // Nothing handled completion: an empty answer left every control on the page disabled.
    const feedback = setup();
    feedback.run(EMPTY, { failure: 'no', success: () => undefined });
    expect(feedback.busy()).toBe(false);
  });

  it('lets a recovered load withdraw its own failure, and only its own', () => {
    // A live view polls: one failed poll must not leave a banner claiming the failure after the
    // next poll has answered — and must not clear somebody else's.
    const feedback = setup();
    const said = feedback.fail({ status: 502 }, 'Could not load the requests.');
    expect(feedback.error()).toBe(said);

    feedback.withdraw(said);
    expect(feedback.error()).toBeNull();

    feedback.fail({ status: 400 }, 'Could not save the budget.');
    feedback.withdraw(said);
    expect(feedback.error()).toBe('Could not save the budget.');
  });
});
