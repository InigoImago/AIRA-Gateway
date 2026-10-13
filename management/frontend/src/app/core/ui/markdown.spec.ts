import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Markdown, inlines, parseMarkdown, plainText } from './markdown';

@Component({
  selector: 'app-host',
  imports: [Markdown],
  template: `<app-markdown [source]="source()" />`,
})
class Host {
  readonly source = signal('');
}

function render(source: string): HTMLElement {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.source.set(source);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('Markdown', () => {
  it('adds no space the author did not write', () => {
    expect(render('**bold**, then `code`.').textContent?.trim()).toBe('bold, then code.');
  });

  it('renders emphasis as emphasis, not as asterisks', () => {
    // A seeded description read "**Function Calling ist eingeschaltet**" on the overview.
    const element = render('**Function Calling ist eingeschaltet** — und *bewusst* `aus`.');

    expect(element.querySelector('strong')?.textContent).toBe('Function Calling ist eingeschaltet');
    expect(element.querySelector('em')?.textContent).toBe('bewusst');
    expect(element.querySelector('code')?.textContent).toBe('aus');
    expect(element.textContent).not.toContain('*');
  });

  it('keeps paragraphs, line breaks and lists', () => {
    const element = render('First line\nsecond line\n\n- one\n- two\n\n1. first\n2. second');

    expect(element.querySelectorAll('p').length).toBe(1);
    expect(element.querySelectorAll('p br').length).toBe(1);
    expect([...element.querySelectorAll('ul li')].map((li) => li.textContent)).toEqual([
      'one',
      'two',
    ]);
    expect(element.querySelectorAll('ol li').length).toBe(2);
  });

  it('shows markup somebody typed as the characters they typed', () => {
    const element = render('<img src=x onerror="alert(1)"> <script>alert(2)</script> **<b>x</b>**');

    expect(element.querySelector('img, script, b')).toBeNull();
    expect(element.textContent).toContain('<script>alert(2)</script>');
    expect(element.querySelector('strong')?.textContent).toBe('<b>x</b>');
  });

  it('links only to web addresses', () => {
    const element = render('[docs](https://example.com/a) and [evil](javascript:alert(1))');
    const links = [...element.querySelectorAll('a')];

    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://example.com/a']);
    expect(links[0].getAttribute('rel')).toBe('noopener noreferrer');
    expect(element.textContent).toContain('[evil](javascript:alert(1))');
  });

  it('leaves an asterisk inside a word or a formula alone', () => {
    expect(inlines('2 * 3 * 4')).toEqual([{ kind: 'text', text: '2 * 3 * 4' }]);
    expect(inlines('snake_case_name')).toEqual([{ kind: 'text', text: 'snake_case_name' }]);
  });

  it('parses nothing out of nothing', () => {
    expect(parseMarkdown('')).toEqual([]);
    expect(parseMarkdown('\n\n  \n')).toEqual([]);
    expect(render('').textContent?.trim()).toBe('');
  });

  it('gives a one-line reading without the markers', () => {
    expect(plainText('**Agentic** coding\n\n- with `tools`\n- and [docs](https://x.y)')).toBe(
      'Agentic coding with tools and docs',
    );
  });

  it('reads CRLF line endings like LF', () => {
    expect(parseMarkdown('a\r\n\r\n- b')).toEqual([
      { kind: 'p', lines: [[{ kind: 'text', text: 'a' }]] },
      { kind: 'ul', items: [[{ kind: 'text', text: 'b' }]] },
    ]);
  });
});

/**
 * The inline rules as one regular expression — what `inlines` is held to.
 *
 * Not what it runs: with every opener whose closer never comes, the pattern rescans the rest of the
 * line, which is quadratic in a hostile description. What a line *means* is still defined here.
 */
const REFERENCE =
  /`([^`]+)`|\*\*(.+?)\*\*|__(.+?)__|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)|\[([^\]]+)\]\(([^)\s]+)\)/g;

function referenceInlines(line: string) {
  const out: { kind: string; text: string; href?: string }[] = [];
  let last = 0;
  for (const match of line.matchAll(REFERENCE)) {
    const at = match.index ?? 0;
    if (at > last) out.push({ kind: 'text', text: line.slice(last, at) });
    const [whole, code, strong, strongUnderscore, em, emUnderscore, linkText, href] = match;
    if (code !== undefined) out.push({ kind: 'code', text: code });
    else if (strong !== undefined || strongUnderscore !== undefined) {
      out.push({ kind: 'strong', text: strong ?? strongUnderscore });
    } else if (em !== undefined || emUnderscore !== undefined) {
      out.push({ kind: 'em', text: em ?? emUnderscore });
    } else if (/^https?:\/\//i.test(href)) {
      out.push({ kind: 'link', text: linkText, href });
    } else {
      out.push({ kind: 'text', text: whole });
    }
    last = at + whole.length;
  }
  if (last < line.length) out.push({ kind: 'text', text: line.slice(last) });
  return out;
}

describe('Markdown — what a hostile description costs', () => {
  /** Long enough that the quadratic reading took seconds; short enough to stay a unit test. */
  const LENGTH = 50_000;
  /**
   * The bound separates orders of magnitude, not milliseconds: the quadratic reading of this line
   * took 10–16 s and the linear one under 10 ms. Under coverage instrumentation and a full parallel
   * suite the linear one has measured 120 ms, so a bound near it fails for load, not for a defect.
   */
  const BOUND_MS = 1_000;

  it.each([
    ['an opening bracket that never closes', '['],
    ['an asterisk that never closes', ' *a'],
    ['an underscore that never closes', ' _a'],
  ])('reads %s in linear time', (_name, unit) => {
    // A use-case administrator writes the description and every member renders it — and the
    // register renders it for every oversight reader. A line that freezes the tab is an outage
    // anybody with that one permission can cause.
    const line = unit.repeat(Math.ceil(LENGTH / unit.length));

    const started = performance.now();
    const pieces = inlines(line);
    const elapsed = performance.now() - started;

    expect(pieces.map((piece) => piece.text).join('')).toBe(line);
    expect(elapsed).toBeLessThan(BOUND_MS);
  });

  it('reads exactly what the pattern it replaced read', () => {
    // Every rule, every lookbehind and every "the first rule that matches wins", compared on
    // thousands of short lines built from the characters the rules are made of. Seeded, so a
    // failure names a line that can be replayed.
    const alphabet = ['*', '*', '_', '_', '`', '[', ']', '(', ')', 'a', 'b', '1', ' ', '\t'];
    alphabet.push('\u00a0', '\u2028', 'https://x', 'ftp:', '-');
    let seed = 20261012;
    const random = () => {
      seed = (seed * 1_103_515_245 + 12_345) % 2_147_483_648;
      return seed / 2_147_483_648;
    };

    for (let round = 0; round < 6000; round += 1) {
      const length = Math.floor(random() * 14);
      const line = Array.from(
        { length },
        () => alphabet[Math.floor(random() * alphabet.length)],
      ).join('');
      expect(inlines(line), JSON.stringify(line)).toEqual(referenceInlines(line));
    }
  });
});
