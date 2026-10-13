import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, input } from '@angular/core';

/**
 * The part of Markdown a use case's description is written in — paragraphs, `-` and `1.` lists,
 * `**bold**`, `*italic*`, `` `code` `` and `[links](https://…)` — rendered as elements, never as
 * HTML.
 *
 * **Parsed into a tree the template walks**, not converted to a string for `innerHTML`: text the
 * console did not write never becomes markup, so `<script>` in a description is shown as the
 * characters it is. A link keeps its text only when its target is `http` or `https`.
 */

export type Inline =
  | { kind: 'text' | 'strong' | 'em' | 'code'; text: string }
  | { kind: 'link'; text: string; href: string };

export type Block = { kind: 'p'; lines: Inline[][] } | { kind: 'ul' | 'ol'; items: Inline[][] };

const BULLET = /^\s*[-*]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;

/** A line without any of these has no inline markup to find. */
const MARKERS = /[`*_[]/;
/** What emphasis never spans — the characters a regular expression's `.` refuses to cross. */
const LINE_BREAK = /[\n\r\u2028\u2029]/;
const SPACE = /\s/;
const WORD = /\w/;

/**
 * For every position, the first position at or after it where `holds` is true, or `length`.
 *
 * Built in one pass from the end, so "where does this opener close" costs one lookup. Scanning
 * forward from each opener instead is quadratic in a line of openers that never close: a
 * description of `[[[[…` would rescan the rest of the line at every bracket and freeze the tab of
 * everybody who opens the use case — and of the register's oversight readers with it.
 */
function nextWhere(length: number, holds: (at: number) => boolean): (from: number) => number {
  const table = new Int32Array(length + 1);
  table[length] = length;
  for (let at = length - 1; at >= 0; at -= 1) table[at] = holds(at) ? at : table[at + 1];
  return (from) => (from >= length ? length : table[from]);
}

/**
 * The inline pieces of one line: `code`, then **strong**, then *em* or _em_, then [text](url) — the
 * first rule that matches at a position wins, and the scan resumes after it.
 *
 * Linear in the length of the line whatever it holds. An emphasis opener needs a non-space after
 * it and no letter or `*` before it, its closer the reverse, so `2 * 3 * 4` and `snake_case` stay
 * text.
 */
export function inlines(line: string): Inline[] {
  const length = line.length;
  if (!MARKERS.test(line)) return length ? [{ kind: 'text', text: line }] : [];

  const char = (at: number): string => (at >= 0 && at < length ? line[at] : '');
  const space = (at: number): boolean => SPACE.test(char(at));
  const word = (at: number): boolean => WORD.test(char(at));

  const backtick = nextWhere(length, (at) => line[at] === '`');
  const lineBreak = nextWhere(length, (at) => LINE_BREAK.test(line[at]));
  const bracket = nextWhere(length, (at) => line[at] === ']');
  // A link target ends at its `)` — or at a space, which no target may contain.
  const targetEnd = nextWhere(length, (at) => line[at] === ')' || space(at));
  const double = {
    '*': nextWhere(length, (at) => line[at] === '*' && line[at + 1] === '*'),
    _: nextWhere(length, (at) => line[at] === '_' && line[at + 1] === '_'),
  };
  // A closer has no space before it, and no letter after it — nor a `*`, after an asterisk.
  const closer = {
    '*': nextWhere(
      length,
      (at) => line[at] === '*' && !space(at - 1) && !word(at + 1) && char(at + 1) !== '*',
    ),
    _: nextWhere(length, (at) => line[at] === '_' && !space(at - 1) && !word(at + 1)),
  };

  /** The piece that starts at `at`, and where it ends — or `null` when no rule matches there. */
  const pieceAt = (at: number): { piece: Inline; end: number } | null => {
    const marker = line[at];
    if (marker === '`') {
      const close = backtick(at + 1);
      return close < length && close > at + 1
        ? { piece: { kind: 'code', text: line.slice(at + 1, close) }, end: close + 1 }
        : null;
    }
    if (marker === '*' || marker === '_') {
      if (line[at + 1] === marker) {
        // `**x**`: at least one character inside, on one line.
        const close = double[marker](at + 3);
        if (close < length && lineBreak(at + 2) >= close) {
          return { piece: { kind: 'strong', text: line.slice(at + 2, close) }, end: close + 2 };
        }
      }
      const opens =
        !word(at - 1) &&
        (marker === '_' || char(at - 1) !== '*') &&
        at + 1 < length &&
        !space(at + 1);
      if (opens) {
        const close = closer[marker](at + 2);
        if (close < length && lineBreak(at + 1) >= close) {
          return { piece: { kind: 'em', text: line.slice(at + 1, close) }, end: close + 1 };
        }
      }
      return null;
    }
    if (marker === '[') {
      const close = bracket(at + 1);
      if (close >= length || close === at + 1 || line[close + 1] !== '(') return null;
      const end = targetEnd(close + 2);
      if (end >= length || end === close + 2 || line[end] !== ')') return null;
      const href = line.slice(close + 2, end);
      const text = line.slice(at + 1, close);
      // A target that is not a web address — `javascript:`, a relative path — stays text.
      const piece: Inline = /^https?:\/\//i.test(href)
        ? { kind: 'link', text, href }
        : { kind: 'text', text: line.slice(at, end + 1) };
      return { piece, end: end + 1 };
    }
    return null;
  };

  const out: Inline[] = [];
  let last = 0;
  for (let at = 0; at < length;) {
    const found = pieceAt(at);
    if (!found) {
      at += 1;
      continue;
    }
    if (at > last) out.push({ kind: 'text', text: line.slice(last, at) });
    out.push(found.piece);
    last = at = found.end;
  }
  if (last < length) out.push({ kind: 'text', text: line.slice(last) });
  return out;
}

/** Blocks separated by blank lines; a block whose every line is a list item is a list. */
export function parseMarkdown(source: string): Block[] {
  const blocks: Block[] = [];
  for (const chunk of source.replace(/\r\n?/g, '\n').split(/\n\s*\n/)) {
    const lines = chunk.split('\n').filter((line) => line.trim());
    if (!lines.length) continue;
    if (lines.every((line) => BULLET.test(line))) {
      blocks.push({ kind: 'ul', items: lines.map((line) => inlines(line.replace(BULLET, ''))) });
    } else if (lines.every((line) => NUMBERED.test(line))) {
      blocks.push({ kind: 'ol', items: lines.map((line) => inlines(line.replace(NUMBERED, ''))) });
    } else {
      blocks.push({ kind: 'p', lines: lines.map((line) => inlines(line.trim())) });
    }
  }
  return blocks;
}

/** The words without the markers, for a place that shows one line of it: a table cell, a title. */
export function plainText(source: string): string {
  return parseMarkdown(source)
    .flatMap((block) => (block.kind === 'p' ? block.lines : block.items))
    .map((line) => line.map((piece) => piece.text).join(''))
    .join(' ');
}

@Component({
  selector: 'app-markdown',
  imports: [NgTemplateOutlet],
  host: { class: 'markdown' },
  template: `
    <ng-template #line let-pieces>
      @for (piece of pieces; track $index) {
        @switch (piece.kind) {
          @case ('strong') {
            <strong>{{ piece.text }}</strong>
          }
          @case ('em') {
            <em>{{ piece.text }}</em>
          }
          @case ('code') {
            <code>{{ piece.text }}</code>
          }
          @case ('link') {
            <a [href]="piece.href" target="_blank" rel="noopener noreferrer">{{ piece.text }}</a>
          }
          @default {
            <!-- In an element of its own: text between control-flow lines keeps a space, and
                 "**bold**, next" would read "bold , next". -->
            <span>{{ piece.text }}</span>
          }
        }
      }
    </ng-template>
    @for (block of blocks(); track $index) {
      @switch (block.kind) {
        @case ('p') {
          <p>
            @for (pieces of block.lines; track $index) {
              @if ($index) {
                <br />
              }
              <ng-container *ngTemplateOutlet="line; context: { $implicit: pieces }" />
            }
          </p>
        }
        @case ('ul') {
          <ul>
            @for (pieces of block.items; track $index) {
              <li><ng-container *ngTemplateOutlet="line; context: { $implicit: pieces }" /></li>
            }
          </ul>
        }
        @case ('ol') {
          <ol>
            @for (pieces of block.items; track $index) {
              <li><ng-container *ngTemplateOutlet="line; context: { $implicit: pieces }" /></li>
            }
          </ol>
        }
      }
    }
  `,
})
export class Markdown {
  readonly source = input('');
  protected readonly blocks = computed(() => parseMarkdown(this.source()));
}
