import assert from 'node:assert/strict';
import test from 'node:test';
import { loginPathFor, returnToFromSearch, safeReturnTo } from '../src/lib/authRedirect';

test('preserves a local deep link including query and hash', () => {
  const returnTo = '/models/deployment-1?tab=usage#cost';
  const loginPath = loginPathFor(returnTo);

  assert.equal(returnToFromSearch(loginPath.slice('/login'.length)), returnTo);
});

test('rejects external and public authentication destinations', () => {
  assert.equal(safeReturnTo('https://evil.example/path'), '/');
  assert.equal(safeReturnTo('//evil.example/path'), '/');
  assert.equal(safeReturnTo('/\\evil.example/path'), '/');
  assert.equal(safeReturnTo('/login?returnTo=/models'), '/');
  assert.equal(safeReturnTo('/login/?returnTo=/models'), '/');
  assert.equal(safeReturnTo('/reset-password#token'), '/');
});

test('uses the caller fallback for a missing destination', () => {
  assert.equal(returnToFromSearch('', '/models'), '/models');
});
