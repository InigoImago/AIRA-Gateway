import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, input } from '@angular/core';
import { PermissionInfo, RoleChange, RoleSnapshot } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { ServerTableView } from '../../core/ui/server-table-view';
import { TablePager } from '../../core/ui/table-pager';

/** One page of the log, as the server pages it: the browser holds that page and nothing more. */
const PAGE = 25;

const NOTHING: RoleSnapshot = { label: '', group_path: '', permissions: [] };

export const ACTION_LABELS: Record<RoleChange['action'], string> = {
  created: 'Created',
  updated: 'Changed',
  deleted: 'Deleted',
};

/** What one entry changed: a name or group as before → after, and permissions added or removed. */
export interface ChangeSummary {
  label: string | null;
  group: string | null;
  added: string[];
  removed: string[];
}

/** `before → after` when both exist, else the one that does; `null` when they are the same. */
function transition(before: string, after: string): string | null {
  if (before === after) return null;
  return before && after ? `${before} → ${after}` : after || before;
}

/**
 * One change as a difference. A creation is a difference from nothing and a deletion one to
 * nothing, so all three read the same way.
 */
export function summarise(change: RoleChange): ChangeSummary {
  const before = change.before ?? NOTHING;
  const after = change.after ?? NOTHING;
  return {
    label: transition(before.label, after.label),
    group: transition(before.group_path, after.group_path),
    added: after.permissions.filter((name) => !before.permissions.includes(name)),
    removed: before.permissions.filter((name) => !after.permissions.includes(name)),
  };
}

/**
 * The record of every creation, change and deletion of a role (`FRD-614` FR-6): when, who, which
 * role and what changed. Paged at the server; the page calls `reload()` after each write, which
 * returns to the first page, where the entry a person just made is.
 */
@Component({
  selector: 'app-role-changes',
  imports: [DatePipe, TablePager],
  templateUrl: './role-changes.html',
})
export class RoleChanges implements OnInit {
  /** Every role's name by slug, from `/me`. */
  readonly labels = input<Record<string, string>>({});
  /** For a permission's label beside its name. */
  readonly catalogue = input<PermissionInfo[]>([]);

  private readonly service = inject(UseCaseService);
  private readonly feedback = inject(PageFeedback);

  protected readonly actions = ACTION_LABELS;
  protected readonly view = new ServerTableView<RoleChange>(
    (_query, page) => this.service.roleChanges({ page, pageSize: PAGE }),
    (response) => this.feedback.fail(response, 'Could not load the change log of the roles.'),
  );

  private readonly permissionLabels = computed(
    () => new Map(this.catalogue().map((permission) => [permission.name, permission.label])),
  );

  ngOnInit(): void {
    this.view.start();
  }

  /** The first page again — newest first, so that is where a change just made appears. */
  reload(): void {
    this.view.page.set(1);
    this.view.reload();
  }

  /**
   * The role's name today, else the name the entry recorded (a deleted role is no longer in
   * `/me`), else its slug.
   */
  protected roleName(change: RoleChange): string {
    return this.labels()[change.role] ?? change.after?.label ?? change.before?.label ?? change.role;
  }

  protected summary(change: RoleChange): ChangeSummary {
    return summarise(change);
  }

  protected permissionLabel(name: string): string {
    return this.permissionLabels().get(name) ?? name;
  }
}
