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
 * The register of processing activities (`FRD-608`).
 *
 * The owner asked for *"an overview for IT Steuerung where they can list use cases, the description
 * in them, the models used, all the controls like how many days data is stored, and generally how
 * the data processing happens"* — and the answer turned out to be a **shape**, not data and not a
 * permission. IT Steuerung already saw every use case and every field already existed; what did not
 * exist was one table you can compare rows in. *Which use cases store prompts? Which keep them
 * longer than thirty days? Which were processed outside the EU?* are not questions anybody answers
 * by opening forty detail pages.
 *
 * So this page is deliberately a **table and nothing else**: no editing (`ADR-0007` makes governance
 * read-only, and a register that can change what it registers is not a register), no charts, and no
 * second permission model — the gateway scopes it by the same `visible_scope` the report uses.
 *
 * The CSV is the deliverable rather than a convenience: printed, it is close to a *Verzeichnis von
 * Verarbeitungstätigkeiten*, assembled from configuration the gateway actually enforces rather than
 * from a spreadsheet somebody maintains beside it.
 */
@Component({
  selector: 'app-register-page',
  imports: [FormsModule, InfoHint, TablePager],
  templateUrl: './register-page.html',
  // One banner for the page, the same rule as every other screen here.
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

  // Zoneless: every piece of form state is a signal, or changing it from code renders nothing.
  protected readonly preset = signal<Preset>('this-month');
  protected readonly from = signal('');
  protected readonly to = signal('');
  protected readonly search = signal('');
  /** Show only rows with something a governance reader would act on. Off by default: a register
   *  is a register first, and a filter that hides the compliant rows by default would make the
   *  document it prints incomplete without saying so. */
  protected readonly findingsOnly = signal(false);

  /**
   * Which rows are open, by slug.
   *
   * A **set**, where the request list (`traces-tab`) keeps a single id — and the difference is the
   * point of this screen rather than a preference. Opening a request fetches its payload, so one
   * at a time is both cheaper and the only thing a reader can read. Here everything is already
   * loaded, and the question a register is opened to answer is a *comparison*: two use cases'
   * models, or their retention, side by side. One-at-a-time would make the reader close the row
   * they are comparing against.
   *
   * Reset on nothing. A row that scrolls out of the page keeps its state, so paging back returns
   * the reader to what they had open rather than to a collapsed table they have to rebuild.
   */
  private readonly opened = signal<ReadonlySet<string>>(new Set());

  protected readonly entries = computed(() => this.register()?.use_cases ?? []);

  /**
   * Rows a reader would act on: a region the configuration does not name, or a released model the
   * installation will not serve. Both are *disagreements* between what was decided and what is —
   * which is the only kind of thing a read-only screen can usefully surface.
   */
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
   * Models the two planes disagree about (`FRD-608` §4).
   *
   * Management authors the catalogue and the gateway receives it over Kafka, so the two lists
   * should be the same list — and **nothing compared them**. A model in the gateway and not in
   * Management is one it could serve that no screen shows and no role can remove; a model in
   * Management and not in the gateway is a configuration change that never arrived, which is the
   * failure `consumer.apply` now logs and nothing displays.
   *
   * `null` while either half is still unknown — a diff computed against a list that has not
   * arrived would report every model as missing, which is the loudest possible way to be wrong.
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
    // Management's catalogue, for the comparison. Failing to reach it leaves `authored` null and
    // the comparison unshown — never a diff against an empty list, which would report every model
    // as missing from a plane that is merely unreachable.
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
        // **Never an empty table on a failed load.** An empty register and an unreachable gateway
        // look identical on screen and mean opposite things, and this is the one screen where
        // somebody would take the first as evidence.
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
    // A new Set rather than a mutation: a signal holding the same object it held before notifies
    // nothing, and the row would stay shut until something else on the page happened to render.
    const next = new Set(this.opened());
    if (!next.delete(slug)) {
      next.add(slug);
    }
    this.opened.set(next);
  }

  /** What is worth acting on in this row, in the words a reader needs. */
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
        // Released immediately: an object URL held open pins the blob in memory for the life of
        // the page, and somebody exporting a dozen periods would keep every one of them.
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
