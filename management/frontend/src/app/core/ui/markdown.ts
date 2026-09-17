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

/** `code`, then **strong**, then *em* or _em_, then [text](url) — the first that matches wins. */
const INLINE =
  /`([^`]+)`|\*\*(.+?)\*\*|__(.+?)__|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)|\[([^\]]+)\]\(([^)\s]+)\)/g;

/** The inline pieces of one line. */
export function inlines(line: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const match of line.matchAll(INLINE)) {
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
      // A target that is not a web address — `javascript:`, a relative path — stays text.
      out.push({ kind: 'text', text: whole });
    }
    last = at + whole.length;
  }
  if (last < line.length) out.push({ kind: 'text', text: line.slice(last) });
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
