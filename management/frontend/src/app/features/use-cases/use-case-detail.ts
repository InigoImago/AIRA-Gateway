import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  ApiKey,
  Budget,
  BudgetUsage,
  IssuedApiKey,
  Membership,
  UseCase,
} from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';

@Component({
  selector: 'app-use-case-detail',
  imports: [FormsModule, RouterLink],
  templateUrl: './use-case-detail.html',
})
export class UseCaseDetail implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly service = inject(UseCaseService);

  protected readonly useCase = signal<UseCase | null>(null);
  protected readonly members = signal<Membership[]>([]);
  protected readonly apiKeys = signal<ApiKey[]>([]);
  protected readonly issued = signal<IssuedApiKey | null>(null);
  protected readonly copied = signal(false);
  protected readonly tab = signal<'overview' | 'members' | 'keys' | 'budgets'>('overview');
  protected readonly showAddMember = signal(false);
  protected readonly showIssueKey = signal(false);
  protected readonly showAddBudget = signal(false);
  protected readonly budgets = signal<Budget[]>([]);
  protected readonly usage = signal<Record<number, BudgetUsage>>({});
  protected slug = '';
  protected memberUsername = '';
  protected memberRole = 'user';
  protected keyLabel = '';
  protected budgetScope: 'use_case' | 'member' = 'use_case';
  protected budgetSubject = '';
  protected budgetPeriod: 'day' | 'month' = 'month';
  protected budgetTokens: number | null = null;
  protected budgetRequests: number | null = null;

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    this.load();
  }

  protected load(): void {
    this.service.get(this.slug).subscribe((useCase) => this.useCase.set(useCase));
    this.service.members(this.slug).subscribe((members) => this.members.set(members));
    this.loadKeys();
    this.loadBudgets();
  }

  protected loadKeys(): void {
    this.service.apiKeys(this.slug).subscribe((keys) => this.apiKeys.set(keys));
  }

  protected loadBudgets(): void {
    this.service.budgets(this.slug).subscribe((budgets) => this.budgets.set(budgets));
    this.service.budgetUsage(this.slug).subscribe({
      next: ({ usage }) => {
        const map: Record<number, BudgetUsage> = {};
        for (const entry of usage) map[entry.id] = entry;
        this.usage.set(map);
      },
      error: () => this.usage.set({}), // gateway not reachable → show limits without consumption
    });
  }

  protected addBudget(): void {
    if (this.budgetTokens == null && this.budgetRequests == null) return;
    const budget: Budget = {
      scope: this.budgetScope,
      subject: this.budgetScope === 'member' ? this.budgetSubject : '',
      period: this.budgetPeriod,
      limit_tokens: this.budgetTokens,
      limit_requests: this.budgetRequests,
    };
    this.service.createBudget(this.slug, budget).subscribe(() => {
      this.budgetSubject = '';
      this.budgetTokens = null;
      this.budgetRequests = null;
      this.showAddBudget.set(false);
      this.loadBudgets();
    });
  }

  protected removeBudget(id: number | undefined): void {
    if (id == null) return;
    this.service.deleteBudget(this.slug, id).subscribe(() => this.loadBudgets());
  }

  protected usedFor(budget: Budget): BudgetUsage {
    return (
      (budget.id != null ? this.usage()[budget.id] : undefined) ?? {
        id: budget.id ?? 0,
        used_tokens: 0,
        used_requests: 0,
      }
    );
  }

  protected pct(used: number, limit: number | null | undefined): number {
    return limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  }

  protected addMember(): void {
    if (!this.memberUsername) {
      return;
    }
    this.service.addMember(this.slug, this.memberUsername, this.memberRole).subscribe(() => {
      this.memberUsername = '';
      this.load();
    });
  }

  protected removeMember(username: string): void {
    this.service.removeMember(this.slug, username).subscribe(() => this.load());
  }

  protected issueKey(): void {
    this.service.issueApiKey(this.slug, this.keyLabel).subscribe((issued) => {
      this.keyLabel = '';
      this.copied.set(false);
      this.issued.set(issued);
      this.loadKeys();
    });
  }

  protected dismissIssued(): void {
    this.issued.set(null);
    this.copied.set(false);
  }

  protected copyKey(value: string): void {
    void navigator.clipboard?.writeText(value).then(() => this.copied.set(true));
  }

  protected revokeKey(prefix: string): void {
    this.service.revokeApiKey(this.slug, prefix).subscribe(() => this.loadKeys());
  }
}
