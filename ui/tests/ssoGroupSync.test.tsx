import assert from 'node:assert/strict';
import test from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import SsoMappingTable from '../src/components/settings/sso/SsoMappingTable';
import type { SsoGroupSyncSettings } from '../src/lib/api/ssoGroupSync';
import {
  addUniqueValues,
  buildSsoGroupSyncPayload,
  DEFAULT_SSO_GROUP_SYNC,
  describeChange,
  parsePreviewInput,
  sameSsoGroupSync,
  targetIndex,
  validateSsoGroupSync,
} from '../src/lib/ssoGroupSync';

const settings: SsoGroupSyncSettings = {
  ...DEFAULT_SSO_GROUP_SYNC,
  enabled: true,
  claim: 'deltallm_role',
  admin_values: ['admin'],
  mappings: [
    { value: 'team-ml', team_id: 'team_ml', team_role: 'team_developer' },
    { value: 'finance', organization_id: 'org_acme', org_role: 'org_billing' },
  ],
};

test('validation matches the server rules', () => {
  assert.equal(validateSsoGroupSync(settings), null);
  assert.match(validateSsoGroupSync({ ...settings, claim: 'bad claim' }) ?? '', /claim name/);
  assert.equal(validateSsoGroupSync({ ...settings, claim: 'realm_access.roles' }), null);
  assert.match(
    validateSsoGroupSync({ ...settings, mappings: [{ value: 'admin', team_id: 't', team_role: 'team_viewer' }] }) ?? '',
    /already an admin value/,
  );
  assert.match(
    validateSsoGroupSync({ ...settings, mappings: [settings.mappings[0], { ...settings.mappings[0], team_role: 'team_admin' }] }) ?? '',
    /twice/,
  );
  assert.match(validateSsoGroupSync({ ...settings, mappings: [{ value: 'x' }] }) ?? '', /Pick a team/);
});

test('payload trims values and keeps only the role for the target type', () => {
  const payload = buildSsoGroupSyncPayload({
    ...settings,
    claim: ' deltallm_role ',
    admin_values: [' admin ', ''],
    mappings: [{ value: ' team-ml ', team_id: 'team_ml', team_role: 'team_admin', org_role: 'org_owner' }],
  });
  assert.equal(payload.claim, 'deltallm_role');
  assert.deepEqual(payload.admin_values, ['admin']);
  assert.deepEqual(payload.mappings, [{ value: 'team-ml', team_id: 'team_ml', team_role: 'team_admin' }]);
  assert.equal(sameSsoGroupSync(settings, { ...settings, claim: ' deltallm_role ' }), true);
  assert.equal(sameSsoGroupSync(settings, { ...settings, remove_unlisted: true }), false);
});

test('chip input and preview input parsing', () => {
  assert.deepEqual(addUniqueValues(['admin'], 'ops, admin,DeltaLLM Admins'), ['admin', 'ops', 'DeltaLLM Admins']);
  assert.deepEqual(parsePreviewInput('team-ml, admin\nml-leads'), { values: ['team-ml', 'admin', 'ml-leads'] });
  assert.deepEqual(parsePreviewInput('{"deltallm_role": ["a"]}'), { claims: { deltallm_role: ['a'] } });
  assert.throws(() => parsePreviewInput('{not json'), SyntaxError);
});

test('change descriptions read as plain sentences', () => {
  const base = { target_id: 'team_ml', target_name: 'ML Platform', reason: null, revokes_keys: false };
  assert.equal(
    describeChange({ ...base, kind: 'team', action: 'update', role_before: 'team_viewer', role_after: 'team_admin' }),
    'ML Platform: Viewer to Admin',
  );
  assert.equal(
    describeChange({ ...base, kind: 'platform_role', action: 'kept', target_id: null, target_name: null, role_before: 'platform_admin', role_after: 'platform_admin' }),
    'Platform role kept: last platform admin',
  );
});

test('mapping table shows target names, roles and skipped targets', () => {
  const html = renderToStaticMarkup(
    <SsoMappingTable
      mappings={[...settings.mappings, { value: 'research', team_id: 'team_gone', team_role: 'team_viewer' }]}
      targets={targetIndex([
        { id: 'team_ml', kind: 'team', name: 'ML Platform', organization_name: 'Acme', status: 'ok' },
        { id: 'org_acme', kind: 'organization', name: 'Acme', status: 'ok' },
        { id: 'team_gone', kind: 'team', name: null, status: 'not_found' },
      ])}
      onChange={() => undefined}
      onRemove={() => undefined}
    />,
  );
  assert.match(html, /ML Platform/);
  assert.match(html, /Team · Acme/);
  assert.match(html, /<option value="org_billing" selected="">Billing<\/option>/);
  assert.match(html, /Not found\. Sync skips this row\./);
  assert.match(renderToStaticMarkup(
    <SsoMappingTable mappings={[]} targets={new Map()} onChange={() => undefined} onRemove={() => undefined} />,
  ), /No mappings yet/);
});
