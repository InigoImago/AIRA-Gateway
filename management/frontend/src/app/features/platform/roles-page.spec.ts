import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import {
  GroupCheck,
  Me,
  Page,
  PermissionInfo,
  Role,
  RoleChange,
  RoleDraft,
} from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { RolesPage } from './roles-page';

const CATALOGUE: PermissionInfo[] = [
  {
    name: 'usecase.read_all',
    area: 'Use cases',
    label: 'See every use case',
    sensitive: false,
    reserved: false,
  },
  {
    name: 'usecase.manage_all',
    area: 'Use cases',
    label: 'Administer every use case',
    sensitive: true,
    reserved: false,
  },
  {
    name: 'report.read_all',
    area: 'Reporting',
    label: 'Every figure',
    sensitive: false,
    reserved: false,
  },
  { name: 'role.read', area: 'Roles', label: 'See the roles', sensitive: false, reserved: false },
  {
    name: 'role.manage',
    area: 'Roles',
    label: 'Create, change and delete roles',
    sensitive: true,
    reserved: true,
  },
];

const GA: Role = {
  slug: 'global-admin',
  label: 'Global Administrator',
  group_paths: ['/aira/admins'],
  permissions: CATALOGUE.map((p) => p.name),
  builtin: true,
  fixed: true,
};
const SEC: Role = {
  slug: 'it-security',
  label: 'IT Security',
  group_paths: ['/aira/security'],
  permissions: ['usecase.read_all', 'usecase.manage_all', 'role.read'],
  builtin: true,
  fixed: true,
};
const STG: Role = {
  slug: 'it-steuerung',
  label: 'IT Steuerung',
  group_paths: ['/aira/steuerung'],
  permissions: ['usecase.read_all', 'report.read_all', 'role.read'],
  builtin: true,
  fixed: false,
};
const CUSTOM: Role = {
  slug: 'controlling',
  label: 'Controlling',
  group_paths: ['/finance/controlling'],
  permissions: ['report.read_all'],
  builtin: false,
  fixed: false,
};

const LABELS = {
  'global-admin': 'Global Administrator',
  'it-security': 'IT Security',
  'it-steuerung': 'IT Steuerung',
  controlling: 'Controlling',
};

const MANAGER = ['role.read', 'role.manage'];
const READER = ['role.read'];

function page(results: RoleChange[], number = 1, pages = 1): Page<RoleChange> {
  return { count: results.length * pages, page: number, page_size: 25, pages, results };
}

/** A refusal in the error envelope Management answers with. */
function refused(status: number, message: string, details: unknown = null) {
  return throwError(() => ({ status, error: { error: { code: 'x', message, details } } }));
}

interface Options {
  permissions?: string[];
  roles?: Role[];
  changes?: Observable<Page<RoleChange>>[];
  check?: (path: string) => Observable<GroupCheck>;
  create?: Observable<Role>;
  rolesLoad?: () => Observable<Role[]>;
  confirm?: boolean;
  meFails?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const state = { roles: options.roles ?? [GA, SEC, STG, CUSTOM] };
  const calls = {
    roles: 0,
    changes: [] as Record<string, unknown>[],
    checks: [] as string[],
    created: [] as RoleDraft[],
    updated: [] as [string, Partial<RoleDraft>][],
    deleted: [] as string[],
    asked: [] as string[],
  };
  const changes = [...(options.changes ?? [])];
  const service = {
    roles: () => {
      calls.roles++;
      return options.rolesLoad ? options.rolesLoad() : of(state.roles);
    },
    permissionCatalogue: () => of(CATALOGUE),
    roleChanges: (query: Record<string, unknown>) => {
      calls.changes.push(query);
      return changes.shift() ?? of(page([]));
    },
    checkGroup: (path: string) => {
      calls.checks.push(path);
      return options.check ? options.check(path) : of({ group_path: path, exists: true });
    },
    createRole: (draft: RoleDraft) => {
      calls.created.push(draft);
      return (
        options.create ??
        of({
          ...CUSTOM,
          slug: 'audit',
          label: draft.label,
          group_paths: [draft.group_path],
          permissions: draft.permissions,
        })
      );
    },
    updateRole: (slug: string, body: Partial<RoleDraft>) => {
      calls.updated.push([slug, body]);
      const role = state.roles.find((r) => r.slug === slug)!;
      return of({ ...role, ...body });
    },
    deleteRole: (slug: string) => {
      calls.deleted.push(slug);
      return of(undefined);
    },
  };
  const me: Me = {
    subject: 's',
    username: 'admin',
    email: '',
    roles: [],
    use_cases: [],
    permissions: options.permissions ?? MANAGER,
    role_labels: LABELS,
  };
  TestBed.configureTestingModule({
    imports: [RolesPage],
    providers: [
      { provide: UseCaseService, useValue: service },
      {
        provide: MeService,
        useValue: {
          currency: signal(''),
          get: () => (options.meFails ? refused(500, 'Account service down.') : of(me)),
        },
      },
      {
        provide: ConfirmService,
        useValue: {
          ask: (question: string) => {
            calls.asked.push(question);
            return options.confirm ?? true;
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(RolesPage);
  fixture.detectChanges();
  const html = () => fixture.nativeElement as HTMLElement;
  const q = <T extends HTMLElement = HTMLElement>(id: string) =>
    html().querySelector<T>(`[data-testid="${id}"]`);
  /**
   * Render, then let the form settle. A template-driven form registers its `ngModel` controls in a
   * microtask, so a field typed into before that reaches no control and the signal never changes.
   */
  const settle = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  };
  const click = async (id: string) => {
    const element = q(id);
    if (!element) throw new Error(`no [data-testid="${id}"] to click`);
    element.click();
    await settle();
  };
  const type = async (id: string, value: string) => {
    const input = q<HTMLInputElement>(id);
    if (!input) throw new Error(`no [data-testid="${id}"] to type into`);
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await settle();
  };
  const open = async (slug: string) => {
    html()
      .querySelector<HTMLButtonElement>(
        `[data-testid="role-row-${slug}"] [data-testid="role-open"]`,
      )!
      .click();
    await settle();
  };
  const saveEnabled = () => !q<HTMLButtonElement>('save-role')!.disabled;
  return { fixture, html, q, click, type, open, saveEnabled, calls, state };
}

describe('RolesPage (`FRD-614` FR-9)', () => {
  // ---- the list --------------------------------------------------------------------------------

  it('lists every role with its group, how much it may do, and which ones are fixed or built in', async () => {
    const { q } = setup();
    const row = (slug: string) => q(`role-row-${slug}`)!;

    expect(q('roles-table')).not.toBeNull();
    expect(row('global-admin').textContent).toContain('Global Administrator');
    expect(row('global-admin').querySelector('[data-testid="role-count"]')?.textContent).toBe('5');
    expect(row('controlling').querySelector('[data-testid="role-groups"]')?.textContent).toContain(
      '/finance/controlling',
    );
    expect(row('it-steuerung').querySelector('[data-testid="role-count"]')?.textContent).toBe('3');

    // Fixed: the two roles no change may touch. Built in: IT Steuerung. Neither: a custom role.
    const badges = (slug: string) =>
      ['badge-fixed', 'badge-builtin'].filter(
        (id) => row(slug).querySelector(`[data-testid="${id}"]`) !== null,
      );
    expect(badges('global-admin')).toEqual(['badge-fixed']);
    expect(badges('it-security')).toEqual(['badge-fixed']);
    expect(badges('it-steuerung')).toEqual(['badge-builtin']);
    expect(badges('controlling')).toEqual([]);
    expect(row('global-admin').textContent).toContain('🔒');
  });

  it('says so when a role has no configured group', async () => {
    const { q } = setup({ roles: [{ ...STG, group_paths: [] }] });

    expect(q('role-row-it-steuerung')?.textContent).toContain('No group configured');
  });

  it('says there is nothing to show when the list is empty', async () => {
    const { q } = setup({ roles: [] });

    expect(q('roles-table')).toBeNull();
    expect(q('no-roles')).not.toBeNull();
  });

  it('reports a refused list instead of an empty page', async () => {
    const { html, q } = setup({
      rolesLoad: () => refused(403, 'You may not read the roles.'),
    });

    expect(html().querySelector('[role="alert"]')?.textContent).toContain(
      'You may not read the roles.',
    );
    expect(q('no-roles')).not.toBeNull();
  });

  it('says it is loading until the roles arrive', async () => {
    const pending = new Subject<Role[]>();
    const { q, fixture } = setup({ rolesLoad: () => pending });

    expect(q('roles-loading')).not.toBeNull();
    pending.next([GA]);
    fixture.detectChanges();
    expect(q('roles-loading')).toBeNull();
    expect(q('role-row-global-admin')).not.toBeNull();
  });

  // ---- fixed roles -----------------------------------------------------------------------------

  it('shows a fixed role read-only, even to a Global Administrator', async () => {
    const { q, open, html } = setup();
    await open('global-admin');

    expect(q('role-details')).not.toBeNull();
    expect(q('role-fixed-notice')?.textContent).toContain('cannot be changed');
    expect(q('role-fixed-notice')?.textContent).toContain('holds every permission');
    expect(q('edit-role')).toBeNull();
    expect(q('role-editor')).toBeNull();
    expect(q('delete-role')).toBeNull();
    expect(html().querySelector('input[type="checkbox"]')).toBeNull();
    // What it holds, grouped by area — the reserved permission included, because it holds it.
    expect(q('held-role.manage')).not.toBeNull();
    expect(q('held-usecase.manage_all')?.textContent).toContain('⚠ Sensitive');
    expect(q('held-usecase.read_all')?.textContent).not.toContain('⚠');
    expect(q('role-permissions')?.textContent).toContain('Reporting');
  });

  it('closes a role when it is opened a second time', async () => {
    const { q, open } = setup();
    await open('it-security');
    expect(q('role-details')).not.toBeNull();
    expect(q('role-row-it-security')?.classList.contains('is-open')).toBe(true);

    await open('it-security');
    expect(q('role-details')).toBeNull();
  });

  // ---- IT Steuerung ----------------------------------------------------------------------------

  it('offers only the permissions of IT Steuerung, and saves only them', async () => {
    const { q, open, click, saveEnabled, calls } = setup();
    await open('it-steuerung');
    await click('edit-role');

    expect(q('role-window')?.getAttribute('role')).toBe('dialog');
    expect(q('role-window')?.textContent).toContain('Change IT Steuerung');
    expect(q('role-editor')).not.toBeNull();
    expect(q('builtin-notice')?.textContent).toContain('AIRA_ROLE_GROUPS');
    expect(q('builtin-group')?.textContent).toContain('/aira/steuerung');
    expect(q('role-group')).toBeNull();
    expect(q('role-label')).toBeNull();
    expect(q('check-group')).toBeNull();
    expect(q('delete-role')).toBeNull();
    expect(q<HTMLInputElement>('permission-report.read_all')?.checked).toBe(true);
    expect(q<HTMLInputElement>('permission-usecase.manage_all')?.checked).toBe(false);
    // Nothing changed yet, so nothing to save.
    expect(saveEnabled()).toBe(false);

    await click('permission-usecase.manage_all');
    await click('permission-report.read_all');
    expect(saveEnabled()).toBe(true);
    await click('save-role');

    // In the catalogue's order, and without a name or a group, which the server refuses for it.
    expect(calls.updated).toEqual([
      ['it-steuerung', { permissions: ['usecase.read_all', 'usecase.manage_all', 'role.read'] }],
    ]);
  });

  it('names a built-in role with no configured group', async () => {
    const { q, open, click } = setup({ roles: [{ ...STG, group_paths: [] }] });
    await open('it-steuerung');
    await click('edit-role');

    expect(q('builtin-group')?.textContent).toContain('no configured group');
  });

  // ---- creating a role -------------------------------------------------------------------------

  it('creates a role once its group is confirmed, and shows it in the list and the log', async () => {
    const created: RoleChange = {
      id: 7,
      role: 'audit',
      action: 'created',
      actor: 'admin',
      at: '2026-09-13T10:15:00+00:00',
      before: null,
      after: { label: 'Audit', group_path: '/finance/audit', permissions: ['report.read_all'] },
    };
    const { q, click, type, saveEnabled, calls, state, html } = setup({
      changes: [of(page([])), of(page([created]))],
    });
    await click('new-role');
    // A creator opens a window, not a form unfolding under the list.
    expect(q('role-window')?.getAttribute('role')).toBe('dialog');
    expect(q('role-window')?.textContent).toContain('New role');

    await type('role-group', ' /finance/audit ');
    await type('role-label', 'Audit');
    await click('permission-report.read_all');
    // Not yet: the group has not been checked.
    expect(saveEnabled()).toBe(false);
    expect(q('save-blocked')?.textContent).toContain('Check the group');

    await click('check-group');
    expect(calls.checks).toEqual(['/finance/audit']);
    const result = q('group-check-result')!;
    expect(result.getAttribute('data-verdict')).toBe('confirmed');
    expect(result.textContent).toContain('/finance/audit');
    expect(q('save-blocked')).toBeNull();
    expect(saveEnabled()).toBe(true);

    state.roles = [GA, SEC, STG, CUSTOM, { ...CUSTOM, slug: 'audit', label: 'Audit' }];
    await click('save-role');

    expect(calls.created).toEqual([
      { label: 'Audit', group_path: '/finance/audit', permissions: ['report.read_all'] },
    ]);
    expect(calls.roles).toBe(2);
    expect(q('role-window')).toBeNull();
    expect(q('role-row-audit')?.classList.contains('is-open')).toBe(true);
    expect(q('role-details')?.textContent).toContain('Audit');
    expect(html().querySelector('[role="status"].callout')?.textContent).toContain(
      'Audit is created',
    );
    // The log was asked again after the write, and shows the entry just made.
    expect(calls.changes.length).toBe(2);
    expect(q('role-change-7')?.textContent).toContain('Audit');
  });

  it('shows the server’s reason when Keycloak refuses the group, and does not save', async () => {
    const { q, click, type, saveEnabled, calls } = setup({
      check: () =>
        refused(400, 'Request failed.', { group_path: ["Keycloak has no group '/nope'."] }),
    });
    await click('new-role');
    await type('role-group', '/nope');
    await type('role-label', 'Nope');
    await click('check-group');

    const result = q('group-check-result')!;
    expect(result.getAttribute('data-verdict')).toBe('refused');
    expect(result.textContent).toContain("Keycloak has no group '/nope'.");
    expect(result.textContent).not.toContain('Request failed.');
    expect(result.classList.contains('field__hint--error')).toBe(true);
    expect(saveEnabled()).toBe(false);
    await click('save-role');
    expect(calls.created).toEqual([]);
  });

  it('shows the server’s message for a refusal that names no field', async () => {
    const { q, click, type } = setup({ check: () => refused(400, 'A JSON object.') });
    await click('new-role');
    await type('role-group', '/x');
    await click('check-group');

    expect(q('group-check-result')?.textContent).toContain('A JSON object.');
  });

  it('says plainly that a group that could not be verified is not bound', async () => {
    const { q, click, type, saveEnabled, calls } = setup({
      check: () => refused(503, 'The directory could not be asked whether this group exists.'),
    });
    await click('new-role');
    await type('role-group', '/finance/audit');
    await type('role-label', 'Audit');
    await click('check-group');

    const result = q('group-check-result')!;
    expect(result.getAttribute('data-verdict')).toBe('unverified');
    expect(result.textContent).toContain('could not be verified, so it is not bound');
    expect(saveEnabled()).toBe(false);
    await click('save-role');
    expect(calls.created).toEqual([]);
  });

  it('counts an unreachable server as a group nobody verified', async () => {
    const { q, click, type } = setup({ check: () => throwError(() => ({ status: 0 })) });
    await click('new-role');
    await type('role-group', '/finance/audit');
    await click('check-group');

    expect(q('group-check-result')?.getAttribute('data-verdict')).toBe('unverified');
    expect(q('group-check-result')?.textContent).toContain('server could not be reached');
  });

  it('asks for a new check once the checked path is edited', async () => {
    const { q, click, type, saveEnabled, calls } = setup();
    await click('new-role');
    await type('role-label', 'Audit');
    await type('role-group', '/finance/audit');
    await click('check-group');
    expect(saveEnabled()).toBe(true);

    await type('role-group', '/finance/audit2');
    // The confirmation was about another path: it is not shown and does not count.
    expect(q('group-check-result')).toBeNull();
    expect(saveEnabled()).toBe(false);

    await click('check-group');
    expect(calls.checks).toEqual(['/finance/audit', '/finance/audit2']);
    expect(saveEnabled()).toBe(true);
  });

  it('does not ask twice while a check is running, nor for an empty path', async () => {
    const pending = new Subject<GroupCheck>();
    const { q, click, type, calls, fixture } = setup({ check: () => pending });
    await click('new-role');
    expect(q<HTMLButtonElement>('check-group')?.disabled).toBe(true);

    await type('role-group', '/finance/audit');
    await click('check-group');
    expect(q('check-group')?.textContent).toContain('Checking…');
    expect(q<HTMLButtonElement>('check-group')?.disabled).toBe(true);

    pending.next({ group_path: '/finance/audit', exists: true });
    fixture.detectChanges();
    expect(calls.checks).toEqual(['/finance/audit']);
    expect(q('check-group')?.textContent).toContain('Check group');
  });

  it('needs a name before a new role can be saved', async () => {
    const { q, click, type, saveEnabled } = setup();
    await click('new-role');
    await type('role-group', '/finance/audit');
    await click('check-group');

    expect(saveEnabled()).toBe(false);
    expect(q('save-blocked')?.textContent).toContain('Give the role a name.');
  });

  it('keeps the form and reports the server’s reason when a save is refused', async () => {
    const { q, click, type, html } = setup({
      create: refused(400, 'Request failed.', { label: ["A role called 'Audit' exists already."] }),
    });
    await click('new-role');
    await type('role-group', '/finance/audit');
    await type('role-label', 'Audit');
    await click('check-group');
    await click('save-role');

    expect(html().querySelector('[role="alert"]')?.textContent).toContain(
      "A role called 'Audit' exists already.",
    );
    expect(q('role-editor')).not.toBeNull();
  });

  it('marks sensitive permissions and never offers a reserved one', async () => {
    const { q, click } = setup();
    await click('new-role');

    expect(q('sensitive-usecase.manage_all')?.textContent).toContain('⚠');
    expect(q('sensitive-usecase.read_all')).toBeNull();
    expect(q('role-permission-boxes')?.textContent).toContain(
      'Sensitive: it reaches stored content',
    );
    // `role.manage` is held by the Global Administrator alone: no box, not even an unticked one.
    expect(q('permission-role.manage')).toBeNull();
    expect(q('permission-role.read')).not.toBeNull();
    expect(q('area-Roles')?.querySelectorAll('input[type="checkbox"]').length).toBe(1);
  });

  it('closes the form on Cancel', async () => {
    const { q, click } = setup();
    await click('new-role');
    await click('cancel-role');

    expect(q('role-editor')).toBeNull();
  });

  it('closes the window with its ✕ and keeps the open role open', async () => {
    const { q, click, open } = setup();
    await open('controlling');
    await click('edit-role');
    await click('role-window-close');

    expect(q('role-window')).toBeNull();
    expect(q('role-details')?.textContent).toContain('Controlling');
  });

  // ---- changing and deleting a custom role -----------------------------------------------------

  it('saves only what changed on a custom role, and a kept group needs no check', async () => {
    const { click, type, open, saveEnabled, calls } = setup();
    await open('controlling');
    await click('edit-role');
    expect(saveEnabled()).toBe(false);

    await type('role-label', 'Controlling & Finance');
    expect(saveEnabled()).toBe(true);
    await click('save-role');

    expect(calls.checks).toEqual([]);
    expect(calls.updated).toEqual([['controlling', { label: 'Controlling & Finance' }]]);
  });

  it('rebinds a custom role only to a group that was checked', async () => {
    const { q, click, type, open, saveEnabled, calls } = setup();
    await open('controlling');
    await click('edit-role');
    await type('role-group', '/finance/controlling-new');

    expect(saveEnabled()).toBe(false);
    expect(q('save-blocked')?.textContent).toContain('check it before saving');
    await click('check-group');
    await click('save-role');

    expect(calls.updated).toEqual([['controlling', { group_path: '/finance/controlling-new' }]]);
  });

  it('deletes a custom role after asking, and refreshes the list and the log', async () => {
    const { q, click, open, calls, state, html } = setup();
    await open('controlling');
    await click('edit-role');
    state.roles = [GA, SEC, STG];
    await click('delete-role');

    expect(calls.asked[0]).toContain('Controlling');
    expect(calls.asked[0]).toContain('/finance/controlling');
    expect(calls.deleted).toEqual(['controlling']);
    expect(q('role-row-controlling')).toBeNull();
    expect(q('role-editor')).toBeNull();
    expect(q('role-details')).toBeNull();
    expect(calls.changes.length).toBe(2);
    expect(html().querySelector('[role="status"].callout')?.textContent).toContain(
      'Controlling is deleted.',
    );
  });

  it('deletes nothing when the question is declined', async () => {
    const { click, open, calls } = setup({ confirm: false });
    await open('controlling');
    await click('edit-role');
    await click('delete-role');

    expect(calls.asked.length).toBe(1);
    expect(calls.deleted).toEqual([]);
  });

  // ---- without role.manage ---------------------------------------------------------------------

  it('shows the same roles read-only to a reader who may not manage them', async () => {
    const { q, open, html } = setup({ permissions: READER });

    expect(q('roles-readonly')?.textContent).toContain('Only a Global Administrator');
    expect(q('new-role')).toBeNull();
    expect(q('roles-table')).not.toBeNull();

    for (const slug of ['controlling', 'it-steuerung']) {
      await open(slug);
      expect(q('role-details'), slug).not.toBeNull();
      expect(q('edit-role'), slug).toBeNull();
      expect(q('role-editor'), slug).toBeNull();
      expect(q('delete-role'), slug).toBeNull();
      expect(q('save-role'), slug).toBeNull();
      expect(html().querySelector('input'), slug).toBeNull();
    }
    expect(q('held-report.read_all')).not.toBeNull();
    expect(q('role-fixed-notice')).toBeNull();
  });

  it('says a role that may do nothing yet may do nothing', async () => {
    const { q, open } = setup({
      permissions: READER,
      roles: [{ ...CUSTOM, permissions: [] }],
    });
    await open('controlling');

    expect(q('role-details')?.textContent).toContain('This role may do nothing yet.');
  });

  it('offers no control and says why when the account cannot be loaded', async () => {
    const { q, html } = setup({ meFails: true });

    expect(html().querySelector('[role="alert"]')?.textContent).toContain('Account service down.');
    expect(q('new-role')).toBeNull();
  });

  it('offers the manager controls a reader does not get', async () => {
    // The paired case of the read-only test: the same page with `role.manage`.
    const { q, open, click } = setup({ permissions: MANAGER });

    expect(q('roles-readonly')).toBeNull();
    expect(q('new-role')).not.toBeNull();
    await open('controlling');
    expect(q('edit-role')).not.toBeNull();
    await click('edit-role');
    expect(q('role-editor')).not.toBeNull();
    expect(q('delete-role')).not.toBeNull();
  });

  // ---- the change log --------------------------------------------------------------------------

  it('shows what each change did: who, when, which role, and the difference', async () => {
    const rows: RoleChange[] = [
      {
        id: 3,
        role: 'controlling',
        action: 'updated',
        actor: 'admin',
        at: '2026-09-13T10:15:00+00:00',
        before: {
          label: 'Controlling',
          group_path: '/finance/controlling',
          permissions: ['report.read_all', 'usecase.read_all'],
        },
        after: {
          label: 'Controlling & Finance',
          group_path: '/finance/cf',
          permissions: ['report.read_all', 'usecase.manage_all'],
        },
      },
      {
        id: 2,
        role: 'old-role',
        action: 'deleted',
        actor: 'root',
        at: '2026-09-12T08:00:00+00:00',
        before: { label: 'Old role', group_path: '/old', permissions: ['role.read'] },
        after: null,
      },
      {
        id: 1,
        role: 'it-steuerung',
        action: 'updated',
        actor: 'admin',
        at: '2026-09-11T08:00:00+00:00',
        before: { label: 'IT Steuerung', group_path: '', permissions: ['role.read'] },
        after: { label: 'IT Steuerung', group_path: '', permissions: ['role.read'] },
      },
      {
        id: 0,
        role: 'gone',
        action: 'created',
        actor: 'admin',
        at: '2026-09-10T08:00:00+00:00',
        before: null,
        after: null,
      },
    ];
    const { q } = setup({ changes: [of(page(rows))] });
    const row = (id: number) => q(`role-change-${id}`)!;
    const cell = (id: number, part: string) =>
      row(id).querySelector(`[data-testid="${part}"]`)?.textContent?.trim();

    expect(q('role-changes')).not.toBeNull();
    expect(row(3).textContent).toContain('13.09.2026');
    expect(cell(3, 'change-actor')).toBe('admin');
    // The role's name today, from `/me`.
    expect(cell(3, 'change-role')).toBe('Controlling');
    expect(cell(3, 'change-action')).toBe('Changed');
    expect(cell(3, 'change-label')).toContain('Controlling → Controlling & Finance');
    expect(cell(3, 'change-group')).toContain('/finance/controlling → /finance/cf');
    expect(cell(3, 'change-added')).toContain('usecase.manage_all');
    expect(cell(3, 'change-added')).not.toContain('report.read_all');
    expect(cell(3, 'change-removed')).toContain('usecase.read_all');
    expect(row(3).querySelector('[data-testid="change-added"] code')?.getAttribute('title')).toBe(
      'Administer every use case',
    );

    // A deleted role is no longer in `/me`: the name it had is what the log recorded.
    expect(cell(2, 'change-role')).toBe('Old role');
    expect(cell(2, 'change-action')).toBe('Deleted');
    expect(cell(2, 'change-label')).toContain('Old role');
    expect(cell(2, 'change-removed')).toContain('role.read');
    expect(row(2).querySelector('[data-testid="change-added"]')).toBeNull();

    expect(cell(1, 'change-role')).toBe('IT Steuerung');
    expect(row(1).textContent).toContain('Saved without a difference');
    // Neither a name today nor a recorded one: the slug.
    expect(cell(0, 'change-role')).toBe('gone');
    expect(cell(0, 'change-action')).toBe('Created');
  });

  it('says nothing has changed yet, rather than showing an empty table', async () => {
    const { q } = setup({ changes: [of(page([]))] });

    expect(q('no-role-changes')).not.toBeNull();
  });

  it('pages the log at the server, one page at a time', async () => {
    const entry = (id: number): RoleChange => ({
      id,
      role: 'controlling',
      action: 'created',
      actor: 'admin',
      at: '2026-09-13T10:15:00+00:00',
      before: null,
      after: { label: 'Controlling', group_path: '/finance/controlling', permissions: [] },
    });
    const { q, click, calls } = setup({
      changes: [
        of({ count: 2, page: 1, page_size: 1, pages: 2, results: [entry(2)] }),
        of({ count: 2, page: 2, page_size: 1, pages: 2, results: [entry(1)] }),
      ],
    });
    expect(q('role-changes-pager')?.textContent).toContain('1–1 of 2 changes');

    await click('pager-next');

    expect(calls.changes[1]).toMatchObject({ page: 2 });
    // The next page replaces the first: the browser holds one page, never the log.
    expect(q('role-change-1')).not.toBeNull();
    expect(q('role-change-2')).toBeNull();
    expect(q('role-changes-pager')?.textContent).toContain('2–2 of 2 changes');
  });

  it('reports a log that cannot be loaded, and a page that cannot be loaded', async () => {
    const entry: RoleChange = {
      id: 1,
      role: 'controlling',
      action: 'created',
      actor: 'admin',
      at: '2026-09-13T10:15:00+00:00',
      before: null,
      after: { label: 'Controlling', group_path: '/g', permissions: [] },
    };
    const failing = setup({ changes: [refused(500, 'Log unavailable.')] });
    expect(failing.html().querySelector('[role="alert"]')?.textContent).toContain(
      'Log unavailable.',
    );

    const paging = setup({
      changes: [
        of({ count: 2, page: 1, page_size: 1, pages: 2, results: [entry] }),
        refused(500, 'Page two unavailable.'),
      ],
    });
    await paging.click('pager-next');
    expect(paging.html().querySelector('[role="alert"]')?.textContent).toContain(
      'Page two unavailable.',
    );
    // The page on screen stays, rather than an empty table claiming the log is empty.
    expect(paging.q('role-change-1')).not.toBeNull();
  });
});
