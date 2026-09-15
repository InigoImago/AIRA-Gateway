import { TestBed } from '@angular/core/testing';
import { Observable, of } from 'rxjs';
import { UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { CapabilitiesPanel } from './capabilities-panel';

type Writable<T> = { set: (v: T) => void; (): T };

interface Panel {
  toolsEnabled: Writable<boolean>;
  includeReasoning: Writable<boolean>;
  promptCaching: Writable<boolean>;
  cacheTtl: Writable<string>;
  saveCapabilities: () => void;
}

const USE_CASE: UseCase = {
  slug: 'demo-uc',
  name: 'Demo',
  description: 'A demo use case',
  processing_notes: '',
  retention_days: 7,
  store_payloads: true,
  permissions: { can_admin: true, can_manage: true, is_member: true },
};

/** The fields an `update:` call carried — asserted by value, so a new setting breaks nothing. */
function sentUpdate(calls: string[]): Record<string, unknown> {
  const call = calls.find((entry) => entry.startsWith('update:'));
  return call ? JSON.parse(call.slice('update:'.length)) : {};
}

function setup(
  options: { useCase?: UseCase; update?: Observable<UseCase>; canManage?: boolean } = {},
) {
  TestBed.resetTestingModule();
  const useCase = options.useCase ?? USE_CASE;
  const calls: string[] = [];
  const saved: UseCase[] = [];
  const service = {
    update: (_s: string, changes: Partial<UseCase>) => {
      calls.push(`update:${JSON.stringify(changes)}`);
      return options.update ?? of({ ...useCase, ...changes });
    },
  };
  TestBed.configureTestingModule({
    imports: [CapabilitiesPanel],
    providers: [{ provide: UseCaseService, useValue: service }, PageFeedback],
  });
  const fixture = TestBed.createComponent(CapabilitiesPanel);
  fixture.componentRef.setInput('slug', 'demo-uc');
  fixture.componentRef.setInput('useCase', useCase);
  fixture.componentRef.setInput('canManage', options.canManage ?? true);
  fixture.componentRef.setInput('shown', true);
  fixture.componentInstance.saved.subscribe((value) => saved.push(value));
  fixture.detectChanges();
  return {
    fixture,
    calls,
    saved,
    feedback: TestBed.inject(PageFeedback),
    component: fixture.componentInstance as unknown as Panel,
    html: () => fixture.nativeElement as HTMLElement,
  };
}

/**
 * `include_reasoning` (`FRD-135`), `tools_enabled` (`FRD-131`) and `prompt_caching_enabled`
 * (`FRD-133`) must each have a way in: a capability only the API can switch is one nobody notices
 * is missing.
 */
describe('CapabilitiesPanel — the switches', () => {
  it('offers both switches to an administrator and sends them', () => {
    const harness = setup();

    const dom = harness.html();
    const caching = dom.querySelector<HTMLInputElement>('#prompt-caching');
    const tools = dom.querySelector<HTMLInputElement>('#tools-enabled');
    expect(caching, 'no way to turn prompt caching on').not.toBeNull();
    expect(tools, 'no way to turn tool calling on').not.toBeNull();

    caching!.checked = true;
    caching!.dispatchEvent(new Event('change'));
    harness.fixture.detectChanges();
    harness.component.saveCapabilities();

    expect(harness.calls.some((c) => c.includes('"prompt_caching_enabled":true'))).toBe(true);
    expect(harness.saved[0]?.prompt_caching_enabled).toBe(true);
  });

  it('does not report a retention change when what changed was a capability', () => {
    const harness = setup();
    harness.component.promptCaching.set(true);

    harness.component.saveCapabilities();

    const sent = sentUpdate(harness.calls);
    expect(sent).not.toHaveProperty('retention_days');
    expect(sent).not.toHaveProperty('store_payloads');
  });

  it('explains what caching changes, and what it does not', () => {
    // It changes the price, never the answer; a reader deciding whether to enable it needs that.
    const harness = setup();

    (harness.html().querySelector('[data-testid="info-prompt-caching"]') as HTMLElement).click();
    harness.fixture.detectChanges();

    const help = harness.html().querySelector('[data-testid="help-prompt-caching"]');
    expect(help?.textContent).toContain('never the answer');
    expect(help?.textContent).toContain('whole organisation');
  });

  it('says that a declared function is carried and never run', () => {
    // The gateway runs nothing (`ADR-0013`); an administrator must not expect a sandbox.
    const harness = setup();

    (harness.html().querySelector('[data-testid="info-tools-enabled"]') as HTMLElement).click();
    harness.fixture.detectChanges();

    const help = harness.html().querySelector('[data-testid="help-tools-enabled"]');
    expect(help?.textContent).toContain('never executes');
  });

  it('starts with reasoning on and sends it when an administrator turns it off', () => {
    const harness = setup({ useCase: { ...USE_CASE, include_reasoning: true } });

    const reasoning = harness.html().querySelector<HTMLInputElement>('#include-reasoning');
    expect(reasoning?.checked, 'reasoning should start on').toBe(true);

    reasoning!.checked = false;
    reasoning!.dispatchEvent(new Event('change'));
    harness.fixture.detectChanges();
    harness.component.saveCapabilities();

    expect(sentUpdate(harness.calls)).toMatchObject({
      include_reasoning: false,
      tools_enabled: false,
    });
  });

  it('states every setting to a reader who cannot change them', () => {
    const harness = setup({
      useCase: {
        ...USE_CASE,
        include_reasoning: false,
        tools_enabled: true,
        prompt_caching_enabled: false,
      },
      canManage: false,
    });

    const readonly = harness.html().querySelector('[data-testid="capabilities-readonly"]');
    expect(harness.html().querySelector('#prompt-caching')).toBeNull();
    expect(readonly?.textContent).toMatch(
      /Reasoning is\s+off, function\s+calling is\s+on and prompt caching is\s+off/,
    );
  });
});

/**
 * The cache lifetime (`FRD-133`): a write costs about a quarter extra for five minutes and about
 * double for an hour, so only somebody's own traffic settles it — and it matters only once
 * caching is on.
 */
describe('CapabilitiesPanel — tuning the cache', () => {
  it('offers the lifetime only when caching is switched on', () => {
    const harness = setup();

    expect(harness.html().querySelector('#cache-ttl'), 'offered before caching is on').toBeNull();

    harness.component.promptCaching.set(true);
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('#cache-ttl')).not.toBeNull();
  });

  it('sends the chosen lifetime', () => {
    const harness = setup();
    harness.component.promptCaching.set(true);
    harness.component.cacheTtl.set('1h');

    harness.component.saveCapabilities();

    expect(sentUpdate(harness.calls)).toMatchObject({
      prompt_caching_enabled: true,
      prompt_cache_ttl: '1h',
    });
  });

  it('sends nothing when nothing was changed', () => {
    // The method guards itself, not only the disabled button: a keyboard submit must not post an
    // unchanged object and report success.
    const harness = setup();

    harness.component.saveCapabilities();

    expect(harness.calls).toEqual([]);
  });

  it('reads a response that omits the capability fields as their defaults', () => {
    // What grants a capability reads as off and the cheap lifetime, because absence of information
    // is not permission. Reasoning reads as on, the use-case default (`FRD-135`).
    const harness = setup({ update: of({ slug: 'demo-uc', name: 'Demo' } as UseCase) });
    harness.component.promptCaching.set(true);
    harness.component.cacheTtl.set('1h');

    harness.component.saveCapabilities();
    harness.fixture.detectChanges();

    expect(harness.component.promptCaching()).toBe(false);
    expect(harness.component.cacheTtl()).toBe('5m');
    expect(harness.component.toolsEnabled()).toBe(false);
    expect(harness.component.includeReasoning()).toBe(true);
  });

  it('says what the longer lifetime costs, not just that it is longer', () => {
    const harness = setup();
    harness.component.promptCaching.set(true);
    harness.fixture.detectChanges();

    (harness.html().querySelector('[data-testid="info-cache-ttl"]') as HTMLElement).click();
    harness.fixture.detectChanges();

    const help = harness.html().querySelector('[data-testid="help-cache-ttl"]');
    expect(help?.textContent).toContain('about double');
    expect(harness.html().querySelector('#cache-ttl')?.textContent).toContain('costs about double');
  });

  it('reports a refused save instead of appearing to succeed', () => {
    const harness = setup({
      update: new Observable<UseCase>((subscriber) =>
        subscriber.error({ status: 403, error: { error: { message: 'Not an admin.' } } }),
      ),
    });
    harness.component.promptCaching.set(true);

    harness.component.saveCapabilities();

    expect(harness.feedback.error()).toBe('Not an admin.');
    expect(harness.saved).toEqual([]);
  });
});
