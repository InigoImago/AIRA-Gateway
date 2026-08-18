import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import {
  KiraModel,
  ApiKey,
  Budget,
  BudgetUsage,
  IssuedApiKey,
  Membership,
  RateLimit,
  PersonRow,
  ReportRow,
  UseCase,
  UseCaseConsumption,
} from '../../core/api/models';
import { errorMessage } from '../../core/api/error-message';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { windowFor } from '../../core/ui/periods';
import { BudgetsTab } from './budgets-tab';
import { ConnectionPanel } from './connection-panel';
import { ConsumptionPanel } from './consumption-panel';
import { PeoplePanel } from './people-panel';
import { AboutPanel } from './about-panel';
import { ModelReleasePanel } from './model-release-panel';
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
    ConnectionPanel,
    DatePipe,
    FormsModule,
    RouterLink,
    AccessPanel,
    BudgetsTab,
    InfoHint,
    Modal,
    ConsumptionPanel,
    PeoplePanel,
    AboutPanel,
    ModelReleasePanel,
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
  private readonly meService = inject(MeService);

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
  /**
   * What this use case consumed, from the request log rather than from a budget counter
   * (`FRD-603`). `null` is **unknown**, never zero — see `UseCaseConsumption`.
   */
  protected readonly consumptionMonth = signal<ReportRow | null>(null);
  protected readonly consumptionToday = signal<ReportRow | null>(null);
  /**
   * Who consumed what, over each of the two windows (`FRD-606`).
   *
   * Two windows because a budget's period decides which one answers *"how much is left"*: a `day`
   * budget is measured against today and a `month` budget against the month. Both are already
   * being fetched for the totals above, so this costs no extra request.
   */
  protected readonly peopleMonth = signal<PersonRow[]>([]);
  protected readonly peopleToday = signal<PersonRow[]>([]);
  /** The signed-in reader's name, for the overview's "what you used" card. */
  protected readonly myName = signal<string | null>(null);
  /**
   * How many of the two windows did not arrive — a **count**, not a flag.
   *
   * The first version was a boolean written by both subscriptions, so the second one to answer
   * decided: a month that had already been fetched was hidden because the day's request failed a
   * moment later. Neither request knows about the other, so neither of them can own that verdict.
   */
  private readonly consumptionFailures = signal(0);
  private readonly consumptionReason = signal('');
  protected readonly consumptionOutOfScope = signal(false);
  /** The gateway's KIRA listing for the connection block; `null` until it answers. */
  protected readonly kiraModels = signal<KiraModel[] | null>(null);
  protected readonly kiraFailure = signal('');

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
  /** May change what the use case **is** — which is what releasing a model is (`FRD-308`), and it
   *  is a narrower answer than `can_manage`: the server's own `perform_update` asks `may_admin`. */
  protected readonly canAdmin = computed(() => this.useCase()?.permissions?.can_admin ?? false);
  /** Membership, which is what issuing a key needs — and seeing a use case is not (ADR-0007). */
  protected readonly isMember = computed(() => this.useCase()?.permissions?.is_member ?? false);

  // Form state lives in signals: the app runs zoneless, so a plain property changed from code
  // (resetting a form, switching scope) would not schedule a re-render.
  protected readonly memberUsername = signal('');
  protected readonly memberRole = signal('user');
  protected readonly keyLabel = signal('');
  /**
   * Issue the key **on behalf of** somebody else (`FRD-604` FR-5).
   *
   * Empty is the ordinary case and means "me". Typed rather than picked from a directory, which
   * is a deliberate deviation from the FRD: the constraint is not "a real identity" but "an
   * identity with access to *this* use case", the server checks exactly that, and a wrong name is
   * refused by name. A picker of the membership list would have been narrower than the rule —
   * access can come from a group grant, and a service account granted that way belongs to no
   * membership row.
   */
  protected readonly keyOwner = signal('');
  /**
   * Days until the new key stops working. Empty means **never**, which is what every key issued
   * before this existed carries — offering the choice rather than imposing a default, because a
   * default lifetime would break existing integrations at whatever mark it picked.
   */
  protected readonly keyExpiresInDays = signal('');
  /**
   * The installation's key policy, shown rather than assumed.
   *
   * Read from `/api/v1/me` so the form states the same numbers the server enforces — a client-side
   * constant would be a second definition, and the first time somebody changed the setting the
   * form would be confidently wrong about a refusal the reader then cannot explain.
   */
  /**
   * The installation's key policy, asked for rather than assumed (`ADR-0015`).
   *
   * The values below are only what the form shows until `/me` answers; the **server** decides and
   * refuses an out-of-range lifetime by name. Stating a number the server does not enforce would
   * leave the reader with a refusal they cannot explain, so these are a placeholder, not a rule.
   */
  protected readonly defaultKeyDays = signal(30);
  protected readonly maxKeyDays = signal(180);
  /** Data-protection settings being edited on the overview tab (FRD-404). */
  protected readonly retentionDays = signal<number | null>(null);
  protected readonly storePayloads = signal(true);
  /** Whether members of this use case see only their own requests (`FRD-505` FR-4). */
  protected readonly restrictMembers = signal(false);
  /** Two switches that existed only in the API until 2026-08-10 (`FRD-131`, `FRD-133`). A
   * capability with no way in does not announce itself — unlike a control that refuses when used,
   * which at least produces a complaint (`FRD-206`, inverted). */
  protected readonly toolsEnabled = signal(false);
  /**
   * Whether a model's reasoning comes back and is kept (`FRD-135`).
   *
   * Beside the other two because it is the same kind of decision — what this use case may do — and
   * off for the same reason `store_payloads` exists at all: reasoning can restate the prompt
   * verbatim, so switching it on is a decision about **content**, not about verbosity.
   */
  protected readonly includeReasoning = signal(false);
  protected readonly promptCaching = signal(false);
  protected readonly cacheTtl = signal('5m');

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    const requested = this.route.snapshot.queryParamMap?.get('tab') as Tab | null;
    if (requested && TABS.includes(requested)) {
      this.tab.set(requested);
    }
    this.load();
    // Which models can actually be used by an assistant. Read from the catalog rather than
    // hard-coded, because "declares tool calling" is a measured fact that changes (`FRD-131`).
    this.service.models().subscribe({
      next: (models) =>
        this.toolModels.set(
          models.filter((m) => (m.capabilities ?? []).includes('tools')).map((m) => m.name),
        ),
      error: () => undefined,
    });
    // The KIRA ids, for the connection block. Loaded here rather than in the panel because the
    // page loads and a panel renders (`CLAUDE.md` §3) — and because a child that fetches for
    // itself is a child the page's own tests cannot stand up.
    //
    // A failure is carried down rather than swallowed: without it every id would read as absent,
    // which is a real state — a deployment where no model has a number — and a different one.
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
        // Who is reading, so the overview can show *their* consumption. The name rather than the
        // subject, because that is what the gateway groups a person's figures under (`FRD-606`).
        this.myName.set(me.username || null);
        if (me.api_key_default_days) {
          this.defaultKeyDays.set(me.api_key_default_days);
        }
        if (me.api_key_max_days) {
          this.maxKeyDays.set(me.api_key_max_days);
        }
      },
      // Deliberately silent: the policy is a hint on one form, and a banner about it would sit
      // above a page whose actual content loaded perfectly well.
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
        this.retentionDays.set(useCase.retention_days ?? null);
        this.storePayloads.set(useCase.store_payloads ?? true);
        this.restrictMembers.set(useCase.restrict_members_to_own_requests ?? false);
        this.toolsEnabled.set(useCase.tools_enabled ?? false);
        this.includeReasoning.set(useCase.include_reasoning ?? false);
        this.promptCaching.set(useCase.prompt_caching_enabled ?? false);
        this.cacheTtl.set(useCase.prompt_cache_ttl ?? '5m');
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
    // Not part of `loadBudgets`: consumption belongs to the overview and is a fact about the use
    // case, not about its limits. Hanging it off the budget load would mean adding a budget
    // refetched it and removing the budgets panel silently removed it.
    this.loadConsumption();
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

  /**
   * What this use case has actually consumed, whether or not anybody set a limit (`FRD-603`).
   *
   * Two windows, because they answer two questions: the month is the figure somebody reports, and
   * the day is the one that says whether something is running away right now.
   *
   * A failure here is **not** reported through the page banner. Consumption is a figure this tab
   * offers, not something the reader asked for; a red banner across the page for it would say the
   * use case failed to load. The panel states its own absence instead — and states it as
   * *unknown*, never as zero.
   */
  protected loadConsumption(): void {
    this.consumptionFailures.set(0);
    this.consumptionReason.set('');
    const load = (preset: 'this-month' | 'today', into: (row: ReportRow | null) => void) => {
      const { from, to } = windowFor(preset, new Date());
      this.service.useCaseReport(this.slug, from, to).subscribe({
        next: (report) => {
          // `in_scope: false` is an empty report the caller was not entitled to fill. Showing its
          // zeroes would tell them this use case consumed nothing, which is a statement nobody
          // made — the gateway said only that it would not answer.
          this.consumptionOutOfScope.set(report.in_scope === false);
          into(report.in_scope === false ? null : report.totals);
          const people = report.in_scope === false ? [] : (report.by_person ?? []);
          (preset === 'today' ? this.peopleToday : this.peopleMonth).set(people);
        },
        error: (response: unknown) => {
          into(null);
          this.consumptionFailures.update((count) => count + 1);
          // §3's rule — no silent failures, and the backend's envelope rather than our guess.
          // Kept out of the page banner all the same: this is a figure the panel offers, and a
          // red bar across the page would say the use case failed to load.
          this.consumptionReason.set(errorMessage(response, 'The gateway could not be reached.'));
        },
      });
    };
    load('this-month', (row) => this.consumptionMonth.set(row));
    load('today', (row) => this.consumptionToday.set(row));
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
    const days = Number(this.keyExpiresInDays());
    this.feedback.run(
      this.service.issueApiKey(
        this.slug,
        this.keyLabel(),
        days > 0 ? days : null,
        this.keyOwner().trim() || null,
      ),
      {
        failure: 'Could not issue the key.',
        success: (issued: IssuedApiKey) => {
          this.keyLabel.set('');
          this.keyOwner.set('');
          this.keyExpiresInDays.set('');
          this.copied.set(false);
          this.copyFailed.set(false);
          this.issued.set(issued);
          this.showIssueKey.set(false);
          this.loadKeys();
        },
      },
    );
  }

  /**
   * The OpenCode configuration for the key that was just issued (`FRD-132`).
   *
   * Generated **here and now** because the plaintext exists for exactly this moment: it is shown
   * once and never retrievable, so a configuration offered on any later screen could only contain
   * a placeholder — and a config file with a placeholder in it is one somebody pastes and then
   * debugs for twenty minutes.
   *
   * The key is written into the file rather than referenced from the environment, which is the
   * opposite of what `tools/opencode/opencode.json` does. Deliberate: that one lives in a
   * repository, this one is downloaded to a workstation for a single developer. The banner says
   * so, because a file with a credential in it should never be a surprise to its owner.
   */
  protected openCodeConfig(issued: IssuedApiKey): string {
    return JSON.stringify(
      {
        $schema: 'https://opencode.ai/config.json',
        provider: {
          aira: {
            npm: '@ai-sdk/google',
            name: 'AIRA Gateway',
            options: { baseURL: `${window.location.origin}/gw/v1beta`, apiKey: issued.api_key },
            models: Object.fromEntries(
              this.toolModels().map((model) => [model, { name: `${model} via AIRA` }]),
            ),
          },
        },
        model: `aira/${this.toolModels()[0] ?? 'qwen2.5:3b'}`,
      },
      null,
      2,
    );
  }

  /** Models the catalog declares able to call tools — the only ones an assistant can use. */
  protected readonly toolModels = signal<string[]>([]);

  protected downloadOpenCodeConfig(issued: IssuedApiKey): void {
    const blob = new Blob([this.openCodeConfig(issued)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'opencode.json';
    link.click();
    URL.revokeObjectURL(url);
  }

  protected copyOpenCodeConfig(issued: IssuedApiKey): void {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard;
    if (!clipboard) {
      this.copyFailed.set(true);
      return;
    }
    void clipboard.writeText(this.openCodeConfig(issued)).then(
      () => this.configCopied.set(true),
      () => this.copyFailed.set(true),
    );
  }

  protected readonly configCopied = signal(false);

  protected dismissIssued(): void {
    this.issued.set(null);
    this.copied.set(false);
    this.configCopied.set(false);
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
      this.storePayloads() !== (this.useCase()?.store_payloads ?? true) ||
      this.restrictMembers() !== (this.useCase()?.restrict_members_to_own_requests ?? false)
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
        restrict_members_to_own_requests: this.restrictMembers(),
        ...(store && days != null ? { retention_days: days } : {}),
      }),
      {
        failure: 'Could not change the data-protection settings.',
        success: (useCase: UseCase) => {
          this.useCase.set(useCase);
          this.retentionDays.set(useCase.retention_days ?? null);
          this.storePayloads.set(useCase.store_payloads ?? true);
          this.restrictMembers.set(useCase.restrict_members_to_own_requests ?? false);
          this.feedback.succeed(
            store
              ? `Prompts and responses are now kept for ${days} day(s). Anything already past that is removed on the next run.`
              : 'Prompts and responses are no longer stored for this use case. Anything already stored is removed on the next run.',
          );
        },
      },
    );
  }

  /**
   * What this use case may do — function calling and prompt caching, with the cache's lifetime.
   *
   * A save of its own, because it is a different question from data protection and used to share
   * that panel's form: two capability switches sat between "store prompts and responses" and
   * "keep them for N days", which a reader treats as one setting, and turning caching on then
   * reported success in a sentence about retention.
   */
  protected capabilitiesChanged(): boolean {
    return (
      this.toolsEnabled() !== (this.useCase()?.tools_enabled ?? false) ||
      this.includeReasoning() !== (this.useCase()?.include_reasoning ?? false) ||
      this.promptCaching() !== (this.useCase()?.prompt_caching_enabled ?? false) ||
      this.cacheTtl() !== (this.useCase()?.prompt_cache_ttl ?? '5m')
    );
  }

  protected canSaveCapabilities(): boolean {
    return this.capabilitiesChanged() && !this.feedback.busy();
  }

  protected saveCapabilities(): void {
    if (!this.canSaveCapabilities()) {
      return;
    }
    const caching = this.promptCaching();
    const ttl = this.cacheTtl();
    this.feedback.run(
      this.service.update(this.slug, {
        tools_enabled: this.toolsEnabled(),
        include_reasoning: this.includeReasoning(),
        prompt_caching_enabled: caching,
        prompt_cache_ttl: ttl,
      }),
      {
        failure: 'Could not change what this use case may do.',
        success: (useCase: UseCase) => {
          this.useCase.set(useCase);
          this.toolsEnabled.set(useCase.tools_enabled ?? false);
          this.includeReasoning.set(useCase.include_reasoning ?? false);
          this.promptCaching.set(useCase.prompt_caching_enabled ?? false);
          this.cacheTtl.set(useCase.prompt_cache_ttl ?? '5m');
          // Says what changed *and* where the consequence shows up: a saving nobody can see is a
          // saving nobody believes (`FRD-133` FR-10).
          this.feedback.succeed(
            caching
              ? `Caching is on, keeping the prefix for ${ttl === '1h' ? 'an hour' : 'five minutes'}. The Cached share on this page shows how much of the input it catches.`
              : 'Saved. Prompt caching is off, so every request is charged in full.',
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
