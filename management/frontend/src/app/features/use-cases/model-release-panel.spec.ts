import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { CatalogModel, UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { ModelReleasePanel } from './model-release-panel';

/**
 * Which models a use case may call (`FRD-308`).
 *
 * The screen that a use case with nothing released depends on: **empty means none**, so a reader
 * arriving at a use case that refuses every request has to find the cause here rather than in the
 * gateway's logs.
 */

const FLASH: CatalogModel = {
  name: 'gemini-2.5-flash',
  display_name: 'Gemini 2.5 Flash',
  provider: 'generative-language',
  approved: true,
  is_priced: true,
  input_price_per_million: '0.075',
};
const SONNET: CatalogModel = {
  name: 'claude-sonnet-4-5',
  provider: 'vertex',
  approved: true,
  is_priced: false,
};
/** In the catalog and **not** released for the installation — so it cannot be released here. */
const DRAFT: CatalogModel = { name: 'draft-1', approved: false, is_priced: false };

@Component({
  selector: 'app-host',
  imports: [ModelReleasePanel],
  providers: [PageFeedback],
  template: `<app-model-release-panel
    slug="uc"
    [canManage]="canManage()"
    [released]="released()"
    (saved)="saved.set($event)"
  />`,
})
class Host {
  readonly canManage = signal(true);
  readonly released = signal<string[]>([]);
  readonly saved = signal<UseCase | null>(null);
}

function setup(
  options: {
    catalog?: Observable<CatalogModel[]>;
    released?: string[];
    canManage?: boolean;
    update?: Observable<UseCase>;
  } = {},
) {
  const updates: Partial<UseCase>[] = [];
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [
      {
        provide: UseCaseService,
        useValue: {
          models: () => options.catalog ?? of([FLASH, SONNET, DRAFT]),
          update: (_slug: string, body: Partial<UseCase>) => {
            updates.push(body);
            return (
              options.update ??
              of({ slug: 'uc', name: 'UC', allowed_models: body.allowed_models } as UseCase)
            );
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.canManage.set(options.canManage ?? true);
  fixture.componentInstance.released.set(options.released ?? []);
  fixture.detectChanges();
  const panel = fixture.debugElement.children[0].componentInstance as unknown as {
    count: () => number;
    dirty: () => boolean;
    chosenList: () => string[];
    setChosen: (names: string[]) => void;
    releasable: () => CatalogModel[];
    withdrawn: () => string[];
    choices: () => { value: string; detail?: string }[];
    save: () => void;
  };
  // The banner belongs to the **page**, not to this panel: one page shows one message, and every
  // panel reports into the same `PageFeedback`. So the outcome is asserted where it is written
  // rather than in this component's own markup, which correctly has none.
  const feedback = fixture.debugElement.children[0].injector.get(PageFeedback);
  return {
    fixture,
    panel,
    updates,
    feedback,
    host: fixture.componentInstance,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    html: () => fixture.nativeElement as HTMLElement,
    /** Open the picker and click one option — the way somebody releases a model. */
    pick: (name: string) => {
      const root = fixture.nativeElement as HTMLElement;
      root.querySelector<HTMLButtonElement>('[data-testid="release-picker-toggle"]')!.click();
      fixture.detectChanges();
      root
        .querySelector<HTMLButtonElement>(`[data-testid="release-picker-option-${name}"]`)!
        .click();
      fixture.detectChanges();
    },
    search: (query: string) => {
      const root = fixture.nativeElement as HTMLElement;
      const input = root.querySelector<HTMLInputElement>('[data-testid="release-picker-search"]')!;
      input.value = query;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    },
  };
}

describe('ModelReleasePanel', () => {
  it('says loudly that a use case with nothing released can call nothing', () => {
    /** The default state of every use case, and the one a reader must not have to infer. A blank
     *  list would leave somebody reading gateway refusals to find out why their team is stuck. */
    const { text } = setup({ released: [] });

    expect(text()).toContain('No model is released');
    expect(text()).toContain('refused');
  });

  it('offers only models the installation has approved', () => {
    /** The server refuses to release an unapproved one, so offering it would be `FRD-206`'s
     *  complaint exactly — a control that invites a click and answers 400. */
    const { panel } = setup();

    expect(panel.releasable().map((m) => m.name)).toEqual([
      'gemini-2.5-flash',
      'claude-sonnet-4-5',
    ]);
  });

  it('releases what was ticked, and says what the use case can now do', () => {
    const { panel, updates, fixture, feedback, host, pick } = setup({ released: [] });

    pick('gemini-2.5-flash');
    expect(panel.dirty()).toBe(true);

    panel.save();
    fixture.detectChanges();

    expect(updates).toEqual([{ allowed_models: ['gemini-2.5-flash'] }]);
    expect(feedback.notice()).toContain('1 model(s) released');
    // The parent takes the answer as its own, or the next render shows what was there before.
    expect(host.saved()?.allowed_models).toEqual(['gemini-2.5-flash']);
  });

  it('says what releasing nothing means rather than reporting a save', () => {
    /** "Saved" after taking the last model away leaves somebody believing their use case works.
     *  The message is about the resulting **state**, not about the form having been submitted. */
    const { panel, fixture, feedback, html } = setup({ released: ['gemini-2.5-flash'] });

    // Taken back from the chip, which is where a reader takes one back from.
    html()
      .querySelector<HTMLButtonElement>('[data-testid="release-picker-remove-gemini-2.5-flash"]')!
      .click();
    fixture.detectChanges();
    panel.save();
    fixture.detectChanges();

    expect(feedback.notice()).toContain('Every request from this use case will be refused');
  });

  it('names a model that was released and has since been withdrawn', () => {
    /** The gateway refuses these anyway — approval is checked before the release is — so a use
     *  case carrying one is already failing for a reason its own screen would never mention. */
    const { panel, text } = setup({ released: ['draft-1'] });

    expect(panel.withdrawn()).toEqual(['draft-1']);
    expect(text()).toContain('no longer approved');
  });

  it('shows a reader who cannot change it what is released, without dead controls', () => {
    /** Read-only means **inert, not un-saveable** (`FRD-206`): the list is worth reading, and
     *  disabled checkboxes would be a row of controls that do nothing. */
    const { html, text } = setup({ canManage: false, released: ['gemini-2.5-flash'] });

    expect(text()).toContain('gemini-2.5-flash');
    expect(text()).toContain('Only an administrator');
    expect(html().querySelector('[data-testid="save-release"]')).toBeNull();
    expect(html().querySelectorAll('input[type="checkbox"]').length).toBe(0);
  });

  it('says there is nothing to release when nothing is approved at all', () => {
    /** Different from "nothing released": one is this screen's to fix, the other is the model
     *  catalog's — and an empty table would look identical for both. */
    const { text } = setup({ catalog: of([DRAFT]) });

    expect(text()).toContain('has been approved for use yet');
  });

  it('reports a catalog it could not load rather than showing an empty one', () => {
    const { feedback } = setup({ catalog: throwError(() => ({ status: 503 })) });

    expect(feedback.error()).toContain('Could not load the model catalog');
  });

  it('filters the list without changing what is released', () => {
    const { panel, html, search } = setup({ released: ['claude-sonnet-4-5'] });

    search('gemini');

    const options = [...html().querySelectorAll('[data-testid^="release-picker-option-"]')];
    expect(options.map((o) => o.getAttribute('data-testid'))).toEqual([
      'release-picker-option-gemini-2.5-flash',
    ]);
    // The one filtered out is still released, and still on screen as a chip — a search that
    // silently unreleased what it hid would be catastrophic and completely invisible.
    expect(panel.chosenList()).toEqual(['claude-sonnet-4-5']);
    expect(html().textContent).toContain('claude-sonnet-4-5');
    expect(panel.count()).toBe(1);
  });

  it('leaves the button dead until something actually changed', () => {
    const { panel, html, pick } = setup({ released: ['gemini-2.5-flash'] });

    expect(panel.dirty()).toBe(false);
    const save = html().querySelector<HTMLButtonElement>('[data-testid="save-release"]')!;
    expect(save.disabled).toBe(true);

    pick('claude-sonnet-4-5');
    expect(html().querySelector<HTMLButtonElement>('[data-testid="save-release"]')!.disabled).toBe(
      false,
    );
  });

  it('frees the save button again when the save failed', () => {
    /** A failed mutation that leaves the control disabled is a screen somebody has to reload, with
     *  the error explaining why but not that they cannot retry. */
    const { panel, fixture, html, feedback, pick } = setup({
      released: [],
      update: throwError(() => ({ status: 500 })),
    });

    pick('gemini-2.5-flash');
    panel.save();
    fixture.detectChanges();

    expect(feedback.error()).toContain('Could not change which models');
    expect(html().querySelector<HTMLButtonElement>('[data-testid="save-release"]')!.disabled).toBe(
      false,
    );
  });
});
