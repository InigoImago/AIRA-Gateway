import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
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
import { PageFeedback } from '../../core/ui/page-feedback';
import { BudgetsTab } from './budgets-tab';
import { AccessPanel } from './access-panel';
import { RulesTab } from './rules-tab';
import { TracesTab } from './traces-tab';
import { WarningsTab } from './warnings-tab';
import { RateLimitsTab } from './rate-limits-tab';
import { ConfirmService } from '../../core/ui/confirm.service';

type Tab =
  'overview' | 'members' | 'keys' | 'budgets' | 'rate-limits' | 'rules' | 'warnings' | 'traces';

const TABS: readonly Tab[] = [
  'overview',
  'members',
  'keys',
  'budgets',
  'rate-limits',
  'rules',
  'warnings',
  'traces',
];

@Component({
  selector: 'app-use-case-detail',
  imports: [
    FormsModule,
    RouterLink,
    AccessPanel,
    BudgetsTab,
    RateLimitsTab,
    RulesTab,
    WarningsTab,
    TracesTab,
  ],
  templateUrl: './use-case-detail.html',
  // Provided here, not in root: the banner belongs to this page, and every panel on it reports
  // into the same one rather than announcing its own outcome separately.
  providers: [PageFeedback],
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
  protected readonly budgets = signal<Budget[]>([]);
  protected readonly rateLimits = signal<RateLimit[]>([]);
  protected readonly usage = signal<Record<number, BudgetUsage>>({});
  protected readonly usageUnavailable = signal(false);
  /** Why consumption is missing: refused by the gateway, or not reachable at all. */
  protected readonly usageRefused = signal(false);
  /** How many findings are about this use case. Owned here so the tab badge is right before the
   * tab is opened — the same reason loading stays in the parent (`CLAUDE.md` §3). */
  protected readonly warningCount = signal(0);
  protected readonly ruleCount = signal(0);

  /** One banner per page, shared with every panel it contains. */
  protected readonly feedback = inject(PageFeedback);
  protected readonly loading = signal(true);

  protected slug = '';

  /**
   * What this caller may do here — answered by the server, never inferred.
   *
   * These are object-level (guardian) permissions, so `/me` does not contain them and the console
   * cannot work them out. It used to render every action to everybody: a use-case *user* saw
   * "Add member" and "Remove", clicked one, and got a 403 from the screen that had just invited
   * the click. An action nobody can carry out reads as a broken system, not as a boundary.
   *
   * Default while nothing has loaded: **no**. Showing an action and taking it away is worse than
   * showing it a moment later.
   */
  protected readonly canManage = computed(() => this.useCase()?.permissions?.can_manage ?? false);
  /** Membership, which is what issuing a key needs — and seeing a use case is not (ADR-0007). */
  protected readonly isMember = computed(() => this.useCase()?.permissions?.is_member ?? false);

  // Form state lives in signals: the app runs zoneless, so a plain property changed from code
  // (resetting a form, switching scope) would not schedule a re-render.
  protected readonly memberUsername = signal('');
  protected readonly memberRole = signal('user');
  protected readonly keyLabel = signal('');
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
        this.feedback.fail(response, 'Could not load this use case.');
        this.loading.set(false);
      },
    });
    this.reloadMembers();
    this.loadKeys();
    this.loadBudgets();
    this.loadRateLimits();
  }

  protected loadRateLimits(): void {
    this.service.rateLimits(this.slug).subscribe({
      next: (limits) => this.rateLimits.set(limits),
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the rate limits.'),
    });
  }

  protected loadKeys(): void {
    this.service.apiKeys(this.slug).subscribe({
      next: (keys) => this.apiKeys.set(keys),
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the API keys.'),
    });
  }

  protected loadBudgets(): void {
    this.service.budgets(this.slug).subscribe({
      next: (budgets) => this.budgets.set(budgets),
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the budgets.'),
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
  //
  // Adding and removing moved into `access-panel` with `FRD-209`: a grant names a group or a
  // person, and splitting the two across two owners is how one of them quietly loses a rule the
  // other gained. The parent keeps the **load**, because the tab count has to exist before the
  // tab is opened.

  /** Re-read the members after the access panel changed one. */
  protected reloadMembers(): void {
    this.service.members(this.slug).subscribe({
      next: (members) => this.members.set(members),
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the members.'),
    });
  }

  // -- API keys ------------------------------------------------------------------------

  protected issueKey(): void {
    if (this.feedback.busy()) {
      return;
    }
    this.feedback.run(this.service.issueApiKey(this.slug, this.keyLabel()), {
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
    this.feedback.run(this.service.revokeApiKey(this.slug, prefix), {
      failure: 'Could not revoke the key.',
      success: () => {
        this.feedback.succeed('The key was revoked.');
        this.loadKeys();
      },
    });
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
    return !this.retentionError() && this.retentionChanged() && !this.feedback.busy();
  }

  protected saveRetention(): void {
    if (!this.canSaveRetention()) {
      return;
    }
    const days = this.retentionDays();
    const store = this.storePayloads();
    this.feedback.run(
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
          this.feedback.succeed(
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
}
