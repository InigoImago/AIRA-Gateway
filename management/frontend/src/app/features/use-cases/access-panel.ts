import { Component, DestroyRef, OnInit, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable, Subject, Subscription, debounceTime, distinctUntilChanged } from 'rxjs';
import { DirectoryEntry, GroupGrant, Membership } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';

/** How long to wait after the last keystroke before asking the directory. */
const TYPING_PAUSE_MS = 250;

/**
 * Who can reach this use case, and as what (`FRD-209`).
 *
 * One panel for **both kinds of grant**, because the question a person is asking is "who should
 * get this" and not "am I about to name a group or a person". Two panels would make them choose
 * the mechanism before they have chosen the recipient, which is backwards — and would put the
 * search box on one of them.
 *
 * A group grant is the point of the feature: a department has a group, the identity provider knows
 * who is in it, and somebody joining or leaving takes effect without anybody editing an access
 * list here. A person is still grantable, for the cases where a group would be one person.
 */
@Component({
  selector: 'app-access-panel',
  imports: [FormsModule, InfoHint],
  templateUrl: './access-panel.html',
})
export class AccessPanel implements OnInit {
  readonly slug = input.required<string>();
  readonly canManage = input(false);
  /** The members the parent already loaded — it owns them, and its tab count reads from them. */
  readonly members = input<Membership[]>([]);
  /** Raised when a grant changes, so the parent can reload what it owns. */
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly feedback = inject(PageFeedback);

  protected readonly grants = signal<GroupGrant[]>([]);
  protected readonly loading = signal(true);

  // The picker.
  protected readonly query = signal('');
  protected readonly results = signal<DirectoryEntry[]>([]);
  protected readonly searching = signal(false);
  /**
   * Where the last answer came from.
   *
   * `local` means Keycloak could not be asked, and the console says so: an empty list from a
   * directory nobody could reach reads exactly like "no such group", and those are different
   * answers to act on.
   */
  protected readonly source = signal<'keycloak' | 'local' | 'none'>('none');
  protected readonly hint = signal('');
  protected readonly picked = signal<DirectoryEntry | null>(null);
  protected readonly role = signal<'admin' | 'user'>('user');

  private readonly typed = new Subject<string>();
  private inFlight: Subscription | null = null;

  constructor() {
    const typing = this.typed
      .pipe(debounceTime(TYPING_PAUSE_MS), distinctUntilChanged())
      .subscribe((value) => this.lookup(value));
    this.destroyRef.onDestroy(() => {
      typing.unsubscribe();
      this.inFlight?.unsubscribe();
    });
  }

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.service.groupGrants(this.slug()).subscribe({
      next: (grants) => {
        this.grants.set(grants);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.feedback.fail(response, 'Could not load who has access to this use case.');
      },
    });
  }

  protected search(value: string): void {
    this.query.set(value);
    this.picked.set(null);
    this.typed.next(value);
  }

  protected pick(entry: DirectoryEntry): void {
    this.picked.set(entry);
    this.query.set(entry.label);
    this.results.set([]);
  }

  protected grant(): void {
    const entry = this.picked();
    if (!entry) return;
    // Typed as the wider of the two on purpose: `feedback.run` only cares that something
    // completed, and narrowing here would mean two near-identical blocks that can drift.
    const request: Observable<unknown> =
      entry.kind === 'group'
        ? this.service.grantGroup(this.slug(), entry.id, this.role())
        : this.service.addMember(this.slug(), entry.id, this.role());
    this.feedback.run(request, {
      failure: `Could not grant access to ${entry.label}.`,
      success: () => {
        this.feedback.succeed(
          entry.kind === 'group'
            ? `${entry.label} granted. Everyone in that group reaches this use case from their ` +
                `next sign-in; it takes a few seconds to reach the gateway.`
            : `${entry.label} granted.`,
        );
        this.reset();
        this.load();
        this.changed.emit();
      },
    });
  }

  protected revokeGroup(grant: GroupGrant): void {
    const question =
      `Revoke access for ${grant.group_path}? Everyone who reaches this use case only through ` +
      `that group loses it. Anybody granted separately keeps theirs.`;
    if (!this.confirmService.ask(question)) return;
    this.feedback.run(this.service.revokeGroup(this.slug(), grant.group_path), {
      failure: 'Could not revoke this group.',
      success: () => {
        this.feedback.succeed(`${grant.group_path} revoked.`);
        this.load();
        this.changed.emit();
      },
    });
  }

  protected revokeMember(member: Membership): void {
    if (!this.confirmService.ask(`Remove ${member.username} from this use case?`)) return;
    this.feedback.run(this.service.removeMember(this.slug(), member.username), {
      failure: 'Could not remove this person.',
      success: () => {
        this.feedback.succeed(`${member.username} removed.`);
        this.changed.emit();
      },
    });
  }

  private reset(): void {
    this.query.set('');
    this.picked.set(null);
    this.results.set([]);
    this.role.set('user');
  }

  private lookup(value: string): void {
    // Switched, not queued: a slow answer for "ku" must not land after a fast one for "kunden"
    // and repopulate the list with results nobody is looking at any more.
    this.inFlight?.unsubscribe();
    if (value.trim().length < 2) {
      this.results.set([]);
      this.source.set('none');
      return;
    }
    this.searching.set(true);
    this.inFlight = this.service.directory(value.trim()).subscribe({
      next: (page) => {
        this.results.set(page.results);
        this.source.set(page.source);
        this.hint.set(page.hint ?? '');
        this.searching.set(false);
      },
      error: (response: unknown) => {
        this.searching.set(false);
        this.results.set([]);
        this.feedback.fail(response, 'Could not search the directory.');
      },
    });
  }
}
