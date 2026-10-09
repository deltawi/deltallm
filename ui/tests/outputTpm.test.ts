import assert from 'node:assert/strict';
import test from 'node:test';
import { parseOutputTpm, reconcileRuntimeOutputTpm } from '../src/lib/outputTpm';
import type { Principal } from '../src/lib/api';

test('blank clears the policy and positive bounded values are accepted', () => {
  assert.equal(parseOutputTpm(''), null);
  assert.equal(parseOutputTpm('1'), 1);
  assert.equal(parseOutputTpm('2147483647'), 2147483647);
});

test('output completion cannot reopen or replace a different selected account', () => {
  const account = { account_id: 'account-a', runtime_user: { user_id: 'runtime-a', output_tpm_limit: 100 }, email: 'unchanged' } as Principal;
  assert.equal(reconcileRuntimeOutputTpm(null, 'account-a', 'runtime-a', 200), null);
  assert.equal(reconcileRuntimeOutputTpm(account, 'account-b', 'runtime-a', 200), account);
  assert.equal(reconcileRuntimeOutputTpm(account, 'account-a', 'runtime-b', 200), account);
  const updated = reconcileRuntimeOutputTpm(account, 'account-a', 'runtime-a', 200);
  assert.equal(updated?.runtime_user?.output_tpm_limit, 200);
  assert.equal(updated?.email, 'unchanged');
  assert.equal(account.runtime_user?.output_tpm_limit, 100);
});
test('invalid and overflowing values cannot be submitted', () => {
  for (const value of ['0', '-1', '1.5', '1e3', '2147483648', 'NaN']) {
    assert.throws(() => parseOutputTpm(value));
  }
});
