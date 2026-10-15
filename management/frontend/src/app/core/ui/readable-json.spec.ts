import { readableJson } from './readable-json';

describe('readableJson', () => {
  it('shows a line break inside a string as a line break', () => {
    const text = readableJson({ systemInstruction: 'first line\nsecond line\n\tindented' });

    expect(text).toContain('first line\nsecond line\n\tindented');
    expect(text).not.toContain('\\n');
  });

  it('leaves an escaped backslash before an n as a backslash and an n', () => {
    // The string holds a backslash followed by `n` — a Windows path, a regex — not a line break.
    const text = readableJson({ path: 'C:\\new\\folder' });

    expect(text).toContain('C:\\\\new\\\\folder');
    expect(text.split('\n')).toHaveLength(3);
  });

  it('keeps every other escape as JSON writes it', () => {
    expect(readableJson({ quote: 'say "hi"' })).toContain('"say \\"hi\\""');
  });

  it('passes text through and shows nothing for nothing', () => {
    expect(readableJson('already text\nwith a break')).toBe('already text\nwith a break');
    expect(readableJson(null)).toBe('');
    expect(readableJson(undefined)).toBe('');
  });

  it('is still pretty-printed JSON around the strings', () => {
    expect(readableJson({ a: [1] })).toBe('{\n  "a": [\n    1\n  ]\n}');
  });
});
