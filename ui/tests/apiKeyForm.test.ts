import assert from 'node:assert/strict';
import test from 'node:test';
import { emptyKeyForm, keyMutationPayload, validateKeyForm } from '../src/lib/apiKeyForm';
import type { SelfServicePolicy } from '../src/lib/api';

const policy: SelfServicePolicy = { self_service_keys_enabled: true, self_service_max_keys_per_user: 3, self_service_budget_ceiling: 50, self_service_require_expiry: true, self_service_max_expiry_days: 7 };

test('key conversion keeps every supported limit, owner, expiry, and blank-field behavior', () => {
  const form = { ...emptyKeyForm(), key_name: '  Support key  ', team_id: 'team-1', owner_mode: 'service_account' as const, owner_service_account_id: 'service-1', max_budget: '25.5', rpm_limit: '0', tpm_limit: '1000', output_tpm_limit: '2000', model_output_tpm_limit: [{ model: 'support/chat', limit: '100' }], rph_limit: '300', rpd_limit: '500', tpd_limit: '10000', expires: '2026-10-09T12:30' };
  const payload = keyMutationPayload(form, { selfService: false, editing: false, accountId: 'account-1' });
  assert.deepEqual(payload, { key_name: 'Support key', team_id: 'team-1', max_budget: 25.5, rpm_limit: 0, tpm_limit: 1000, output_tpm_limit: 2000, model_output_tpm_limit: { 'support/chat': 100 }, rph_limit: 300, rpd_limit: 500, tpd_limit: 10000, owner_account_id: undefined, owner_service_account_id: 'service-1', expires: new Date(form.expires).toISOString() });
  const update = keyMutationPayload(form, { selfService: false, editing: true, accountId: 'account-1' });
  assert.equal('expires' in update, false, 'admin edits keep the existing expiry');
  assert.equal('owner_account_id' in update, false, 'admin edits keep the existing owner');
  assert.equal('owner_service_account_id' in update, false);
  assert.equal(keyMutationPayload({ ...form, rpm_limit: '' }, { selfService: false, editing: true, accountId: 'account-1' }).rpm_limit, null, 'clearing a limit uses the API deletion value');
  const personal = keyMutationPayload(form, { selfService: true, editing: false, accountId: 'account-1' });
  assert.equal('owner_account_id' in personal, false);
  assert.equal('owner_service_account_id' in personal, false);
  const blank = keyMutationPayload(emptyKeyForm(), { selfService: true, editing: false, accountId: '' });
  assert.equal(blank.max_budget, undefined);
  assert.equal(blank.rpm_limit, undefined);
  assert.equal(blank.output_tpm_limit, null);
  assert.equal(blank.model_output_tpm_limit, null);
  assert.equal('expires' in blank, false);
});

test('key output limits select the Limits tab for errors and use explicit clears', () => {
  const form = { ...emptyKeyForm(), key_name: 'Support', team_id: 'team-1' };
  for (const invalid of [
    { ...form, output_tpm_limit: '0' },
    { ...form, output_tpm_limit: '2147483648' },
    { ...form, model_output_tpm_limit: [{ model: 'support', limit: '' }] },
    { ...form, model_output_tpm_limit: [{ model: '*', limit: '100' }] },
  ]) {
    assert.equal(validateKeyForm(invalid, false, null)?.tab, 'limits');
    assert.throws(() => keyMutationPayload(invalid, { selfService: false, editing: true, accountId: 'user-1' }));
  }
  const cleared = keyMutationPayload(form, { selfService: false, editing: true, accountId: 'user-1' });
  assert.equal(cleared.output_tpm_limit, null);
  assert.equal(cleared.model_output_tpm_limit, null);
});

test('key validation selects the tab for required owner and self-service policy fields', () => {
  const now = new Date('2026-10-08T00:00:00Z');
  let form = emptyKeyForm();
  assert.equal(validateKeyForm(form, false, null)?.tab, 'details');
  form = { ...form, key_name: 'key', team_id: 'team-1', owner_mode: 'service_account' };
  assert.match(validateKeyForm(form, false, null)?.message || '', /service account/);
  assert.equal(validateKeyForm(form, true, policy, now)?.tab, 'limits');
  form = { ...form, expires: '2026-10-09T12:00:00Z', max_budget: '51' };
  assert.match(validateKeyForm(form, true, policy, now)?.message || '', /ceiling/);
  form.max_budget = '50';
  assert.equal(validateKeyForm(form, true, policy, now), null);
  form.expires = '2026-10-16T00:00:00Z';
  assert.match(validateKeyForm(form, true, policy, now)?.message || '', /within 7 days/);
  form.expires = 'invalid';
  assert.match(validateKeyForm(form, true, policy, now)?.message || '', /valid expiry/);
});
