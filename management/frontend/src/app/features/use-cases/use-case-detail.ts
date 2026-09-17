import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import {
  ApiKey,
  Budget,
  BudgetUsage,
  CatalogModel,
  IssuedApiKey,
  KiraModel,
  Membership,
  PersonRow,
  RateLimit,
  ReportRow,
  UseCase,
  UseCaseConsumption,
} from '../../core/api/models';
import { errorMessage } from '../../core/api/error-message';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { windowFor } from '../../core/ui/periods';
import { AboutPanel } from './about-panel';
import { AccessPanel } from './access-panel';
import { ApiKeysPanel } from './api-keys-panel';
import { BudgetsTab } from './budgets-tab';
import { CapabilitiesPanel } from './capabilities-panel';
import { ConnectionPanel } from './connection-panel';
import { ConsumptionPanel } from './consumption-panel';
import { DataProtectionPanel } from './data-protection-panel';
import { ModelReleasePanel } from './model-release-panel';
import { PeoplePanel } from './people-panel';
import { RateLimitsTab } from './rate-limits-tab';
import { RulesTab } from './rules-tab';
import { TracesTab } from './traces-tab';
import { WarningsTab } from './warnings-tab';

const TABS = [
  'overview',
  'members',
  'keys',
  'budgets',
  'rate-limits',
  'rules',
  'warnings',
  'traces',
] as const;

type Tab = (typeof TABS)[number];

/**
 * One use case: the tab bar and what it counts (`CLAUDE.md` §3, a page is a parent plus panels).
 *
 * The parent loads everything a tab count or several panels need; each panel owns its form state
 * and mutations and raises an output when the parent should reload.
 */
@Component({
  selector: 'app-use-case-detail',
  imports: [
    RouterLink,
    AboutPanel,
    AccessPanel,
    ApiKeysPanel,
    BudgetsTab,
    CapabilitiesPanel,
    ConnectionPanel,
    ConsumptionPanel,
    DataProtectionPanel,
    ModelReleasePanel,
    PeoplePanel,
    RateLimitsTab,
    RulesTab,
    TracesTab,
    WarningsTab,
  ],
  templateUrl: './use-case-detail.html',
  // Provided here, not in root: every panel on the page reports into this one banner.
  providers: [PageFeedback],
})
export class UseCaseDetail implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  protected readonly feedback = inject(PageFeedback);
  /** Passed down to the panels that price things, which must not fetch it themselves. */
  protected readonly currency = this.meService.currency;

  protected slug = '';
  protected readonly loading = signal(true);
  /** The server answered 404: no such use case, or none this caller may see. */
  protected readonly missing = signal(false);
  protected readonly tab = signal<Tab>('overview');
  protected readonly onOverview = computed(() => this.tab() === 'overview');

  protected readonly useCase = signal<UseCase | null>(null);
  protected readonly members = signal<Membership[]>([]);
  protected readonly apiKeys = signal<ApiKey[]>([]);
  protected readonly budgets = signal<Budget[]>([]);
  protected readonly rateLimits = signal<RateLimit[]>([]);
  protected readonly usage = signal<Record<number, BudgetUsage>>({});
  protected readonly usageUnavailable = signal(false);
  /** Why budget usage is missing: refused by the gateway, or not reachable at all. */
  protected readonly usageRefused = signal(false);
  /** Held here so the tab badges are right before a tab is opened. */
  protected readonly warningCount = signal(0);
  protected readonly ruleCount = signal(0);

  /**
   * What this use case consumed, from the request log rather than a budget counter (`FRD-603`).
   * `null` is **unknown**, never zero.
   */
  protected readonly consumptionMonth = signal<ReportRow | null>(null);
  protected readonly consumptionToday = signal<ReportRow | null>(null);
  /** Who consumed what (`FRD-606`), over both windows: a budget's period picks which one applies. */
  protected readonly peopleMonth = signal<PersonRow[]>([]);
  protected readonly peopleToday = signal<PersonRow[]>([]);
  /**
   * How many of the two windows failed — a count, because neither request knows about the other,
   * and a flag written by both let the later one hide a window that had arrived.
   */
  private readonly consumptionFailures = signal(0);
  private readonly consumptionReason = signal('');
  protected readonly consumptionOutOfScope = signal(false);
  protected readonly consumption = computed<UseCaseConsumption>(() => {
    const month = this.consumptionMonth();
    const today = this.consumptionToday();
    const failed = this.consumptionFailures() > 0;
    return {
      month,
      today,
      unavailable: failed && month === null && today === null,
      partial: failed && (month !== null || today !== null),
      reason: this.consumptionReason(),
      outOfScope: this.consumptionOutOfScope(),
    };
  });

  /** The signed-in reader's name — what the gateway groups a person's figures under (`FRD-606`). */
  protected readonly myName = signal<string | null>(null);
  /** The gateway's KIRA listing for the connection block; `null` until it answers. */
  protected readonly kiraModels = signal<KiraModel[] | null>(null);
  protected readonly kiraFailure = signal('');
  /** The model catalogue, for the OpenCode configuration a new key comes with. */
  protected readonly catalog = signal<CatalogModel[]>([]);
  /** The key just issued, held here so its one-time plaintext survives a tab switch. */
  protected readonly issued = signal<IssuedApiKey | null>(null);
  /**
   * The installation's key policy. These are placeholders until `/me` answers: the server decides
   * and refuses an out-of-range lifetime by name (`ADR-0015`).
   */
  protected readonly defaultKeyDays = signal(30);
  protected readonly maxKeyDays = signal(180);

  /**
   * What this caller may do here, answered by the server. Object-level permissions are not in
   * `/me`, so the console cannot infer them; until they arrive the answer is **no**, because
   * showing an action and taking it away is worse than showing it a moment later.
   */
  protected readonly canManage = computed(() => this.useCase()?.permissions?.can_manage ?? false);
  /** Changing what the use case **is** — releasing a model (`FRD-308`) — needs `may_admin`. */
  protected readonly canAdmin = computed(() => this.useCase()?.permissions?.can_admin ?? false);
  /** Membership, which issuing a key needs and seeing a use case does not (`ADR-0007`). */
  protected readonly isMember = computed(() => this.useCase()?.permissions?.is_member ?? false);

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    const requested = this.route.snapshot.queryParamMap?.get('tab') as Tab | null;
    if (requested && TABS.includes(requested)) {
      this.tab.set(requested);
    }
    this.load();
    // A failure leaves the catalogue empty; the OpenCode config then names a fallback model.
    this.service.models().subscribe({
      next: (models) => this.catalog.set(models),
      error: () => undefined,
    });
    // Loaded here rather than in the connection panel, which only renders. A failure is carried
    // down: without it every id would read as absent, which is a different, real state.
    this.service.kiraModels().subscribe({
      next: (models) => this.kiraModels.set(models),
      error: (error) => {
        this.kiraModels.set([]);
        this.kiraFailure.set(
          errorMessage(error, 'The gateway could not be asked for its model ids.'),
        );
      },
    });
    this.meService.get().subscribe({
      next: (me) => {
        this.myName.set(me.username || null);
        if (me.api_key_default_days) {
          this.defaultKeyDays.set(me.api_key_default_days);
        }
        if (me.api_key_max_days) {
          this.maxKeyDays.set(me.api_key_max_days);
        }
      },
      // Silent: the policy is a hint on one form, not a reason for a banner over the page.
      error: () => undefined,
    });
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
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.missing.set((response as { status?: number } | null)?.status === 404);
        this.feedback.fail(response, 'Could not load this use case.');
        this.loading.set(false);
      },
    });
    this.reloadMembers();
    this.loadKeys();
    this.loadBudgets();
    this.loadRateLimits();
    // Its own load: consumption is a fact about the use case, not about its limits.
    this.loadConsumption();
  }

  /** Re-read the members; the access panel raises `changed` after a grant changes. */
  protected reloadMembers(): void {
    this.service.members(this.slug).subscribe({
      next: (members) => this.members.set(members),
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the members.'),
    });
  }

  protected loadKeys(): void {
    this.service.apiKeys(this.slug).subscribe({
      next: (keys) => this.apiKeys.set(keys),
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the API keys.'),
    });
  }

  protected loadRateLimits(): void {
    this.service.rateLimits(this.slug).subscribe({
      next: (limits) => this.rateLimits.set(limits),
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the rate limits.'),
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
      // Usage comes from the gateway and limits from Management: an unreachable or refusing
      // gateway must not blank the tab. Show the limits and say which of the two happened.
      error: (response: { status?: number }) => {
        this.usage.set({});
        this.usageUnavailable.set(true);
        this.usageRefused.set(response?.status === 403);
      },
    });
  }

  /**
   * This month and today (`FRD-603`): the figure somebody reports, and the one that shows
   * something running away now.
   *
   * A failure is stated by the panel as *unknown*, not in the page banner: a figure the reader did
   * not ask for failing would otherwise read as the use case failing to load.
   */
  protected loadConsumption(): void {
    this.consumptionFailures.set(0);
    this.consumptionReason.set('');
    const load = (preset: 'this-month' | 'today', into: (row: ReportRow | null) => void) => {
      const { from, to } = windowFor(preset, new Date());
      this.service.useCaseReport(this.slug, from, to).subscribe({
        next: (report) => {
          // `in_scope: false` is an empty report the caller was not entitled to fill; its zeroes
          // would claim this use case consumed nothing.
          this.consumptionOutOfScope.set(report.in_scope === false);
          into(report.in_scope === false ? null : report.totals);
          const people = report.in_scope === false ? [] : (report.by_person ?? []);
          (preset === 'today' ? this.peopleToday : this.peopleMonth).set(people);
        },
        error: (response: unknown) => {
          into(null);
          this.consumptionFailures.update((count) => count + 1);
          this.consumptionReason.set(errorMessage(response, 'The gateway could not be reached.'));
        },
      });
    };
    load('this-month', (row) => this.consumptionMonth.set(row));
    load('today', (row) => this.consumptionToday.set(row));
  }
}
