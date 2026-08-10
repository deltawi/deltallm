import assert from 'node:assert/strict';
import test from 'node:test';
import { isPlatformAdminSession, resolveUiAccess } from '../src/lib/authorization';

test('organization administrators cannot access tier management actions', () => {
  const session = {
    authenticated: true,
    role: 'org_user',
    effective_permissions: ['org.read', 'org.update', 'team.update'],
    ui_access: { tiers: true, organization_create: true, team_create: true },
    organization_memberships: [{ role: 'org_admin' }],
  };

  const access = resolveUiAccess('session', session);

  assert.equal(isPlatformAdminSession('session', session), false);
  assert.equal(access.tiers, false);
  assert.equal(access.organization_create, false);
  assert.equal(access.team_create, true);
});

test('platform administrators and the master key can manage tiers', () => {
  const platformAdmin = { authenticated: true, role: 'platform_admin' };

  assert.equal(isPlatformAdminSession('session', platformAdmin), true);
  assert.equal(resolveUiAccess('session', platformAdmin).tiers, true);
  assert.equal(isPlatformAdminSession('master_key', null), true);
  assert.equal(resolveUiAccess('master_key', null).tiers, true);
});

test('legacy permission fallback does not expose gated self reporting', () => {
  const access = resolveUiAccess('session', {
    authenticated: true,
    role: 'org_user',
    effective_permissions: ['spend.read.self'],
  });

  assert.equal(access.usage, false);
  assert.equal(access.dashboard, false);
});

test('legacy permission fallback does not expose gated team reporting', () => {
  const access = resolveUiAccess('session', {
    authenticated: true,
    role: 'org_user',
    effective_permissions: ['spend.read.team'],
  });

  assert.equal(access.usage, false);
  assert.equal(access.dashboard, false);
});

test('server-authorized scoped reporting exposes Usage without the admin dashboard', () => {
  const access = resolveUiAccess('session', {
    authenticated: true,
    role: 'org_user',
    effective_permissions: ['spend.read.self'],
    ui_access: { usage: true },
  });

  assert.equal(access.usage, true);
  assert.equal(access.dashboard, false);
});
