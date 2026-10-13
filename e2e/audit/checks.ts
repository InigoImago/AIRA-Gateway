import AxeBuilder from '@axe-core/playwright';
import { Page } from '@playwright/test';
import { NOT_A_ROW_CONTROL, SMALL_BY_DESIGN } from './rows';

/** One thing the audit found, in one state of the console. */
export interface Finding {
  check: string;
  severity: 'high' | 'medium' | 'low';
  message: string;
  target: string;
}

/**
 * Checks that read the rendered page and need no opinion. Each returns findings rather than
 * throwing: the audit's job is to list everything, not to stop at the first.
 *
 * `scope` narrows every check to an open window, whose backdrop otherwise makes the page behind
 * it look covered.
 */
export async function layoutChecks(page: Page, scope: string, phone: boolean): Promise<Finding[]> {
  return page.evaluate(
    ({ scope, phone, exempt, small }) => {
      const found: {
        check: string;
        severity: 'high' | 'medium' | 'low';
        message: string;
        target: string;
      }[] = [];
      const root = document.querySelector(scope) ?? document.body;

      const describe = (el: Element): string => {
        const testid = el.getAttribute('data-testid');
        const id = el.id ? `#${el.id}` : '';
        const cls =
          typeof el.className === 'string' && el.className.trim()
            ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.')
            : '';
        const text = (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 40);
        return `${el.tagName.toLowerCase()}${testid ? `[data-testid=${testid}]` : id || cls}${text ? ` "${text}"` : ''}`;
      };
      const visible = (el: Element): boolean => {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style.visibility !== 'hidden' &&
          style.display !== 'none'
        );
      };

      // The page is wider than the screen: everything scrolls sideways.
      if (document.documentElement.scrollWidth > window.innerWidth + 1) {
        const wide = Array.from(document.querySelectorAll('body *'))
          .filter((el) => visible(el) && el.getBoundingClientRect().right > window.innerWidth + 1)
          .filter((el) => !el.closest('.table-wrap, pre, code'))
          .slice(-1)[0];
        found.push({
          check: 'horizontal-overflow',
          severity: 'high',
          message: `page is ${document.documentElement.scrollWidth}px wide in a ${window.innerWidth}px viewport`,
          target: wide ? describe(wide) : 'document',
        });
      }

      // A value the template did not have, printed as a word.
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const parent = node.parentElement;
        if (!parent || parent.closest('pre, code, textarea, script, style') || !visible(parent))
          continue;
        const text = node.textContent ?? '';
        const junk = text.match(/\bundefined\b|\bNaN\b|\[object Object\]|^\s*null\s*$|\{\{|\}\}/);
        if (junk) {
          found.push({
            check: 'rendered-junk',
            severity: 'high',
            message: `renders "${junk[0].trim()}"`,
            target: describe(parent),
          });
        }
      }

      // A table column squeezed so narrow its words break apart: three lines or more, and more lines
      // than words. "Whole / use / case" is wrapping; "en / twi / ckl" is a word broken apart.
      // Lines are counted from the text's own boxes: a cell is as tall as its row, so its height
      // says nothing about its own text.
      for (const cell of Array.from(root.querySelectorAll('td, th')).filter(visible)) {
        // `innerText`, not `textContent`: a code and the line below it are two words, not one.
        const text = (cell as HTMLElement).innerText.trim().replace(/\s+/g, ' ');
        if (text.length < 8) continue;
        // Text nodes only: a range over the cell also returns a box per child element.
        const tops = new Set<number>();
        const texts = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        for (let node = texts.nextNode(); node; node = texts.nextNode()) {
          if (!(node.textContent ?? '').trim()) continue;
          const range = document.createRange();
          range.selectNodeContents(node);
          for (const box of Array.from(range.getClientRects())) {
            if (box.width > 0 && box.height > 0) tops.add(Math.round(box.top));
          }
        }
        const lines = tops.size;
        // Hyphens, underscores and slashes are break points too: `pen- / global` is wrapping.
        const words = text.split(/[\s\-_/]+/).filter(Boolean).length;
        if (lines >= 3 && lines > words) {
          found.push({
            check: 'squeezed-column',
            severity: 'high',
            message: `${cell.clientWidth}px wide: ${text.length} characters over ${lines} lines`,
            target: describe(cell),
          });
        }
      }

      const controls = Array.from(
        root.querySelectorAll(
          'button, a[href], input:not([type=hidden]), select, textarea, [role=tab]',
        ),
      ).filter(visible);

      for (const el of controls) {
        const rect = el.getBoundingClientRect();
        // Cut-off text in a control, a badge or a header that does not say it is cut off.
        const style = getComputedStyle(el);
        if (
          (el.tagName === 'BUTTON' || el.getAttribute('role') === 'tab') &&
          el.scrollWidth > el.clientWidth + 1 &&
          style.textOverflow !== 'ellipsis'
        ) {
          found.push({
            check: 'clipped-text',
            severity: 'medium',
            message: 'label is wider than its control',
            target: describe(el),
          });
        }
        // Something else sits on top of the control's centre: it cannot be clicked there.
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        if (x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight) {
          const top = document.elementFromPoint(x, y);
          if (top && top !== el && !el.contains(top) && !top.contains(el)) {
            found.push({
              check: 'covered-control',
              severity: 'high',
              message: `covered by ${describe(top)}`,
              target: describe(el),
            });
          }
        }
        // WCAG 2.2 2.5.8: a target at least 24 × 24 px. Links inside running text are exempt.
        if (
          phone &&
          el.tagName !== 'A' &&
          (rect.width < 24 || rect.height < 24) &&
          el.getAttribute('type') !== 'checkbox' &&
          el.getAttribute('type') !== 'radio'
        ) {
          found.push({
            check: 'small-target',
            severity: 'low',
            message: `${Math.round(rect.width)}×${Math.round(rect.height)}px, below 24×24`,
            target: describe(el),
          });
        }
      }

      // Two controls overlapping each other.
      for (let i = 0; i < controls.length; i++) {
        for (let j = i + 1; j < controls.length; j++) {
          const a = controls[i];
          const b = controls[j];
          if (a.contains(b) || b.contains(a)) continue;
          const ra = a.getBoundingClientRect();
          const rb = b.getBoundingClientRect();
          const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
          const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
          if (w > 2 && h > 2) {
            found.push({
              check: 'overlapping-controls',
              severity: 'high',
              message: `overlaps ${describe(b)}`,
              target: describe(a),
            });
          }
        }
      }

      // A tab strip wider than its column: the tab past the edge is a section nobody can see. The
      // use-case detail's eight tabs were 18px too wide at 1440px and every walk passed — the strip
      // scrolls inside itself, so the page never overflowed and nothing here looked.
      for (const strip of Array.from(root.querySelectorAll('.tabs, [role=tablist]')).filter(
        visible,
      )) {
        if (strip.scrollWidth > strip.clientWidth + 1) {
          found.push({
            check: 'hidden-tab',
            severity: 'medium',
            message: `tabs need ${strip.scrollWidth}px in a ${strip.clientWidth}px strip`,
            target: describe(strip),
          });
        }
      }

      // Controls side by side that are not one height: a text field beside a select and a button
      // measured 42, 39 and 39px, a row ragged along its bottom edge. Lines are found the way the
      // browser lays them out — boxes that overlap vertically — and what is not a row control, or
      // is small on purpose, is left out by the rule `expectFormControlsAligned` shares
      // (`audit/rows.ts`). A window built on `<app-modal>` projects its buttons through a
      // `[modal-foot]` wrapper that has no box of its own, so they are one level further down.
      const rows = root.querySelectorAll(
        '.form-inline, .filter-row, .row, .actions, .spread, .multiselect__field, .modal__foot',
      );
      for (const row of Array.from(rows).filter(visible)) {
        const members = Array.from(
          row.querySelectorAll(
            ':scope > input, :scope > select, :scope > .btn, :scope > .field > input, ' +
              ':scope > .field > select, :scope > [modal-foot] > .btn',
          ),
        )
          .filter(visible)
          .filter((el) => !el.matches(exempt) && !el.matches(small));
        const lines: { top: number; bottom: number; boxes: { el: Element; height: number }[] }[] =
          [];
        for (const el of members) {
          const box = el.getBoundingClientRect();
          const line = lines.find((l) => box.top < l.bottom && box.bottom > l.top);
          const entry = { el, height: Math.round(box.height) };
          if (line) {
            line.top = Math.min(line.top, box.top);
            line.bottom = Math.max(line.bottom, box.bottom);
            line.boxes.push(entry);
          } else {
            lines.push({ top: box.top, bottom: box.bottom, boxes: [entry] });
          }
        }
        for (const line of lines) {
          const heights = line.boxes.map((b) => b.height);
          if (line.boxes.length > 1 && Math.max(...heights) - Math.min(...heights) > 1) {
            found.push({
              check: 'ragged-row',
              severity: 'low',
              message: `controls of ${heights.join(', ')}px on one line`,
              target: line.boxes.map((b) => describe(b.el)).join(' | '),
            });
          }
        }
      }

      // Prose squeezed into a column of single words outside a table — a field, a hint or a note
      // given no width of its own. The access panel's search field measured 53px wide and 379px
      // tall on a phone, its hint one word per line; the table check above cannot see it.
      // The text is the block's own, read without a select's options: a field around a select
      // carried every option's label, so the length gate let any narrow select through. And one
      // squeezed field is one finding — its label and hint inside it are the same squeeze.
      const ownText = (block: Element): string => {
        const copy = block.cloneNode(true) as Element;
        copy.querySelectorAll('select, option, datalist').forEach((el) => el.remove());
        return (copy.textContent ?? '').trim();
      };
      const squeezed: Element[] = [];
      for (const block of Array.from(
        root.querySelectorAll('.field, .field__hint, .callout, p, label'),
      ).filter(visible)) {
        if (block.closest('td, th')) continue;
        if (squeezed.some((outer) => outer.contains(block))) continue;
        const box = block.getBoundingClientRect();
        if (ownText(block).length > 20 && box.width < 120 && box.height > box.width * 2) {
          squeezed.push(block);
          found.push({
            check: 'squeezed-text',
            severity: 'high',
            message: `${Math.round(box.width)}px wide and ${Math.round(box.height)}px tall`,
            target: describe(block),
          });
        }
      }
      return found;
    },
    { scope, phone, exempt: NOT_A_ROW_CONTROL, small: SMALL_BY_DESIGN },
  );
}

/** Keyboard focus that cannot be seen: Tab moves somewhere and nothing on screen says where. */
export async function focusChecks(page: Page, scope: string): Promise<Finding[]> {
  const found: Finding[] = [];
  await page.evaluate((scope) => {
    const root = document.querySelector(scope) as HTMLElement | null;
    (root ?? document.body).focus?.();
  }, scope);
  const seen = new Set<string>();
  for (let step = 0; step < 15; step++) {
    await page.keyboard.press('Tab');
    const result = await page.evaluate((scope) => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body || !el.closest(scope)) return null;
      const focused = getComputedStyle(el);
      const outline = focused.outlineStyle !== 'none' && parseFloat(focused.outlineWidth) > 0;
      const ring = focused.boxShadow && focused.boxShadow !== 'none';
      const text = (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 40);
      const name = `${el.tagName.toLowerCase()}${el.getAttribute('data-testid') ? `[data-testid=${el.getAttribute('data-testid')}]` : ''}${text ? ` "${text}"` : ''}`;
      return { name, visible: outline || !!ring };
    }, scope);
    if (!result || seen.has(result.name)) continue;
    seen.add(result.name);
    if (!result.visible) {
      found.push({
        check: 'focus-not-visible',
        severity: 'medium',
        message: 'no outline or ring when focused by keyboard',
        target: result.name,
      });
    }
  }
  return found;
}

/** WCAG 2.2 AA through axe-core. */
export async function accessibilityChecks(page: Page, scope: string): Promise<Finding[]> {
  const builder = new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .include(scope);
  const results = await builder.analyze();
  return results.violations.flatMap((violation) =>
    violation.nodes.slice(0, 5).map((node) => ({
      check: `a11y:${violation.id}`,
      severity:
        violation.impact === 'critical' || violation.impact === 'serious' ? 'high' : 'medium',
      // axe's own sentence names the ratio and both colours, which is what a fix needs.
      message: `${violation.help}: ${(node.any[0]?.message ?? '').slice(0, 160)}`,
      target: `${node.target.join(' ')}`,
    })),
  );
}
