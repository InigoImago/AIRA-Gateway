import { Component, computed, input, signal } from '@angular/core';
import { displayCount } from '../money';
import { Day } from './usage-series';

/**
 * How many days are listed before the rest are behind a button.
 *
 * Enough that the question *what did I do this week* is answered without clicking, few enough that
 * a card with thirty of them does not bury the model release panel under it.
 */
const SHOWN = 7;

/**
 * The period day by day: what was used, by what, and what it cost (`FRD-626` FR-16).
 *
 * The third view of one series, and the one that answers a sentence the other two cannot:
 * *on the twelfth I made fourteen calls on this model and four on that one, and together they cost
 * me this much.* A column says **how much** against the tallest day; the blocks say **what the whole
 * is made of**; this says **what happened on a day**, with requests, tokens and money on one line.
 *
 * **All three measures at once, whichever the chart is drawing.** Switching the picture to Requests
 * must not take the money off this list — the money is half the sentence somebody came to read.
 *
 * It is a real table, visible by default. That is also what satisfies the relief the palette owes
 * (three of its hues sit below 3:1 against this surface): the figures are text on the screen, not a
 * view somebody has to know to ask for.
 */
@Component({
  selector: 'app-usage-days',
  templateUrl: './usage-days.html',
})
export class UsageDays {
  readonly days = input.required<Day[]>();
  /** The unit after a money heading — `Spend (EUR)`. Never a symbol of this component's choosing. */
  readonly unit = input('');
  /** What one band is: "model", "outcome". Names the second column. */
  readonly noun = input('model');

  protected readonly limit = SHOWN;

  /** The second column's heading: the noun, capitalised, without pulling in a pipe for one word. */
  protected readonly nounLabel = computed(
    () => this.noun().charAt(0).toUpperCase() + this.noun().slice(1),
  );

  /** Whether every day is listed, or only the most recent few. */
  protected readonly all = signal(false);

  protected readonly shown = computed(() =>
    this.all() ? this.days() : this.days().slice(0, SHOWN),
  );

  protected readonly hidden = computed(() => Math.max(0, this.days().length - SHOWN));

  protected count(value: number): string {
    return displayCount(value);
  }

  /** Prompt and completion together, which is the figure a row has room for. */
  protected tokens(prompt: number, completion: number): string {
    return `${displayCount(prompt)} / ${displayCount(completion)}`;
  }
}
