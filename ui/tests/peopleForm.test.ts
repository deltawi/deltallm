import assert from 'node:assert/strict';
import test from 'node:test';
import { accountPayload, changeProvisionMethod, changeProvisionRole, initialAccountForm, initialProvisionForm, provisionPayload, validateAccountForm, validateProvisionForm } from '../src/lib/peopleForm';
import type { RBACAccount } from '../src/lib/api';

const account: RBACAccount = { account_id: 'account-1', email: 'alex@example.com', role: 'org_user', is_active: true, mfa_enabled: true, force_password_change: true, last_login_at: null, created_at: '2026-10-01T00:00:00Z' };

test('creation keeps the current defaults and team prefill takes precedence over organization prefill', () => {
  assert.equal(initialProvisionForm({}).scope, 'none');
  const form = initialProvisionForm({ initialOrganizationId: 'org-2', initialTeamId: 'team-2' }, [{ organization_id: 'org-1' }], [{ team_id: 'team-1' }]);
  assert.equal(form.scope, 'team');
  assert.equal(form.teamId, 'team-2');
  assert.equal(form.organizationId, 'org-2');
  assert.equal(form.teamRole, 'team_viewer');
  assert.equal(form.organizationRole, 'org_member');
  assert.equal(form.mode, 'invite_email');
});

test('invitations omit password and active status and send only the chosen scope', () => {
  const base = { ...initialProvisionForm({}), email: ' alex@example.com ', password: 'sample-password-only', active: false, platformRole: 'platform_admin' };
  assert.deepEqual(provisionPayload({ ...base, scope: 'organization', organizationId: 'org-1', organizationRole: 'org_owner', teamId: 'unused' }), { email: 'alex@example.com', mode: 'invite_email', platform_role: 'org_user', organization_id: 'org-1', organization_role: 'org_owner' });
  assert.deepEqual(provisionPayload({ ...base, scope: 'team', teamId: 'team-1', teamRole: 'team_developer', organizationId: 'unused' }), { email: 'alex@example.com', mode: 'invite_email', platform_role: 'org_user', team_id: 'team-1', team_role: 'team_developer' });
});

test('manual creation preserves password and active status and supports optional membership', () => {
  const form = { ...initialProvisionForm({}), email: 'alex@example.com', mode: 'create_account' as const, password: ' sample-password-only ', active: false };
  assert.equal(validateProvisionForm(form), null);
  assert.deepEqual(provisionPayload(form), { email: 'alex@example.com', mode: 'create_account', platform_role: 'org_user', password: 'sample-password-only', is_active: false });
  assert.deepEqual(provisionPayload({ ...form, scope: 'team', teamId: 'team-1' }).team_role, 'team_viewer');
});

test('changing to Platform Admin removes scoped membership and returning to invitations clears the password', () => {
  const form = { ...initialProvisionForm({ initialTeamId: 'team-1' }), email: 'alex@example.com', mode: 'create_account' as const, password: 'sample-password-only' };
  const admin = changeProvisionRole(form, 'platform_admin');
  assert.equal(admin.scope, 'none');
  assert.equal(provisionPayload({ ...admin, scope: 'team' }).team_id, undefined);
  const invite = changeProvisionMethod(admin, 'invite_email', { initialTeamId: 'team-1' });
  assert.equal(invite.platformRole, 'org_user');
  assert.equal(invite.password, '');
  assert.equal(invite.scope, 'team');
});

test('validation points to the tab and field that needs correction', () => {
  const base = initialProvisionForm({});
  assert.equal(validateProvisionForm(base)?.field, 'email');
  assert.equal(validateProvisionForm({ ...base, email: 'invalid' })?.message, 'Enter a valid email address.');
  assert.deepEqual(validateProvisionForm({ ...base, email: 'alex@example.com' }), { tab: 'access', field: 'scope', message: 'Choose an organization or team for this invitation.' });
  assert.equal(validateProvisionForm({ ...base, email: 'alex@example.com', scope: 'team' })?.field, 'teamId');
  assert.equal(validateProvisionForm({ ...base, email: 'alex@example.com', scope: 'organization' })?.field, 'organizationId');
  assert.equal(validateProvisionForm({ ...base, email: 'alex@example.com', mode: 'create_account', password: 'short' })?.field, 'password');
});

test('account editing keeps immutable identity, omits an empty password, and does not write server-owned state', () => {
  const form = initialAccountForm(account);
  assert.equal(form.password, '');
  assert.deepEqual(accountPayload(account, { ...form, password: '  ', role: 'platform_admin', active: false }), { email: 'alex@example.com', role: 'platform_admin', is_active: false });
  assert.deepEqual(accountPayload(account, { ...form, password: ' sample-password-only ' }), { email: 'alex@example.com', role: 'org_user', is_active: true, password: 'sample-password-only' });
  assert.equal(validateAccountForm(form), null);
  assert.equal(validateAccountForm({ ...form, password: 'short' })?.field, 'password');
});
