import { PERMISSIONS, can } from './roles';

describe('can', () => {
  it('answers yes for a permission the server listed', () => {
    expect(can({ permissions: ['incident.suspend', 'catalog.write'] }, 'catalog.write')).toBe(true);
  });

  it('answers no for a permission the server did not list', () => {
    expect(can({ permissions: ['incident.investigate'] }, 'incident.suspend')).toBe(false);
    expect(can({ permissions: [] }, 'usecase.create')).toBe(false);
  });

  it('answers no when there is no answer from the server', () => {
    // A failed or pending `/me`, or an older backend that sends no list: offer nothing rather than
    // an action that 403s.
    expect(can(null, 'usecase.create')).toBe(false);
    expect(can(undefined, 'usecase.create')).toBe(false);
    expect(can({}, 'usecase.create')).toBe(false);
  });

  it('never reads a role, however senior', () => {
    // The server decides what a role holds; the console restating it is a second definition.
    const me = { roles: ['global-admin'], permissions: [] as string[] };

    expect(PERMISSIONS.some((permission) => can(me, permission))).toBe(false);
  });
});

describe('PERMISSIONS', () => {
  it('names each permission once', () => {
    expect(new Set(PERMISSIONS).size).toBe(PERMISSIONS.length);
  });
});
