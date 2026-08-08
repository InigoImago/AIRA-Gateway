import { Component, effect, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AnomalyRule } from '../../core/api/models';
import { unitOf } from './rule-language';

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

/** The seven kinds, as a person would pick them. Closed — see `aira_common.anomalies`. */
export const RULE_KINDS: { value: string; label: string }[] = [
  { value: 'refusal_rate', label: 'Too many requests are being refused' },
  { value: 'error_rate', label: 'Too many requests are failing upstream' },
  { value: 'spend_spike', label: 'Spend jumped against the previous window' },
  { value: 'request_spike', label: 'Request count jumped against the previous window' },
  { value: 'token_spike', label: 'Token use jumped against the previous window' },
  { value: 'payload_size', label: 'Too many unusually large requests' },
  { value: 'new_source_ip', label: 'Requests from an address never seen before' },
];

/**
 * The form for one anomaly rule, used by the IT Security console **and** by a use case's own
 * rules panel.
 *
 * One component rather than two, because the two screens are the same form with thirteen fields
 * and a validation contract the server enforces per kind. A second copy is how the use-case form
 * quietly loses the field the global one gained — the `:embedContent` failure with a whole screen
 * to hide in.
 *
 * What it deliberately does **not** offer on an existing rule: the **kind**. A rule's kind decides
 * what its threshold *means* — 50 is half the requests under `refusal_rate` and half a multiple
 * under `spend_spike` — so changing it in place silently reinterprets a number somebody chose. A
 * different kind is a different rule.
 */
@Component({
  selector: 'app-rule-form',
  imports: [FormsModule],
  template: `
    <form class="form-inline" (ngSubmit)="submit()">
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
        <label [attr.for]="id('threshold')">Above</label>
        <input
          [attr.id]="id('threshold')"
          type="number"
          name="threshold"
          min="1"
          [ngModel]="threshold()"
          (ngModelChange)="threshold.set($event)"
          [attr.data-testid]="id('threshold')"
        />
        <span class="field__hint">{{ unit() || 'as counted' }}</span>
      </div>

      <div class="field">
        <label [attr.for]="id('window')">Over (minutes)</label>
        <input
          [attr.id]="id('window')"
          type="number"
          name="window"
          min="1"
          [ngModel]="window()"
          (ngModelChange)="window.set($event)"
          [attr.data-testid]="id('window')"
        />
      </div>

      <div class="field">
        <label [attr.for]="id('sample')">Smallest sample</label>
        <input
          [attr.id]="id('sample')"
          type="number"
          name="sample"
          min="0"
          [ngModel]="sample()"
          (ngModelChange)="sample.set($event)"
          [attr.data-testid]="id('sample')"
        />
        <span class="field__hint">Below this many requests it is not judged at all.</span>
      </div>

      @if (kind() === 'payload_size') {
        <div class="field">
          <label [attr.for]="id('parameter')">Larger than</label>
          <input
            [attr.id]="id('parameter')"
            type="number"
            name="parameter"
            min="1"
            [ngModel]="parameter()"
            (ngModelChange)="parameter.set($event)"
            [attr.data-testid]="id('parameter')"
          />
          <span class="field__hint">bytes</span>
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
            [ngModel]="minutes()"
            (ngModelChange)="minutes.set($event)"
            [attr.data-testid]="id('minutes')"
          />
          <span class="field__hint">Empty means until somebody lifts it.</span>
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

      <label class="checkline" style="padding-bottom: 0.55rem">
        <input
          type="checkbox"
          [checked]="enabled()"
          (change)="enabled.set($any($event.target).checked)"
          [attr.data-testid]="id('enabled')"
        />
        Watching
      </label>

      <button
        type="submit"
        class="btn btn--primary"
        [disabled]="busy() || !canSubmit()"
        [attr.data-testid]="id('save')"
      >
        {{ busy() ? 'Saving…' : isNew() ? 'Create rule' : 'Save' }}
      </button>
      <button type="button" class="btn" (click)="cancelled.emit()">Cancel</button>
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
    // one's numbers — the zoneless form-state bug this project has already fixed once.
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

  protected isNew(): boolean {
    return !this.rule().id;
  }

  protected unit(): string {
    return unitOf(this.kind());
  }

  protected id(field: string): string {
    return `${this.formId()}-${field}`;
  }

  protected canSubmit(): boolean {
    if (this.isNew() && !this.name().trim()) return false;
    return !!this.threshold() && !!this.window();
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
