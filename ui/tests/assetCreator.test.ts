import assert from 'node:assert/strict';
import test from 'node:test';

import { assetCreatorDisplay } from '../src/lib/assetCreator';

test('creator display names accounts by email and keeps the account ID', () => {
  assert.deepEqual(
    assetCreatorDisplay({ created_by_user_id: 'account-1', created_by_kind: 'account', created_by_email: 'ana@example.com' }),
    { kind: 'account', accountId: 'account-1', label: 'ana@example.com' },
  );
  assert.deepEqual(
    assetCreatorDisplay({ created_by_user_id: 'account-1', created_by_kind: 'account', created_by_email: null }),
    { kind: 'account', accountId: 'account-1', label: null },
  );
});

test('creator display labels creates without a live account', () => {
  assert.deepEqual(assetCreatorDisplay({ created_by_kind: 'master_key' }), { kind: 'label', label: 'Master key' });
  assert.deepEqual(assetCreatorDisplay({ created_by_kind: 'system' }), { kind: 'label', label: 'System' });
  assert.deepEqual(assetCreatorDisplay({ created_by_kind: 'account', created_by_user_id: null }), { kind: 'label', label: 'Deleted account' });
  assert.deepEqual(assetCreatorDisplay({ created_by_kind: 'unknown' }), { kind: 'label', label: 'Not recorded' });
  assert.deepEqual(assetCreatorDisplay({}), { kind: 'label', label: 'Not recorded' });
});
