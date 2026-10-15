import { targetAtStart, targetName } from './withheld';

describe('targetName / targetAtStart (`FRD-627` FR-8)', () => {
  it('words a withheld person and a withheld key, and leaves a value as it is', () => {
    expect(targetName({ target: 'subject', target_value: null, target_withheld: true })).toBe(
      'a person',
    );
    expect(targetName({ target: 'credential', target_value: null, target_withheld: true })).toBe(
      'a key',
    );
    expect(targetName({ target: 'subject', target_value: 'ada' })).toBe('ada');
    // A row with nothing to name and nothing withheld says so, rather than "null".
    expect(targetName({ target: 'subject', target_value: null })).toBe('—');
  });

  it('capitalises only the words it chose, never a username', () => {
    expect(targetAtStart({ target: 'subject', target_value: null, target_withheld: true })).toBe(
      'A person',
    );
    expect(targetAtStart({ target: 'subject', target_value: 'ada' })).toBe('ada');
  });
});
