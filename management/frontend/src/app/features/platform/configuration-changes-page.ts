import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CONFIGURATION_KINDS, ConfigurationChange, ConfigurationKind } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { ServerTableView } from '../../core/ui/server-table-view';
import { TablePager } from '../../core/ui/table-pager';
import { ZonedPipe } from '../../core/ui/zoned.pipe';

/** One page of the history, as the server pages it. */
const PAGE = 25;

/** What changed, in words. */
export const KIND_LABELS: Record<ConfigurationKind, string> = {
  use_case: 'Use case',
  membership: 'Member',
  group_grant: 'Group grant',
  api_key: 'API key',
  pipeline: 'Pipeline',
  budget: 'Budget',
  rate_limit: 'Rate limit',
  anomaly_rule: 'Anomaly rule',
  model: 'Model',
  role: 'Role',
};

/** What was done, in words; an action the console does not know is shown as the server wrote it. */
export const ACTION_LABELS: Record<string, string> = {
  created: 'Created',
  updated: 'Changed',
  saved: 'Saved',
  granted: 'Granted',
  revoked: 'Revoked',
  removed: 'Removed',
  deleted: 'Deleted',
  retired: 'Retired',
  purged: 'Purged',
};

/** One field's value as text: a string as it is, anything else as JSON, nothing as a dash. */
export function shown(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/**
 * The configuration history (`FRD-632`): every change to the configuration the gateway enforces —
 * who made it, whom it concerns, when, and each field before and after. Only for the roles that may
 * see people, since every row names who changed something; the server authorises every page.
 */
@Component({
  selector: 'app-configuration-changes-page',
  imports: [FormsModule, ZonedPipe, TablePager],
  templateUrl: './configuration-changes-page.html',
  providers: [PageFeedback],
})
export class ConfigurationChangesPage implements OnInit {
  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly kinds = CONFIGURATION_KINDS;
  protected readonly kindLabels = KIND_LABELS;
  protected readonly shown = shown;

  protected readonly kind = signal<ConfigurationKind | ''>('');
  protected readonly useCase = signal('');
  /** The one change whose fields are open. */
  protected readonly openRow = signal<number | null>(null);

  protected readonly view = new ServerTableView<ConfigurationChange>(
    (_query, page) =>
      this.service.configurationChanges({
        kind: this.kind(),
        useCase: this.useCase().trim(),
        page,
        pageSize: PAGE,
      }),
    (response) => this.feedback.fail(response, 'Could not load the configuration history.'),
  );

  ngOnInit(): void {
    this.view.start();
  }

  /** A changed filter asks again, from the first page. */
  protected filter(): void {
    this.view.page.set(1);
    this.view.reload();
  }

  protected toggle(id: number, event?: Event): void {
    event?.stopPropagation();
    this.openRow.update((open) => (open === id ? null : id));
  }

  protected action(change: ConfigurationChange): string {
    return ACTION_LABELS[change.action] ?? change.action;
  }

  /** Who made it: the person, else the process that did. */
  protected who(change: ConfigurationChange): string {
    return change.actor || (change.process ? `(${change.process})` : '—');
  }
}
