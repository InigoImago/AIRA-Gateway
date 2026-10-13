import { copyText } from './clipboard';

describe('copyText', () => {
  const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  const install = (clipboard: unknown) =>
    Object.defineProperty(navigator, 'clipboard', { value: clipboard, configurable: true });
  afterEach(() => {
    if (original) Object.defineProperty(navigator, 'clipboard', original);
    else delete (navigator as unknown as { clipboard?: unknown }).clipboard;
  });

  it('writes the text and says so', async () => {
    const written: string[] = [];
    install({ writeText: async (text: string) => void written.push(text) });

    expect(await copyText('aira_ab12_secret')).toBe(true);
    expect(written).toEqual(['aira_ab12_secret']);
  });

  it('says no when the browser refuses', async () => {
    install({ writeText: () => Promise.reject(new Error('denied')) });
    expect(await copyText('x')).toBe(false);
  });

  it('says no where there is no clipboard at all', async () => {
    install(undefined);
    expect(await copyText('x')).toBe(false);
  });
});
