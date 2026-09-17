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
