import { CatalogModel } from '../../core/api/models';
import { detailOf } from './catalog-display';

describe('detailOf', () => {
  it('shows when a model last changed on the installation’s clock', () => {
    // 10:00 UTC is noon in Berlin in August (`FRD-629`), whatever zone the browser is in.
    const model = {
      name: 'chat-1',
      approved: true,
      provider: 'vertex',
      capabilities: ['generate'],
      updated_at: '2026-08-09T10:00:00Z',
    } as unknown as CatalogModel;

    const changed = (zone?: string) =>
      detailOf(model, 'EUR', zone).find((field) => field.key === 'updated_at')?.value;

    expect(changed('Europe/Berlin')).toBe('2026-08-09 12:00');
    expect(changed()).toBe('2026-08-09 10:00');
  });
});
