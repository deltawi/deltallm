import assert from 'node:assert/strict';
import test from 'node:test';
import {
  normalizeTierPolicyMode,
  organizationTierIsRequired,
  organizationUsesTier,
} from '../src/lib/organizationPolicy';

test('normalizes unknown tier modes to disabled', () => {
  assert.equal(normalizeTierPolicyMode('enforce'), 'enforce');
  assert.equal(normalizeTierPolicyMode('shadow'), 'shadow');
  assert.equal(normalizeTierPolicyMode('disabled'), 'disabled');
  assert.equal(normalizeTierPolicyMode('unexpected'), 'disabled');
  assert.equal(normalizeTierPolicyMode(null), 'disabled');
});

test('enforce always uses and requires a tier', () => {
  assert.equal(organizationUsesTier('enforce', false), true);
  assert.equal(organizationUsesTier('enforce', true), true);
  assert.equal(organizationTierIsRequired('enforce'), true);
});

test('shadow defaults to a tier but allows an explicit migration exception', () => {
  assert.equal(organizationUsesTier('shadow', false), true);
  assert.equal(organizationUsesTier('shadow', true), false);
  assert.equal(organizationTierIsRequired('shadow'), false);
});

test('disabled preserves legacy creation', () => {
  assert.equal(organizationUsesTier('disabled', false), false);
  assert.equal(organizationUsesTier('disabled', true), false);
  assert.equal(organizationTierIsRequired('disabled'), false);
});
