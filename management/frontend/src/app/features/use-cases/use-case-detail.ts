import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { Membership, UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';

@Component({
  selector: 'app-use-case-detail',
  imports: [FormsModule],
  templateUrl: './use-case-detail.html',
})
export class UseCaseDetail implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly service = inject(UseCaseService);

  protected readonly useCase = signal<UseCase | null>(null);
  protected readonly members = signal<Membership[]>([]);
  protected slug = '';
  protected memberUsername = '';
  protected memberRole = 'user';

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    this.load();
  }

  protected load(): void {
    this.service.get(this.slug).subscribe((useCase) => this.useCase.set(useCase));
    this.service.members(this.slug).subscribe((members) => this.members.set(members));
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
}
