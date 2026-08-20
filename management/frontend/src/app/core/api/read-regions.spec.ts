import { describe, expect, it } from 'vitest';

import { readRegions } from './models';

/**
 * One reader for the two spellings a model's regions can arrive in (`FRD-609`).
 *
 * `{region: "x"}` was the shape until a model could name several, so rows written before that
 * still carry it and a redelivered Kafka event can carry it after a rollback. The gateway has the
 * same reader in `ModelDeclaration.regions`; what is shared is the **format**, and the two are
 * tested against the same cases so a second spelling cannot come to mean two different things in
 * the two planes.
 */
describe('readRegions', () => {
  it('reads the list, in order', () => {
    expect(readRegions({ regions: ['europe-west1', 'europe-west4'] })).toEqual([
      'europe-west1',
      'europe-west4',
    ]);
  });

  it('reads the older single-region spelling', () => {
    expect(readRegions({ region: 'europe-west3' })).toEqual(['europe-west3']);
  });

  it('prefers the list where a row somehow carries both', () => {
    /** Not reachable through the console, and reachable through a hand-edited row or a partial
     *  migration. The list is the current shape, so it wins — and silently preferring the older
     *  one would make a model with three regions behave as though it had one. */
    expect(readRegions({ region: 'eu', regions: ['europe-west1'] })).toEqual(['europe-west1']);
  });

  it('drops blanks, trims, and keeps the first of a repeat', () => {
    expect(readRegions({ regions: ['  eu  ', '', 'eu', '   ', 'europe-west1'] })).toEqual([
      'eu',
      'europe-west1',
    ]);
  });

  it('says nothing rather than guessing about a shape it does not recognise', () => {
    /** A region list is addressing, and addressing decides where personal data is processed. Every
     *  one of these is *"we cannot tell"*, and the model is then refused by name for having no
     *  region — which is the safe direction and the one `_targets` already takes. */
    expect(readRegions(null)).toEqual([]);
    expect(readRegions(undefined)).toEqual([]);
    expect(readRegions({})).toEqual([]);
    expect(readRegions({ regions: 'europe-west1' })).toEqual(['europe-west1']);
    expect(readRegions({ regions: [1, null, {}] as unknown[] })).toEqual([]);
    expect(readRegions({ region: 42 } as Record<string, unknown>)).toEqual([]);
  });
});
