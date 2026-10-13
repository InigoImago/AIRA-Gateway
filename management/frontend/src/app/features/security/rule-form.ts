import { Component, computed, effect, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AnomalyRule } from '../../core/api/models';
import { isMultiple, isShare, needsSample, thresholdHint } from './rule-language';

/** The longest rule name the server stores (`AnomalyRule.name`, `max_length=120`). */
const MAX_NAME = 120;
/** A share of requests cannot exceed the whole (`AnomalyRuleSerializer.validate`). */
const MAX_SHARE = 100;
/** A day: the longest window "the window before this one" still describes comparable traffic
 *  (`aira_common.anomalies.MAX_WINDOW_MINUTES`). */
const MAX_WINDOW_MINUTES = 24 * 60;
/** A week: an automatic action that outlives the incident is an outage with a good reason
 *  (`aira_common.anomalies.MAX_ACTION_MINUTES`). */
const MAX_ACTION_MINUTES = 7 * 24 * 60;

/** A whole number at least `min` (and at most `max`) — what a `PositiveIntegerField` accepts. */
function whole(value: number | null, min: number, max = Number.MAX_SAFE_INTEGER): boolean {
  return value !== null && Number.isInteger(value) && value >= min && value <= max;
}

/** What a new rule starts as — the safe end of every axis. */
export const NEW_RULE: AnomalyRule = {
  id: 0,
  use_case: null,
  is_global: false,
  name: '',
  kind: 'refusal_rate',
  window_minutes: 15,
  threshold: 50,
  parameter: null,
  min_sample: 20,
  // `alert` is the default and that is a safety property (`FRD-500` §3): a system whose first
  // setting is `block` blocks wrongly once and is switched off forever.
  action: 'alert',
  target: 'subject',
  action_minutes: null,
  throttle_rpm: null,
  enabled: true,
};

/**
 * The seven kinds, as a person would pick them. Closed — see `aira_common.anomalies`.
 *
 * A hand-written copy of a closed vocabulary drifts, so
 * `tools/tests/test_the_console_speaks_the_closed_vocabulary.py` holds this list to that enum in
 * both directions.
 */
export const RULE_KINDS: { value: string; label: string }[] = [
  { value: 'refusal_rate', label: 'Too many requests are being refused' },
  { value: 'error_rate', label: 'Too many requests are failing upstream' },
  { value: 'spend_spike', label: 'Spend jumped against the previous window' },
  { value: 'request_spike', label: 'Request count jumped against the previous window' },
  { value: 'blocked_prompt_rate', label: 'Too many prompts are being blocked by the pipeline' },
  { value: 'payload_size', label: 'Too many unusually large requests' },
  { value: 'new_source_ip', label: 'Requests from an address never seen before' },
];

/**
 * The form for one anomaly rule, used by the IT Security console **and** by a use case's own
 * rules panel.
 *
 * One component for both screens: thirteen fields and a validation contract the server enforces per
 * kind, and a second copy would quietly lose the field the first one gained.
 *
 * It never offers to change the **kind** of an existing rule. The kind decides what the threshold
 * *means* — 50 is half the requests under `refusal_rate` and half a multiple under `spend_spike` —
 * so a different kind is a different rule.
 */
@Component({
  selector: 'app-rule-form',
  imports: [FormsModule],
  template: `
    <!-- Fields and actions on separate lines: the actions go after the fields, where a reader
         looks for them, rather than reading as two more settings in the same row. -->
    <form class="form-stack" (ngSubmit)="submit()">
      <div class="form-inline">
        @if (isNew()) {
          <div class="field grow">
            <label [attr.for]="id('name')">Name</label>
            <input
              [attr.id]="id('name')"
              name="name"
              [ngModel]="name()"
              (ngModelChange)="name.set($event)"
              placeholder="what somebody reading a finding should see"
              autocomplete="off"
              [attr.data-testid]="id('name')"
            />
          </div>
          <div class="field grow">
            <label [attr.for]="id('kind')">Watch for</label>
            <select
              [attr.id]="id('kind')"
              name="kind"
              [ngModel]="kind()"
              (ngModelChange)="kind.set($event)"
              [attr.data-testid]="id('kind')"
            >
              @for (option of kinds; track option.value) {
                <option [value]="option.value">{{ option.label }}</option>
              }
            </select>
            <!-- Said here rather than discovered on the next edit. -->
            <span class="field__hint">
              Fixed once the rule exists — the kind decides what the threshold means.
            </span>
          </div>
          <div class="field">
            <label [attr.for]="id('target')">Raised about</label>
            <select
              [attr.id]="id('target')"
              name="target"
              [ngModel]="target()"
              (ngModelChange)="target.set($event)"
            >
              <option value="subject">each caller</option>
              <option value="credential">each API key</option>
              <option value="use_case">the use case as a whole</option>
            </select>
          </div>
        }

        <div class="field">
          <!-- "At or above", because the evaluator fires at equality (\`share >= threshold\`). -->
          <label [attr.for]="id('threshold')">At or above</label>
          <input
            [attr.id]="id('threshold')"
            type="number"
            name="threshold"
            [attr.min]="isMultiple(kind()) ? 101 : 1"
            [attr.max]="isShare(kind()) ? 100 : null"
            [ngModel]="threshold()"
            (ngModelChange)="threshold.set($event)"
            [attr.data-testid]="id('threshold')"
          />
          <span class="field__hint">{{ hint() }}</span>
        </div>

        <div class="field">
          <label [attr.for]="id('window')">Over (minutes)</label>
          <input
            [attr.id]="id('window')"
            type="number"
            name="window"
            min="1"
            max="1440"
            [ngModel]="window()"
            (ngModelChange)="window.set($event)"
            [attr.data-testid]="id('window')"
          />
        </div>

        <!-- Only for a proportion: the server stores 0 for an event kind whatever is sent. -->
        @if (needsSample(kind())) {
          <div class="field">
            <label [attr.for]="id('sample')">Smallest sample</label>
            <input
              [attr.id]="id('sample')"
              type="number"
              name="sample"
              min="1"
              [ngModel]="sample()"
              (ngModelChange)="sample.set($event)"
              [attr.data-testid]="id('sample')"
            />
            <span class="field__hint">Below this many requests it is not judged at all.</span>
          </div>
        }

        @if (kind() === 'payload_size') {
          <div class="field">
            <label [attr.for]="id('parameter')">At least (bytes)</label>
            <input
              [attr.id]="id('parameter')"
              type="number"
              name="parameter"
              min="1"
              [ngModel]="parameter()"
              (ngModelChange)="parameter.set($event)"
              [attr.data-testid]="id('parameter')"
            />
            <span class="field__hint">the request size the share is measured against</span>
          </div>
        }

        <div class="field">
          <label [attr.for]="id('action')">Then</label>
          <select
            [attr.id]="id('action')"
            name="action"
            [ngModel]="action()"
            (ngModelChange)="action.set($event)"
            [attr.data-testid]="id('action')"
          >
            <option value="alert">record it (take nothing away)</option>
            <option value="throttle">slow the traffic down</option>
            <option value="block">stop the traffic</option>
          </select>
        </div>

        @if (action() !== 'alert') {
          <div class="field">
            <label [attr.for]="id('minutes')">For (minutes)</label>
            <input
              [attr.id]="id('minutes')"
              type="number"
              name="minutes"
              min="1"
              max="10080"
              [ngModel]="minutes()"
              (ngModelChange)="minutes.set($event)"
              [attr.data-testid]="id('minutes')"
            />
            <span class="field__hint">
              Required: 1 to 10080 (a week). An automatic action with no expiry is an outage.
            </span>
          </div>
        }

        @if (action() === 'throttle') {
          <div class="field">
            <label [attr.for]="id('rpm')">Slowed to</label>
            <input
              [attr.id]="id('rpm')"
              type="number"
              name="rpm"
              min="1"
              [ngModel]="rpm()"
              (ngModelChange)="rpm.set($event)"
              [attr.data-testid]="id('rpm')"
            />
            <span class="field__hint">
              requests a minute — a throttle without a rate is not a decision.
            </span>
          </div>
        }
      </div>

      <!-- Why Save is unavailable, in the server's own terms, rather than a button that is simply
           grey — or a 400 the author has to translate. -->
      @if (problem(); as reason) {
        <p class="field__hint field__hint--error" [attr.data-testid]="id('problem')">
          {{ reason }}
        </p>
      }

      <div class="form-actions">
        <label class="checkline">
          <input
            type="checkbox"
            [checked]="enabled()"
            (change)="enabled.set($any($event.target).checked)"
            [attr.data-testid]="id('enabled')"
          />
          Watching
        </label>
        <span class="form-actions__spacer"></span>
        <button type="button" class="btn" (click)="cancelled.emit()">Cancel</button>
        <button
          type="submit"
          class="btn btn--primary"
          [disabled]="busy() || !canSubmit()"
          [attr.data-testid]="id('save')"
        >
          {{ busy() ? 'Saving…' : isNew() ? 'Create rule' : 'Save' }}
        </button>
      </div>
    </form>
  `,
})
export class RuleForm {
  /** The rule being edited. `NEW_RULE` for one that does not exist yet. */
  readonly rule = input.required<AnomalyRule>();
  readonly busy = input(false);
  /** Distinguishes the ids of two forms on one page. */
  readonly formId = input('rule');

  readonly saved = output<Partial<AnomalyRule>>();
  readonly cancelled = output<void>();

  protected readonly kinds = RULE_KINDS;
  protected readonly needsSample = needsSample;
  protected readonly isShare = isShare;
  protected readonly isMultiple = isMultiple;

  protected readonly name = signal('');
  protected readonly kind = signal('refusal_rate');
  protected readonly target = signal('subject');
  protected readonly threshold = signal<number | null>(null);
  protected readonly window = signal<number | null>(null);
  protected readonly sample = signal<number | null>(null);
  protected readonly parameter = signal<number | null>(null);
  protected readonly action = signal('alert');
  protected readonly minutes = signal<number | null>(null);
  protected readonly rpm = signal<number | null>(null);
  protected readonly enabled = signal(true);

  constructor() {
    // Reset from the rule whenever it changes, so opening a second rule never shows the first
    // one's numbers.
    effect(() => {
      const rule = this.rule();
      this.name.set(rule.name);
      this.kind.set(rule.kind);
      this.target.set(rule.target);
      this.threshold.set(rule.threshold);
      this.window.set(rule.window_minutes);
      this.sample.set(rule.min_sample);
      this.parameter.set(rule.parameter);
      this.action.set(rule.action);
      this.minutes.set(rule.action_minutes);
      this.rpm.set(rule.throttle_rpm);
      this.enabled.set(rule.enabled);
    });
  }

  /** What the threshold field takes, in the unit the server checks and refuses it in. */
  protected readonly hint = computed(() => thresholdHint(this.kind(), this.threshold()));

  /**
   * Why the server would refuse this rule, or `null` when it would not.
   *
   * The same checks, in the same order and the same terms, as `AnomalyRuleSerializer.validate` and
   * the model's bounds — so Save is withheld with its reason beside it instead of being answered
   * with a 400. An older row the server would no longer accept (a block with no duration, a rate
   * judged on zero requests) has to be corrected before it can be saved, as it would there.
   */
  protected readonly problem = computed<string | null>(() => {
    const kind = this.kind();
    const threshold = this.threshold();
    if (this.isNew() && !this.name().trim()) {
      return 'A rule needs a name: it is what somebody reading a finding sees.';
    }
    if (this.name().trim().length > MAX_NAME) {
      return `A name is at most ${MAX_NAME} characters.`;
    }
    if (!whole(threshold, 1)) return 'The threshold is a whole number, at least 1.';
    if (isShare(kind) && threshold! > MAX_SHARE) {
      return 'A share of requests cannot exceed 100 percent.';
    }
    if (isMultiple(kind) && threshold! <= MAX_SHARE) {
      return (
        'A spike is a multiple of the previous window, so the threshold must be above 100 ' +
        'percent — 200 means twice as much.'
      );
    }
    if (!whole(this.window(), 1, MAX_WINDOW_MINUTES)) {
      return `The window is between 1 and ${MAX_WINDOW_MINUTES} minutes (a day).`;
    }
    if (needsSample(kind) && !whole(this.sample(), 1)) {
      return (
        'The smallest sample is at least 1: without a floor, one refused request out of one is ' +
        '100 percent.'
      );
    }
    if (kind === 'payload_size' && !whole(this.parameter(), 1)) {
      return 'A size rule needs the request size, in bytes, that the share is measured against.';
    }
    if (this.action() !== 'alert' && !whole(this.minutes(), 1, MAX_ACTION_MINUTES)) {
      return (
        `An automatic action lasts between 1 and ${MAX_ACTION_MINUTES} minutes (a week): one ` +
        'with no expiry is an outage with a good reason.'
      );
    }
    if (this.action() === 'throttle' && !whole(this.rpm(), 1)) {
      return 'A throttle needs a rate: a rate reduced to nothing is a block.';
    }
    return null;
  });

  protected isNew(): boolean {
    return !this.rule().id;
  }

  protected id(field: string): string {
    return `${this.formId()}-${field}`;
  }

  protected canSubmit(): boolean {
    return this.problem() === null;
  }

  protected submit(): void {
    if (!this.canSubmit()) return;
    this.saved.emit({
      name: this.name().trim(),
      // Sent whether or not it was editable, because the server validates the threshold **against
      // the kind**: a PATCH that omitted it would be checked against the default instead.
      kind: this.kind(),
      target: this.target(),
      threshold: this.threshold() ?? 0,
      window_minutes: this.window() ?? 15,
      min_sample: this.sample() ?? 0,
      parameter: this.kind() === 'payload_size' ? this.parameter() : null,
      action: this.action(),
      action_minutes: this.action() === 'alert' ? null : this.minutes(),
      throttle_rpm: this.action() === 'throttle' ? this.rpm() : null,
      enabled: this.enabled(),
    });
  }
}
