import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { errorMessage } from '../../core/api/error-message';
import {
  ApiKey,
  Budget,
  BudgetUsage,
  IssuedApiKey,
  Membership,
  RateLimit,
  UseCase,
} from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';

type Tab = 'overview' | 'members' | 'keys' | 'budgets' | 'rate-limits';

const TABS: readonly Tab[] = ['overview', 'members', 'keys', 'budgets', 'rate-limits'];

@Component({
  selector: 'app-use-case-detail',
  imports: [FormsModule, RouterLink],
  templateUrl: './use-case-detail.html',
})
export class UseCaseDetail implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);

  protected readonly useCase = signal<UseCase | null>(null);
  protected readonly members = signal<Membership[]>([]);
  protected readonly apiKeys = signal<ApiKey[]>([]);
  protected readonly issued = signal<IssuedApiKey | null>(null);
  protected readonly copied = signal(false);
  protected readonly copyFailed = signal(false);
  protected readonly tab = signal<Tab>('overview');
  protected readonly showAddMember = signal(false);
  protected readonly showIssueKey = signal(false);
  protected readonly showAddBudget = signal(false);
  protected readonly showAddRateLimit = signal(false);
  protected readonly budgets = signal<Budget[]>([]);
  protected readonly rateLimits = signal<RateLimit[]>([]);
  protected readonly usage = signal<Record<number, BudgetUsage>>({});
  protected readonly usageUnavailable = signal(false);
  /** Why consumption is missing: refused by the gateway, or not reachable at all. */
  protected readonly usageRefused = signal(false);

  /** One banner per page: the last thing that failed, and the last thing that worked. */
  protected readonly error = signal<string | null>(null);
  protected readonly notice = signal<string | null>(null);
  protected readonly loading = signal(true);
  /** Set while a mutation is in flight, so the triggering button can disable itself. */
  protected readonly busy = signal(false);

  protected slug = '';

  // Form state lives in signals: the app runs zoneless, so a plain property changed from code
  // (resetting a form, switching scope) would not schedule a re-render.
  protected readonly memberUsername = signal('');
  protected readonly memberRole = signal('user');
  protected readonly keyLabel = signal('');
  protected readonly budgetScope = signal<'use_case' | 'member'>('use_case');
  protected readonly budgetSubject = signal('');
  protected readonly budgetPeriod = signal<'day' | 'month'>('month');
  protected readonly budgetTokens = signal<number | null>(null);
  protected readonly budgetRequests = signal<number | null>(null);
  /** Kept as text: a spend limit must not round-trip through a JS number. */
  protected readonly budgetCost = signal('');
  protected readonly rlScope = signal<'use_case' | 'member'>('use_case');
  protected readonly rlSubject = signal('');
  protected readonly rlRpm = signal<number | null>(null);
  protected readonly rlBurst = signal<number | null>(null);
  /** Data-protection settings being edited on the overview tab (FRD-404). */
  protected readonly retentionDays = signal<number | null>(null);
  protected readonly storePayloads = signal(true);

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    const requested = this.route.snapshot.queryParamMap?.get('tab') as Tab | null;
    if (requested && TABS.includes(requested)) {
      this.tab.set(requested);
    }
    this.load();
  }

  /** Keep the open tab in the URL so a reload — or a shared link — lands in the same place. */
  protected selectTab(tab: Tab): void {
    this.tab.set(tab);
    void this.router
      .navigate([], { queryParams: { tab }, queryParamsHandling: 'merge', replaceUrl: true })
      .catch(() => undefined);
  }

  protected load(): void {
    this.loading.set(true);
    this.service.get(this.slug).subscribe({
      next: (useCase) => {
        this.useCase.set(useCase);
        this.retentionDays.set(useCase.retention_days ?? null);
        this.storePayloads.set(useCase.store_payloads ?? true);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.error.set(errorMessage(response, 'Could not load this use case.'));
        this.loading.set(false);
      },
    });
    this.service.members(this.slug).subscribe({
      next: (members) => this.members.set(members),
      error: (response: unknown) =>
        this.error.set(errorMessage(response, 'Could not load the members.')),
    });
    this.loadKeys();
    this.loadBudgets();
    this.loadRateLimits();
  }

  protected loadRateLimits(): void {
    this.service.rateLimits(this.slug).subscribe({
      next: (limits) => this.rateLimits.set(limits),
      error: (response: unknown) =>
        this.error.set(errorMessage(response, 'Could not load the rate limits.')),
    });
  }

  protected loadKeys(): void {
    this.service.apiKeys(this.slug).subscribe({
      next: (keys) => this.apiKeys.set(keys),
      error: (response: unknown) =>
        this.error.set(errorMessage(response, 'Could not load the API keys.')),
    });
  }

  protected loadBudgets(): void {
    this.service.budgets(this.slug).subscribe({
      next: (budgets) => this.budgets.set(budgets),
      error: (response: unknown) =>
        this.error.set(errorMessage(response, 'Could not load the budgets.')),
    });
    this.service.budgetUsage(this.slug).subscribe({
      next: ({ usage }) => {
        const map: Record<number, BudgetUsage> = {};
        for (const entry of usage) map[entry.id] = entry;
        this.usage.set(map);
        this.usageUnavailable.set(false);
      },
      // Consumption comes from the gateway, limits from Management: a gateway that is
      // unreachable — or that will not show *this* caller the numbers — must not blank out the
      // whole tab. Show the limits and say precisely which of the two happened.
      error: (response: { status?: number }) => {
        this.usage.set({});
        this.usageUnavailable.set(true);
        this.usageRefused.set(response?.status === 403);
      },
    });
  }

  // -- members -------------------------------------------------------------------------

  protected canAddMember(): boolean {
    return !!this.memberUsername().trim() && !this.busy();
  }

  protected addMember(): void {
    if (!this.canAddMember()) {
      return;
    }
    const username = this.memberUsername().trim();
    this.run(this.service.addMember(this.slug, username, this.memberRole()), {
      failure: 'Could not add the member.',
      success: () => {
        this.notice.set(`${username} was added.`);
        this.memberUsername.set('');
        this.showAddMember.set(false);
        this.load();
      },
    });
  }

  protected removeMember(username: string): void {
    if (!this.confirmService.ask(`Remove ${username} from this use case?`)) {
      return;
    }
    this.run(this.service.removeMember(this.slug, username), {
      failure: `Could not remove ${username}.`,
      success: () => {
        this.notice.set(`${username} was removed.`);
        this.load();
      },
    });
  }

  // -- API keys ------------------------------------------------------------------------

  protected issueKey(): void {
    if (this.busy()) {
      return;
    }
    this.run(this.service.issueApiKey(this.slug, this.keyLabel()), {
      failure: 'Could not issue the key.',
      success: (issued: IssuedApiKey) => {
        this.keyLabel.set('');
        this.copied.set(false);
        this.copyFailed.set(false);
        this.issued.set(issued);
        this.showIssueKey.set(false);
        this.loadKeys();
      },
    });
  }

  protected dismissIssued(): void {
    this.issued.set(null);
    this.copied.set(false);
    this.copyFailed.set(false);
  }

  protected copyKey(value: string): void {
    // The clipboard API is unavailable outside a secure context; the key is on screen either
    // way, so say "select and copy it" rather than leaving a button that does nothing.
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard;
    if (!clipboard) {
      this.copyFailed.set(true);
      return;
    }
    void clipboard.writeText(value).then(
      () => {
        this.copied.set(true);
        this.copyFailed.set(false);
      },
      () => this.copyFailed.set(true),
    );
  }

  protected revokeKey(prefix: string): void {
    const question = `Revoke key aira_${prefix}_…? Any client still using it will start failing.`;
    if (!this.confirmService.ask(question)) {
      return;
    }
    this.run(this.service.revokeApiKey(this.slug, prefix), {
      failure: 'Could not revoke the key.',
      success: () => {
        this.notice.set('The key was revoked.');
        this.loadKeys();
      },
    });
  }

  // -- budgets -------------------------------------------------------------------------

  protected budgetError(): string | null {
    if (this.budgetScope() === 'member' && !this.budgetSubject().trim()) {
      return 'A member budget needs a username.';
    }
    const cost = this.budgetCost().trim();
    if (cost && !/^\d+([.,]\d{1,6})?$/.test(cost)) {
      return 'The spend limit must be an amount, e.g. 250 or 250.00.';
    }
    if (!cost && this.budgetTokens() == null && this.budgetRequests() == null) {
      return 'Set a spend limit, a token limit, or a request limit.';
    }
    return null;
  }

  protected canAddBudget(): boolean {
    return !this.budgetError() && !this.busy();
  }

  protected addBudget(): void {
    if (!this.canAddBudget()) {
      return;
    }
    const cost = this.budgetCost().trim().replace(',', '.');
    const budget: Budget = {
      scope: this.budgetScope(),
      subject: this.budgetScope() === 'member' ? this.budgetSubject().trim() : '',
      period: this.budgetPeriod(),
      limit_cost: cost || null,
      limit_tokens: this.budgetTokens(),
      limit_requests: this.budgetRequests(),
    };
    this.run(this.service.createBudget(this.slug, budget), {
      failure: 'Could not save the budget.',
      success: () => {
        this.notice.set('Budget saved.');
        this.budgetSubject.set('');
        this.budgetCost.set('');
        this.budgetTokens.set(null);
        this.budgetRequests.set(null);
        this.showAddBudget.set(false);
        this.loadBudgets();
      },
    });
  }

  protected removeBudget(id: number | undefined): void {
    if (id == null || !this.confirmService.ask('Remove this budget? Its limits stop applying.')) {
      return;
    }
    this.run(this.service.deleteBudget(this.slug, id), {
      failure: 'Could not remove the budget.',
      success: () => {
        this.notice.set('Budget removed.');
        this.loadBudgets();
      },
    });
  }

  protected usedFor(budget: Budget): BudgetUsage {
    return (
      (budget.id != null ? this.usage()[budget.id] : undefined) ?? {
        id: budget.id ?? 0,
        used_tokens: 0,
        used_requests: 0,
        used_cost_nanos: 0,
        used_cost: '0.00',
        unpriced_requests: 0,
      }
    );
  }

  /** Percentage of a spend limit consumed. Compares in nano-units, so the division never
   * touches a decimal amount. */
  protected costPct(budget: Budget): number {
    const limit = budget.limit_cost ? Number(budget.limit_cost) * 1_000_000_000 : 0;
    return limit
      ? Math.min(100, Math.round((this.usedFor(budget).used_cost_nanos / limit) * 100))
      : 0;
  }

  /** Total requests in the period whose cost is unknown because the model has no price. */
  protected readonly unpricedRequests = computed(() =>
    Object.values(this.usage()).reduce((sum, entry) => sum + (entry.unpriced_requests ?? 0), 0),
  );

  protected pct(used: number, limit: number | null | undefined): number {
    return limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  }

  protected budgetLabel(budget: Budget): string {
    return budget.scope === 'member' ? budget.subject || 'member' : 'Whole use case';
  }

  // -- rate limits ---------------------------------------------------------------------

  protected rateLimitError(): string | null {
    if (this.rlScope() === 'member' && !this.rlSubject().trim()) {
      return 'A member limit needs a username.';
    }
    const rpm = this.rlRpm();
    if (rpm == null) return 'Set how many requests per minute are allowed.';
    if (!Number.isInteger(rpm) || rpm < 1) return 'At least 1 request per minute.';
    const burst = this.rlBurst();
    if (burst != null && (!Number.isInteger(burst) || burst < 1)) {
      return 'A burst must be at least 1, or left empty.';
    }
    return null;
  }

  protected canAddRateLimit(): boolean {
    return !this.rateLimitError() && !this.busy();
  }

  protected addRateLimit(): void {
    if (!this.canAddRateLimit()) {
      return;
    }
    const limit: RateLimit = {
      scope: this.rlScope(),
      subject: this.rlScope() === 'member' ? this.rlSubject().trim() : '',
      limit_rpm: this.rlRpm() ?? 0,
      burst: this.rlBurst() ?? 0,
    };
    this.run(this.service.createRateLimit(this.slug, limit), {
      failure: 'Could not save the rate limit.',
      success: () => {
        this.notice.set('Rate limit saved.');
        this.rlSubject.set('');
        this.rlRpm.set(null);
        this.rlBurst.set(null);
        this.showAddRateLimit.set(false);
        this.loadRateLimits();
      },
    });
  }

  protected removeRateLimit(id: number | undefined): void {
    if (
      id == null ||
      !this.confirmService.ask('Remove this rate limit? Requests stop being throttled.')
    ) {
      return;
    }
    this.run(this.service.deleteRateLimit(this.slug, id), {
      failure: 'Could not remove the rate limit.',
      success: () => {
        this.notice.set('Rate limit removed.');
        this.loadRateLimits();
      },
    });
  }

  protected rateLimitLabel(limit: RateLimit): string {
    return limit.scope === 'member' ? limit.subject || 'member' : 'Whole use case';
  }

  /** What the bucket actually allows at once — an unset burst means the per-minute figure. */
  protected effectiveBurst(limit: RateLimit): number {
    return limit.burst && limit.burst > 0 ? limit.burst : limit.limit_rpm;
  }

  // -- retention -----------------------------------------------------------------------

  protected retentionError(): string | null {
    // With storage off there is nothing to keep, so the period is not asked for.
    if (!this.storePayloads()) return null;
    const days = this.retentionDays();
    if (days == null) return 'Set how many days payloads are kept.';
    if (!Number.isInteger(days) || days < 1 || days > 3650) {
      return 'Between 1 and 3650 days.';
    }
    return null;
  }

  protected retentionChanged(): boolean {
    return (
      this.retentionDays() !== (this.useCase()?.retention_days ?? null) ||
      this.storePayloads() !== (this.useCase()?.store_payloads ?? true)
    );
  }

  protected canSaveRetention(): boolean {
    return !this.retentionError() && this.retentionChanged() && !this.busy();
  }

  protected saveRetention(): void {
    if (!this.canSaveRetention()) {
      return;
    }
    const days = this.retentionDays();
    const store = this.storePayloads();
    this.run(
      this.service.update(this.slug, {
        store_payloads: store,
        ...(store && days != null ? { retention_days: days } : {}),
      }),
      {
        failure: 'Could not change the data-protection settings.',
        success: (useCase: UseCase) => {
          this.useCase.set(useCase);
          this.retentionDays.set(useCase.retention_days ?? null);
          this.storePayloads.set(useCase.store_payloads ?? true);
          this.notice.set(
            store
              ? `Prompts and responses are now kept for ${days} day(s). Anything already past that is removed on the next run.`
              : 'Prompts and responses are no longer stored for this use case. Anything already stored is removed on the next run.',
          );
        },
      },
    );
  }

  // -- shared mutation plumbing ---------------------------------------------------------

  /**
   * Run a mutation and report the outcome exactly once. Every mutation goes through here so
   * none of them can fail silently — a silent failure is what made a 403 look like a dead
   * button.
   */
  private run<T>(
    request: Observable<T>,
    handlers: { failure: string; success: (value: T) => void },
  ): void {
    this.busy.set(true);
    this.error.set(null);
    this.notice.set(null);
    request.subscribe({
      next: (value) => {
        this.busy.set(false);
        handlers.success(value);
      },
      error: (response: unknown) => {
        this.busy.set(false);
        this.error.set(errorMessage(response, handlers.failure));
      },
    });
  }
}
