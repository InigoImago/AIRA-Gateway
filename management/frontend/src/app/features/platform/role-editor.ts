import { Component, computed, inject, input, linkedSignal, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { errorMessage } from '../../core/api/error-message';
import { PermissionInfo, Role, RoleDraft } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { SENSITIVE_EXPLAINED, byArea } from './role-catalogue';

/** The directory's answer about one group path, kept with the path it was asked about. */
interface GroupVerdict {
  path: string;
  verdict: 'confirmed' | 'refused' | 'unverified';
  message: string;
}

/** The server's own words about the group field of a refused request, if it gave any. */
function groupMessage(response: unknown): string | null {
  const details = (response as { error?: { error?: { details?: unknown } } } | null)?.error?.error
    ?.details;
  const messages = (details as Record<string, unknown> | null | undefined)?.['group_path'];
  return Array.isArray(messages) && messages.length ? messages.join(' ') : null;
}

/**
 * Why a group was not confirmed. A refusal (400) carries the server's reason. Anything else means
 * nobody could say whether the group exists, and an unverified group is never bound (`FRD-614`
 * FR-4), so the result says that in plain words rather than as a generic failure.
 */
function verdictOf(path: string, response: unknown): GroupVerdict {
  if ((response as { status?: number } | null)?.status === 400) {
    return {
      path,
      verdict: 'refused',
      message: groupMessage(response) ?? errorMessage(response, `'${path}' cannot be bound.`),
    };
  }
  const why = errorMessage(response, 'The directory could not be asked.');
  return {
    path,
    verdict: 'unverified',
    message: `The group could not be verified, so it is not bound. ${why}`,
  };
}

/**
 * The form that creates a role or changes one (`FRD-614` FR-9).
 *
 * A group is bound only once Keycloak confirmed it, so Save waits for a successful check of the
 * path as it stands now; editing the path afterwards asks for a new one. A built-in role keeps its
 * name and group from configuration, so only its permissions are offered. Reserved permissions are
 * never offered: a role that could manage roles could raise itself.
 *
 * Reports through the page's `PageFeedback` and raises `saved` or `deleted`, so the page reloads
 * the roles and the change log it owns.
 */
@Component({
  selector: 'app-role-editor',
  imports: [FormsModule],
  templateUrl: './role-editor.html',
})
export class RoleEditor {
  /** The role being changed, or `null` for a new one. */
  readonly role = input<Role | null>(null);
  readonly catalogue = input<PermissionInfo[]>([]);
  readonly saved = output<Role>();
  readonly deleted = output<string>();
  readonly cancelled = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);
  protected readonly sensitiveExplained = SENSITIVE_EXPLAINED;

  // The form, reset to the role whenever another one is chosen. Signals: the app is zoneless.
  protected readonly label = linkedSignal(() => this.role()?.label ?? '');
  protected readonly groupPath = linkedSignal(() => this.original());
  protected readonly permissions = linkedSignal<ReadonlySet<string>>(
    () => new Set(this.role()?.permissions ?? []),
  );
  private readonly check = linkedSignal<Role | null, GroupVerdict | null>({
    source: this.role,
    computation: () => null,
  });
  protected readonly checking = signal(false);

  protected readonly isNew = computed(() => this.role() === null);
  /** IT Steuerung: its name and group come from configuration, so only what it may do changes. */
  protected readonly onlyPermissions = computed(() => this.role()?.builtin ?? false);
  protected readonly areas = computed(() => byArea(this.catalogue().filter((p) => !p.reserved)));
  protected readonly trimmedPath = computed(() => this.groupPath().trim());

  /** The directory's answer, shown only while it is about the path in the field. */
  protected readonly currentCheck = computed(() => {
    const check = this.check();
    return check && check.path === this.trimmedPath() ? check : null;
  });

  /** A new role always binds a group; a custom role binds one again only when its path changed. */
  protected readonly needsCheck = computed(
    () => this.isNew() || (!this.onlyPermissions() && this.trimmedPath() !== this.original()),
  );

  protected readonly dirty = computed(
    () =>
      this.label().trim() !== (this.role()?.label ?? '') ||
      this.trimmedPath() !== this.original() ||
      !this.samePermissions(),
  );

  /** Why Save is not offered, in words; `null` when nothing stands in the way. */
  protected readonly blocker = computed(() => {
    if (!this.onlyPermissions() && !this.label().trim()) return 'Give the role a name.';
    if (this.needsCheck() && this.currentCheck()?.verdict !== 'confirmed') {
      return this.isNew()
        ? 'Check the group before saving: a role is bound only to a group Keycloak confirmed.'
        : 'The group was changed: check it before saving.';
    }
    return null;
  });

  protected readonly canSave = computed(
    () => !this.feedback.busy() && this.blocker() === null && (this.isNew() || this.dirty()),
  );

  protected has(name: string): boolean {
    return this.permissions().has(name);
  }

  protected toggle(name: string, checked: boolean): void {
    this.permissions.update((held) => {
      const next = new Set(held);
      if (checked) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  protected checkGroup(): void {
    const path = this.trimmedPath();
    if (!path || this.checking()) return;
    this.checking.set(true);
    this.service.checkGroup(path).subscribe({
      next: () => {
        this.checking.set(false);
        this.check.set({
          path,
          verdict: 'confirmed',
          message: `Keycloak has the group ${path}, and it confers no other role.`,
        });
      },
      error: (response: unknown) => {
        this.checking.set(false);
        this.check.set(verdictOf(path, response));
      },
    });
  }

  protected save(): void {
    if (!this.canSave()) return;
    const role = this.role();
    const label = this.label().trim();
    const request = role
      ? this.service.updateRole(role.slug, this.changes(role))
      : this.service.createRole({
          label,
          group_path: this.trimmedPath(),
          permissions: this.ordered(),
        });
    this.feedback.run(request, {
      failure: role ? 'Could not save the role.' : 'Could not create the role.',
      success: (saved) => {
        this.feedback.succeed(
          `${saved.label} is ${role ? 'saved' : 'created'}. ` +
            'It takes a few seconds to reach every gateway instance.',
        );
        this.saved.emit(saved);
      },
    });
  }

  protected remove(): void {
    const role = this.role();
    if (!role || role.builtin) return;
    const question =
      `Delete the role ${role.label}? Everybody in ${this.original()} loses what it grants on ` +
      'their next request. The change log keeps the record.';
    if (!this.confirmService.ask(question)) return;
    this.feedback.run(this.service.deleteRole(role.slug), {
      failure: 'Could not delete the role.',
      success: () => {
        this.feedback.succeed(`${role.label} is deleted.`);
        this.deleted.emit(role.slug);
      },
    });
  }

  /** The path the role is bound to now; a custom role has exactly one. */
  private original(): string {
    return this.role()?.group_paths[0] ?? '';
  }

  /** The ticked permissions in the catalogue's order, so the change log reads like the form. */
  private ordered(): string[] {
    const held = this.permissions();
    return this.catalogue()
      .filter((permission) => held.has(permission.name))
      .map((permission) => permission.name);
  }

  private samePermissions(): boolean {
    const held = this.permissions();
    const before = this.role()?.permissions ?? [];
    return held.size === before.length && before.every((name) => held.has(name));
  }

  /** Only what changed, and never a name or group for a built-in role (the server refuses both). */
  private changes(role: Role): Partial<RoleDraft> {
    const changes: Partial<RoleDraft> = {};
    if (!this.samePermissions()) changes.permissions = this.ordered();
    if (role.builtin) return changes;
    const label = this.label().trim();
    if (label !== role.label) changes.label = label;
    if (this.trimmedPath() !== this.original()) changes.group_path = this.trimmedPath();
    return changes;
  }
}
