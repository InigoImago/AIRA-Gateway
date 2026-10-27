import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Trace } from '../../core/api/models';
import { degradedLine, outcomeParty, outcomeSentence, storyOf } from './what-happened';

/**
 * "What happened" in an opened request (`FRD-628` FR-5, FR-7): the outcome in words, the status,
 * the message the caller was given, and a timeline of every decision on the way.
 *
 * A child of the Requests table, not a block inside it: the table owns opening a row and recording
 * it, this panel only says what the opened row records.
 */
@Component({
  selector: 'app-what-happened',
  imports: [RouterLink],
  templateUrl: './what-happened-panel.html',
  styleUrl: './what-happened-panel.scss',
})
export class WhatHappenedPanel {
  /** The opened request, as the server answered when it recorded the opening. */
  readonly trace = input.required<Trace>();
  /**
   * Where the use case's pipeline test is, or `null` where it is not offered: the parent decides,
   * because only it knows whether this reader may run that test.
   */
  readonly tryLink = input<string[] | null>(null);

  protected readonly sentence = computed(() =>
    outcomeSentence(this.trace().outcome, this.trace().status),
  );
  protected readonly party = computed(() => outcomeParty(this.trace().outcome));
  protected readonly story = computed(() => storyOf(this.trace()));
  protected readonly degraded = computed(() => degradedLine(this.trace().degraded));
}
