import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { DirectoryResults, GroupGrant, Membership } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { AccessPanel } from './access-panel';

const GRANT: GroupGrant = {
  group_path: '/ai/kundenservice',
  role: 'user',
  granted_by: 'ucadmin',
  reaches: 4,
};

const MEMBERS: Membership[] = [{ username: 'ada', role: 'admin' }];

const DIRECTORY: DirectoryResults = {
  source: 'keycloak',
  results: [
    { kind: 'group', id: '/ai/kundenservice', label: 'kundenservice', detail: '/ai' },
    { kind: 'user', id: 'ada', label: 'Ada Lovelace', detail: 'ada@example.org' },
  ],
};

@Component({
  selector: 'app-access-host',
  imports: [AccessPanel],
  template: `<app-access-panel
    [slug]="slug()"
    [canManage]="canManage()"
    [members]="members()"
    (changed)="changes.set(changes() + 1)"
  />`,
  providers: [PageFeedback],
})
class Host {
  readonly slug = signal('uc-a');
  readonly canManage = signal(true);
  readonly members = signal<Membership[]>(MEMBERS);
  readonly changes = signal(0);
}

interface Options {
  grants?: Observable<GroupGrant[]>;
  directory?: Observable<DirectoryResults>;
  grant?: Observable<GroupGrant>;
  addMember?: Observable<Membership>;
  canManage?: boolean;
  members?: Membership[];
  confirmAnswer?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const searches: string[] = [];
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [
      {
        provide: UseCaseService,
        useValue: {
          groupGrants: () => options.grants ?? of([GRANT]),
          directory: (query: string) => {
            searches.push(query);
            return options.directory ?? of(DIRECTORY);
          },
          grantGroup: (slug: string, path: string, role: string) => {
            calls.push(`grantGroup:${slug}:${path}:${role}`);
            return options.grant ?? of(GRANT);
          },
          revokeGroup: (slug: string, path: string) => {
            calls.push(`revokeGroup:${slug}:${path}`);
            return of(undefined);
          },
          addMember: (slug: string, username: string, role: string) => {
            calls.push(`addMember:${slug}:${username}:${role}`);
            return options.addMember ?? of({ username, role } as Membership);
          },
          removeMember: (slug: string, username: string) => {
            calls.push(`removeMember:${slug}:${username}`);
            return of(undefined);
          },
        },
      },
      { provide: ConfirmService, useValue: { ask: () => options.confirmAnswer ?? true } },
    ],
  });
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.canManage.set(options.canManage ?? true);
  if (options.members) fixture.componentInstance.members.set(options.members);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  const panel = fixture.debugElement.children[0].componentInstance as unknown as Record<
    string,
    never
  >;
  return {
    fixture,
    element,
    calls,
    searches,
    panel,
    host: fixture.componentInstance,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: (selector: string) => {
      element.querySelector<HTMLElement>(selector)?.click();
      fixture.detectChanges();
    },
    type: (value: string) => {
      const input = element.querySelector<HTMLInputElement>('[data-testid="access-search"]')!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    },
  };
}

describe('AccessPanel — who has access', () => {
  it('shows group grants and people in one list', () => {
    // One panel for both, because the question is "who should get this", not "am I about to name
    // a group or a person".
    const { text } = setup();

    expect(text()).toContain('/ai/kundenservice');
    expect(text()).toContain('ada');
    expect(text()).toContain('group');
    expect(text()).toContain('person');
  });

  it('says when a grant reaches nobody, rather than showing it like a working one', () => {
    // A path that matches nobody is silently inert: nothing fails, nobody gets access, and an
    // access list that showed it identically could not be audited (`FRD-209` FR-8).
    const { text } = setup({ grants: of([{ ...GRANT, reaches: 0 }]) });

    expect(text()).toContain('nobody yet');
  });

  it('says nothing has been granted rather than showing an empty table', () => {
    const { testid } = setup({ grants: of([]), members: [] });

    expect(testid('no-access')?.textContent).toContain('nobody can make a request as it');
  });

  it('reports a failed load instead of an empty list', () => {
    const harness = setup({ grants: throwError(() => ({ status: 500 })) });
    const feedback = harness.fixture.debugElement.injector.get(PageFeedback);

    expect(feedback.error()).toContain('Could not load who has access');
  });
});

describe('AccessPanel — granting', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('searches the directory after a pause, not per keystroke', () => {
    const harness = setup();

    for (const value of ['k', 'ku', 'kun', 'kund']) {
      harness.type(value);
      vi.advanceTimersByTime(50);
    }
    vi.advanceTimersByTime(300);

    expect(harness.searches).toEqual(['kund']);
  });

  it('does not search on one letter', () => {
    // A picker that dumps the whole directory the moment it is focused is a picker nobody reads,
    // and on a real realm it is thousands of rows.
    const harness = setup();
    harness.type('k');
    vi.advanceTimersByTime(300);

    expect(harness.searches).toEqual([]);
  });

  it('offers groups and people together, each labelled', () => {
    const harness = setup();
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();

    const results = harness.testid('access-results');
    expect(results?.textContent).toContain('kundenservice');
    expect(results?.textContent).toContain('Ada Lovelace');
    expect(results?.textContent).toContain('group');
    expect(results?.textContent).toContain('user');
  });

  it('grants a group through the group endpoint', () => {
    const harness = setup();
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    harness.click('[data-testid="access-results"] button');
    harness.click('[data-testid="access-grant"]');

    expect(harness.calls).toContain('grantGroup:uc-a:/ai/kundenservice:user');
  });

  it('grants a person through the member endpoint', () => {
    // The two endpoints differ in who may write to them and in what they distribute. Sending a
    // person to the group one would name a "group path" that is a username.
    const harness = setup();
    harness.type('ada');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    harness.click('[data-testid="access-results"] li:nth-child(2) button');
    harness.click('[data-testid="access-grant"]');

    expect(harness.calls).toContain('addMember:uc-a:ada:user');
  });

  it('carries the chosen role', () => {
    const harness = setup();
    (harness.panel as unknown as { role: { set: (v: string) => void } }).role.set('admin');
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    harness.click('[data-testid="access-results"] button');
    harness.click('[data-testid="access-grant"]');

    expect(harness.calls).toContain('grantGroup:uc-a:/ai/kundenservice:admin');
  });

  it('will not grant until something is picked', () => {
    // Typing a name is not choosing one: the text in the box may match nothing, and granting to a
    // group that does not exist is silently inert.
    const harness = setup();
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();

    const button = harness.testid('access-grant') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('says a group grant follows the group, not the people in it today', () => {
    const harness = setup();
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    harness.click('[data-testid="access-results"] button');
    harness.click('[data-testid="access-grant"]');
    const feedback = harness.fixture.debugElement.injector.get(PageFeedback);

    expect(feedback.notice()).toContain('Everyone in that group');
    expect(feedback.notice()).toContain('next sign-in');
  });

  it('clears the picker once the grant is made', () => {
    const harness = setup();
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    harness.click('[data-testid="access-results"] button');
    harness.click('[data-testid="access-grant"]');

    const input = harness.element.querySelector<HTMLInputElement>('[data-testid="access-search"]')!;
    expect(input.value).toBe('');
  });

  it('tells the parent, so its member count does not go stale', () => {
    const harness = setup();
    harness.type('ada');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();
    harness.click('[data-testid="access-results"] li:nth-child(2) button');
    harness.click('[data-testid="access-grant"]');

    expect(harness.host.changes()).toBe(1);
  });
});

describe('AccessPanel — a degraded directory', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('says the identity provider could not be searched', () => {
    // An empty list from a directory nobody could reach reads exactly like "no such group", and
    // those are different answers to act on.
    const harness = setup({ directory: of({ source: 'local', results: [] }) });
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('could not be searched');
    expect(harness.testid('access-no-match')?.textContent).toContain('the group may well exist');
  });

  it('still offers what the console already knows', () => {
    const local: DirectoryResults = {
      source: 'local',
      results: [{ kind: 'group', id: '/ai/kundenservice', label: 'kundenservice', detail: '/ai' }],
    };
    const harness = setup({ directory: of(local) });
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    harness.fixture.detectChanges();

    expect(harness.testid('access-results')?.textContent).toContain('kundenservice');
  });

  it('reports a failed search rather than showing nothing', () => {
    const harness = setup({ directory: throwError(() => ({ status: 502 })) });
    harness.type('kunden');
    vi.advanceTimersByTime(300);
    const feedback = harness.fixture.debugElement.injector.get(PageFeedback);

    expect(feedback.error()).toContain('Could not search the directory.');
  });
});

describe('AccessPanel — revoking', () => {
  it('asks first, and says what a revoked group takes away', () => {
    const declined = setup({ confirmAnswer: false });
    declined.click('[aria-label="Revoke /ai/kundenservice"]');
    expect(declined.calls).toEqual([]);

    const accepted = setup({ confirmAnswer: true });
    accepted.click('[aria-label="Revoke /ai/kundenservice"]');
    expect(accepted.calls).toContain('revokeGroup:uc-a:/ai/kundenservice');
  });

  it('removes a person through the member endpoint', () => {
    const harness = setup();
    harness.click('[aria-label="Remove ada"]');

    expect(harness.calls).toContain('removeMember:uc-a:ada');
    expect(harness.host.changes()).toBe(1);
  });
});

describe('AccessPanel — a reader', () => {
  it('is offered no way to grant or revoke, and told who is', () => {
    // `FRD-206`: an action nobody can carry out reads as a broken system rather than a boundary.
    const harness = setup({ canManage: false });

    expect(harness.testid('access-search')).toBeNull();
    expect(harness.testid('access-grant')).toBeNull();
    expect(harness.element.querySelector('[aria-label="Revoke /ai/kundenservice"]')).toBeNull();
    expect(harness.element.querySelector('[aria-label="Remove ada"]')).toBeNull();
    expect(harness.testid('access-readonly')?.textContent).toContain('administers this use case');
  });

  it('still sees who has access — that is not a secret from a member', () => {
    const harness = setup({ canManage: false });

    expect(harness.text()).toContain('/ai/kundenservice');
    expect(harness.text()).toContain('ada');
  });
});
