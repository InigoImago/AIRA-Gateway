import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ApiKey, IssuedApiKey, Membership, UseCase } from '../../core/api/models';
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
  protected readonly tab = signal<'overview' | 'members' | 'keys'>('overview');
  protected readonly showAddMember = signal(false);
  protected readonly showIssueKey = signal(false);
  protected slug = '';
  protected memberUsername = '';
  protected memberRole = 'user';
  protected keyLabel = '';

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    this.load();
  }

  protected load(): void {
    this.service.get(this.slug).subscribe((useCase) => this.useCase.set(useCase));
    this.service.members(this.slug).subscribe((members) => this.members.set(members));
    this.loadKeys();
  }

  protected loadKeys(): void {
    this.service.apiKeys(this.slug).subscribe((keys) => this.apiKeys.set(keys));
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
