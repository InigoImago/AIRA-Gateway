import { Component, OnInit, computed, inject, signal, viewChild } from '@angular/core';
import { MeService } from '../../core/api/me.service';
import { Me, PermissionInfo, Role } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { can } from '../../core/auth/roles';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { SENSITIVE_EXPLAINED, byArea } from './role-catalogue';
import { RoleChanges } from './role-changes';
import { RoleEditor } from './role-editor';

/**
 * The roles and what each may do (`FRD-614` FR-9, `ADR-0025`): Keycloak decides who holds a role,
 * through its group; this page shows, and for a Global Administrator changes, what the role may do.
 *
 * The page loads the roles and the catalogue and owns the table, the selection and the window the
 * form opens in: a creator opens a window, never a form unfolding below the list (`core/ui/modal`).
 * The form is a panel owning its state and mutations, the change log a panel owning its paging, and
 * all three report through this page's one `PageFeedback` (`CLAUDE.md` §3). Managing roles is asked
 * of `role.manage`; a reader without it sees the same roles with no control that would answer 403.
 */
@Component({
  selector: 'app-roles-page',
  imports: [Modal, RoleEditor, RoleChanges],
  templateUrl: './roles-page.html',
  providers: [PageFeedback],
})
export class RolesPage implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  protected readonly feedback = inject(PageFeedback);
  protected readonly sensitiveExplained = SENSITIVE_EXPLAINED;

  private readonly changeLog = viewChild(RoleChanges);

  protected readonly me = signal<Me | null>(null);
  protected readonly roles = signal<Role[]>([]);
  protected readonly catalogue = signal<PermissionInfo[]>([]);
  protected readonly loading = signal(true);
  /** The role whose details are open, by slug, so a reload keeps it open. */
  protected readonly selectedSlug = signal<string | null>(null);
  /** Which form the window holds: a new role, the open role, or none. */
  protected readonly editor = signal<'new' | 'edit' | null>(null);

  protected readonly canManage = computed(() => can(this.me(), 'role.manage'));
  protected readonly labels = computed(() => this.me()?.role_labels ?? {});
  protected readonly selected = computed(
    () => this.roles().find((role) => role.slug === this.selectedSlug()) ?? null,
  );

  /** What the open role holds, grouped by area; reserved permissions included, since it holds them. */
  protected readonly heldAreas = computed(() => {
    const held = new Set(this.selected()?.permissions ?? []);
    return byArea(this.catalogue().filter((permission) => held.has(permission.name)));
  });

  ngOnInit(): void {
    this.loadMe();
    this.loadRoles();
    this.service.permissionCatalogue().subscribe({
      next: (catalogue) => this.catalogue.set(catalogue),
      error: (response: unknown) =>
        this.feedback.fail(response, 'Could not load the permission catalogue.'),
    });
  }

  protected select(slug: string): void {
    this.editor.set(null);
    this.selectedSlug.update((open) => (open === slug ? null : slug));
  }

  protected startNew(): void {
    this.feedback.clear();
    this.editor.set('new');
  }

  protected edit(): void {
    this.feedback.clear();
    this.editor.set('edit');
  }

  /** Closing the window keeps the open role open: the reader is still looking at it. */
  protected close(): void {
    this.editor.set(null);
  }

  /** After a save: the role is open, and the list, the names and the log show the change. */
  protected onSaved(role: Role): void {
    this.editor.set(null);
    this.selectedSlug.set(role.slug);
    this.afterWrite();
  }

  protected onDeleted(): void {
    this.editor.set(null);
    this.selectedSlug.set(null);
    this.afterWrite();
  }

  protected groups(role: Role): string {
    return role.group_paths.join(', ');
  }

  private afterWrite(): void {
    this.loadRoles();
    this.loadMe();
    this.changeLog()?.reload();
  }

  private loadRoles(): void {
    this.service.roles().subscribe({
      next: (roles) => {
        this.roles.set(roles);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.feedback.fail(response, 'Could not load the roles.');
      },
    });
  }

  /**
   * Who is asking, for `role.manage` and the roles' names. Without it the page offers no control,
   * and says why, rather than a form whose every save might be refused.
   */
  private loadMe(): void {
    this.meService.get().subscribe({
      next: (me) => this.me.set(me),
      error: (response: unknown) =>
        this.feedback.fail(response, 'Could not load your account, so nothing can be changed.'),
    });
  }
}
