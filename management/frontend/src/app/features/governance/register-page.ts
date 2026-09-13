import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CatalogModel, Register, RegisterEntry } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';
import { Preset, windowFor } from '../../core/ui/periods';
import { TableView } from '../../core/ui/table-view';
import { TablePager } from '../../core/ui/table-pager';

/**
 * The register of processing activities (`FRD-608`): one comparable table of every visible use
 * case — purpose, models, retention, and where its traffic went.
 *
 * Deliberately a table and nothing else: no editing (`ADR-0007` makes governance read-only, and a
 * register that can change what it registers is not a register), and no second permission model —
 * the gateway scopes it by the same `visible_scope` the report uses. The CSV is the deliverable:
 * printed, it is close to a *Verzeichnis von Verarbeitungstätigkeiten*, built from configuration
 * the gateway actually enforces.
 */
@Component({
  selector: 'app-register-page',
  imports: [FormsModule, InfoHint, TablePager],
  templateUrl: './register-page.html',
  providers: [PageFeedback],
})
export class RegisterPage implements OnInit {
  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly register = signal<Register | null>(null);
  /** Management's catalogue — the other half of the comparison in `FRD-608` §4. */
  protected readonly authored = signal<CatalogModel[] | null>(null);
  protected readonly loading = signal(true);
  protected readonly exporting = signal(false);

  protected readonly preset = signal<Preset>('this-month');
  protected readonly from = signal('');
  protected readonly to = signal('');
  protected readonly search = signal('');
  /**
   * Show only rows with a finding. Off by default: a filter that hides the compliant rows would
   * make the printed register incomplete without saying so.
   */
  protected readonly findingsOnly = signal(false);

  /**
   * Which rows are open, by slug — a **set**, unlike the request list's single id: everything here
   * is already loaded, and a register is opened to compare rows side by side. Never reset, so
   * paging back returns the reader to what they had open.
   */
  private readonly opened = signal<ReadonlySet<string>>(new Set());

  protected readonly entries = computed(() => this.register()?.use_cases ?? []);

  /** Rows where what was decided and what is disagree — see `findingsOf`. */
  protected readonly withFindings = computed(() =>
    this.entries().filter((entry) => this.findingsOf(entry).length > 0),
  );

  protected readonly view = new TableView<RegisterEntry>(
    computed(() => (this.findingsOnly() ? this.withFindings() : this.entries())),
    (entry) => `${entry.slug} ${entry.name} ${entry.purpose} ${entry.processing}`,
  );

  /** Whether this caller is seeing the whole installation or only their own use cases. */
  protected readonly seesEverything = computed(() => this.register()?.scope === 'all');

  /**
   * Models the two planes disagree about (`FRD-608` §4). Management authors the catalogue and the
   * gateway receives it over Kafka: a model only in the gateway is one no screen shows and no role
   * can remove; one only in Management is a change that never arrived.
   *
   * `null` while either half is unknown — a diff against a list that has not arrived would report
   * every model as missing.
   */
  protected readonly catalogueDrift = computed(() => {
    const compiled = this.register();
    const managed = this.authored();
    if (!compiled || !managed || compiled.scope !== 'all') {
      return null;
    }
    const inGateway = new Set(compiled.catalogue);
    const inManagement = new Set(managed.map((model) => model.name));
    return {
      onlyInGateway: [...inGateway].filter((name) => !inManagement.has(name)).sort(),
      onlyInManagement: [...inManagement].filter((name) => !inGateway.has(name)).sort(),
    };
  });

  protected readonly hasDrift = computed(() => {
    const drift = this.catalogueDrift();
    return !!drift && (drift.onlyInGateway.length > 0 || drift.onlyInManagement.length > 0);
  });

  ngOnInit(): void {
    this.applyPreset('this-month');
  }

  protected applyPreset(preset: Preset): void {
    this.preset.set(preset);
    if (preset === 'custom') {
      return;
    }
    const window = windowFor(preset, new Date());
    this.from.set(window.from);
    this.to.set(window.to);
    this.load();
  }

  protected load(): void {
    if (!this.from() || !this.to()) {
      return;
    }
    this.loading.set(true);
    // A failure leaves `authored` null and the comparison unshown, never a diff against nothing.
    this.service.models().subscribe({
      next: (models) => this.authored.set(models),
      error: () => this.authored.set(null),
    });
    this.service.register(this.from(), this.to()).subscribe({
      next: (register) => {
        this.register.set(register);
        this.view.search(this.search());
        this.loading.set(false);
      },
      error: (response: unknown) => {
        // Never an empty table on a failed load: an empty register would be taken as evidence.
        this.register.set(null);
        this.feedback.fail(response, 'Could not load the register.');
        this.loading.set(false);
      },
    });
  }

  protected onSearch(term: string): void {
    this.search.set(term);
    this.view.search(term);
  }

  protected toggleFindings(only: boolean): void {
    this.findingsOnly.set(only);
    this.view.search(this.search());
  }

  protected isOpen(slug: string): boolean {
    return this.opened().has(slug);
  }

  protected toggle(slug: string): void {
    // A new Set, not a mutation: a signal given the object it already holds notifies nothing.
    const next = new Set(this.opened());
    if (!next.delete(slug)) {
      next.add(slug);
    }
    this.opened.set(next);
  }

  /**
   * What is worth acting on in this row: a region the configuration does not name, or a released
   * model the installation will not serve.
   */
  protected findingsOf(entry: RegisterEntry): string[] {
    const found: string[] = [];
    if (entry.unexpected_regions.length) {
      found.push(`processed in ${entry.unexpected_regions.join(', ')}`);
    }
    for (const model of entry.models) {
      if (!model.catalogued) {
        found.push(`${model.name} is not in the catalogue`);
      } else if (!model.approved) {
        found.push(`${model.name} is not approved`);
      }
    }
    return found;
  }

  protected download(): void {
    this.exporting.set(true);
    this.service.registerCsv(this.from(), this.to()).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `aira-register_${this.from()}_${this.to()}.csv`;
        link.click();
        // Released at once: an object URL held open pins the blob for the life of the page.
        URL.revokeObjectURL(url);
        this.exporting.set(false);
      },
      error: (response: unknown) => {
        this.feedback.fail(response, 'Could not export the register.');
        this.exporting.set(false);
      },
    });
  }
}
