import { saveBlob } from './save-blob';

describe('saveBlob', () => {
  afterEach(() => vi.restoreAllMocks());

  it('saves the blob under the name it is given, then releases it', () => {
    /** A URL that is never revoked pins the blob for the life of the page, and one revoked before
     *  the click downloads nothing — so the order is the property, not only the calls. */
    const blob = new Blob(['a,b\n1,2\n'], { type: 'text/csv' });
    const order: string[] = [];
    const created = vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      order.push('create');
      return 'blob:export';
    });
    const revoked = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {
      order.push('revoke');
    });
    let clicked: HTMLAnchorElement | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      order.push('click');
      clicked = this;
    });

    saveBlob(blob, 'aira-usage_model_2026-10-01_2026-10-31.csv');

    expect(created).toHaveBeenCalledWith(blob);
    expect(revoked).toHaveBeenCalledWith('blob:export');
    expect(order).toEqual(['create', 'click', 'revoke']);
    expect(clicked!.href).toBe('blob:export');
    expect(clicked!.download).toBe('aira-usage_model_2026-10-01_2026-10-31.csv');
  });
});
