import { bindsEachMember, scopeLabel } from './limit-scope';

describe('limit scopes', () => {
  it('names a per-person row as binding each member, not as one shared pot', () => {
    expect(scopeLabel('each_member')).toBe('Each member, individually');
    expect(scopeLabel('use_case')).toBe('Whole use case');
  });

  it('says whether any row binds each member separately', () => {
    expect(bindsEachMember([{ scope: 'use_case' }, { scope: 'each_member' }])).toBe(true);
    expect(bindsEachMember([{ scope: 'use_case' }])).toBe(false);
    expect(bindsEachMember([])).toBe(false);
  });
});
