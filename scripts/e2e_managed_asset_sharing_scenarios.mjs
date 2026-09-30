#!/usr/bin/env node

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, '..');

function readDotEnvValue(name) {
  try {
    const contents = readFileSync(resolve(repoRoot, '.env'), 'utf8');
    for (const line of contents.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const separator = trimmed.indexOf('=');
      if (separator < 1 || trimmed.slice(0, separator).trim() !== name) continue;
      return trimmed.slice(separator + 1).trim().replace(/^(["'])(.*)\1$/, '$2');
    }
  } catch {
    return null;
  }
  return null;
}

const baseUrl = (process.env.BASE_URL || 'http://127.0.0.1:4002').replace(/\/$/, '');
const masterKey = process.env.MASTER_KEY
  || process.env.DELTALLM_MASTER_KEY
  || readDotEnvValue('DELTALLM_MASTER_KEY');
const password = process.env.SCENARIO_PASSWORD || 'AssetScenario2026!';
const runId = (process.env.RUN_ID || new Date().toISOString().replace(/\D/g, '').slice(0, 14)).toLowerCase();

if (!masterKey) {
  console.error('MASTER_KEY or DELTALLM_MASTER_KEY is required.');
  process.exit(2);
}

const ids = {
  northOrg: `org-asset-north-${runId}`,
  southOrg: `org-asset-south-${runId}`,
  alphaTeam: `team-asset-alpha-${runId}`,
  betaTeam: `team-asset-beta-${runId}`,
  southTeam: `team-asset-south-${runId}`,
};

const emails = {
  alice: `alice.owner.${runId}@deltallm.local`,
  bob: `bob.reader.${runId}@deltallm.local`,
  carol: `carol.editor.${runId}@deltallm.local`,
  dave: `dave.outsider.${runId}@deltallm.local`,
  erin: `erin.multiteam.${runId}@deltallm.local`,
};

async function api(path, {
  method = 'GET',
  admin = false,
  session = null,
  json = undefined,
  expected = [200],
} = {}) {
  const headers = { Accept: 'application/json' };
  if (admin) headers.Authorization = `Bearer ${masterKey}`;
  if (session) headers.Cookie = session;
  if (json !== undefined) headers['Content-Type'] = 'application/json';

  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers,
    body: json === undefined ? undefined : JSON.stringify(json),
  });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!expected.includes(response.status)) {
    const detail = typeof data === 'object' && data !== null
      ? JSON.stringify(data)
      : String(data || 'empty response');
    throw new Error(`${method} ${path} returned ${response.status}: ${detail.slice(0, 500)}`);
  }
  return { data, response, status: response.status };
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function itemBy(items, field, value) {
  return (items || []).find((item) => item?.[field] === value);
}

async function login(email) {
  const { response } = await api('/auth/internal/login', {
    method: 'POST',
    json: { email, password },
  });
  const setCookie = response.headers.get('set-cookie') || '';
  const match = setCookie.match(/(?:^|,\s*)(deltallm_session=[^;]+)/);
  if (!match) throw new Error(`Login for ${email} did not return a session cookie`);
  return match[1];
}

async function provision(email, organizationId, organizationRole, teamId, teamRole) {
  const { data } = await api('/ui/api/rbac/provision', {
    method: 'POST',
    admin: true,
    json: {
      email,
      mode: 'create_account',
      platform_role: 'org_user',
      password,
      is_active: true,
      organization_id: organizationId,
      organization_role: organizationRole,
      team_id: teamId,
      team_role: teamRole,
    },
  });
  assert(data?.account_id, `Provisioning ${email} returned no account_id`);
  return data.account_id;
}

async function listData(path, session) {
  const { data } = await api(path, { session });
  return Array.isArray(data?.data) ? data.data : [];
}

const results = [];
async function scenario(name, action) {
  try {
    const detail = await action();
    results.push({ name, status: 'PASS', detail: detail || '' });
    console.log(`PASS  ${name}${detail ? ` — ${detail}` : ''}`);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    results.push({ name, status: 'FAIL', detail });
    console.error(`FAIL  ${name} — ${detail}`);
  }
}

console.log(`Creating managed-asset scenario fixtures (${runId})...`);

await api('/ui/api/organizations', {
  method: 'POST',
  admin: true,
  json: { organization_id: ids.northOrg, organization_name: `Asset Scenarios North ${runId}` },
});
await api('/ui/api/organizations', {
  method: 'POST',
  admin: true,
  json: { organization_id: ids.southOrg, organization_name: `Asset Scenarios South ${runId}` },
});

for (const [teamId, teamAlias, organizationId] of [
  [ids.alphaTeam, `Asset Alpha ${runId}`, ids.northOrg],
  [ids.betaTeam, `Asset Beta ${runId}`, ids.northOrg],
  [ids.southTeam, `Asset South ${runId}`, ids.southOrg],
]) {
  await api('/ui/api/teams', {
    method: 'POST',
    admin: true,
    json: { team_id: teamId, team_alias: teamAlias, organization_id: organizationId },
  });
}

const accounts = {
  alice: await provision(emails.alice, ids.northOrg, 'org_owner', ids.alphaTeam, 'team_admin'),
  bob: await provision(emails.bob, ids.northOrg, 'org_member', ids.alphaTeam, 'team_developer'),
  carol: await provision(emails.carol, ids.northOrg, 'org_member', ids.betaTeam, 'team_developer'),
  dave: await provision(emails.dave, ids.southOrg, 'org_member', ids.southTeam, 'team_viewer'),
  erin: await provision(emails.erin, ids.northOrg, 'org_member', ids.alphaTeam, 'team_viewer'),
};

await api(`/ui/api/organizations/${ids.southOrg}/members`, {
  method: 'POST',
  admin: true,
  json: { account_id: accounts.alice, role: 'org_member' },
});
await api(`/ui/api/teams/${ids.betaTeam}/members`, {
  method: 'POST',
  admin: true,
  json: { account_id: accounts.alice, user_role: 'team_admin' },
});
await api(`/ui/api/teams/${ids.betaTeam}/members`, {
  method: 'POST',
  admin: true,
  json: { account_id: accounts.erin, user_role: 'team_viewer' },
});

const sessions = {
  alice: await login(emails.alice),
  bob: await login(emails.bob),
  carol: await login(emails.carol),
  dave: await login(emails.dave),
  erin: await login(emails.erin),
};

const { data: credential } = await api('/ui/api/named-credentials', {
  method: 'POST',
  session: sessions.alice,
  json: {
    name: `Private owner credential ${runId}`,
    provider: 'openai',
    connection_config: {
      api_key: `sk-scenario-${runId}`,
      api_base: 'https://api.openai.com/v1',
    },
    access: { grants: [] },
  },
});

const { data: identity } = await api('/ui/api/models/identity', { session: sessions.alice });
const namespace = identity.api_namespace || identity.suggested_namespace;
assert(namespace, 'Alice model identity returned no namespace');
const modelSlug = `shared-${runId.slice(-10)}`;
const apiModelId = `${namespace}/${modelSlug}`;
const { data: model } = await api('/ui/api/models', {
  method: 'POST',
  session: sessions.alice,
  json: {
    model_name: `Shared Support Model ${runId}`,
    display_name: `Shared Support Model ${runId}`,
    api_model_id: apiModelId,
    api_namespace: namespace,
    api_model_slug: modelSlug,
    named_credential_id: credential.credential_id,
    deltallm_params: { provider: 'openai', model: 'openai/gpt-4o-mini' },
    model_info: { mode: 'chat' },
    access: {
      grants: [
        { subject_type: 'team', subject_id: ids.alphaTeam, access_role: 'reader' },
        { subject_type: 'team', subject_id: ids.betaTeam, access_role: 'editor' },
      ],
    },
  },
});

const { data: prompt } = await api('/ui/api/prompt-registry/templates', {
  method: 'POST',
  session: sessions.alice,
  json: {
    template_key: `support-prompt-${runId}`,
    name: `Support Prompt ${runId}`,
    access: {
      grants: [
        { subject_type: 'team', subject_id: ids.alphaTeam, access_role: 'reader' },
        { subject_type: 'team', subject_id: ids.betaTeam, access_role: 'editor' },
      ],
    },
  },
});

const { data: routeGroup } = await api('/ui/api/route-groups', {
  method: 'POST',
  session: sessions.alice,
  json: {
    group_key: `private-route-${runId}`,
    name: `Private Route ${runId}`,
    mode: 'chat',
    access: { grants: [] },
  },
});

const { data: mcp } = await api('/ui/api/mcp-servers', {
  method: 'POST',
  session: sessions.alice,
  json: {
    server_key: `south-docs-${runId}`,
    name: `South Docs ${runId}`,
    base_url: 'https://mcp-scenario.example.com',
    access: {
      grants: [
        { subject_type: 'organization', subject_id: ids.southOrg, access_role: 'reader' },
      ],
    },
  },
});

const { data: publicPrompt } = await api('/ui/api/prompt-registry/templates', {
  method: 'POST',
  admin: true,
  json: {
    template_key: `public-help-${runId}`,
    name: `Public Help ${runId}`,
    access: {
      grants: [
        { subject_type: 'public', subject_id: null, access_role: 'reader' },
      ],
      visibility: 'public',
    },
  },
});

await scenario('Private named credential is owner-only', async () => {
  const aliceItems = await listData('/ui/api/named-credentials', sessions.alice);
  const bobItems = await listData('/ui/api/named-credentials', sessions.bob);
  const daveItems = await listData('/ui/api/named-credentials', sessions.dave);
  assert(itemBy(aliceItems, 'credential_id', credential.credential_id), 'Owner cannot see the credential');
  assert(!itemBy(bobItems, 'credential_id', credential.credential_id), 'North reader can see a private credential');
  assert(!itemBy(daveItems, 'credential_id', credential.credential_id), 'South outsider can see a private credential');
  return 'owner sees it; other users do not';
});

await scenario('Team grants distinguish Reader and Editor on prompts', async () => {
  const bobItems = await listData('/ui/api/prompt-registry/templates', sessions.bob);
  const carolItems = await listData('/ui/api/prompt-registry/templates', sessions.carol);
  const bobPrompt = itemBy(bobItems, 'template_key', prompt.template_key);
  const carolPrompt = itemBy(carolItems, 'template_key', prompt.template_key);
  assert(bobPrompt?.access?.effective_role === 'reader', 'Bob does not have Reader access');
  assert(carolPrompt?.access?.effective_role === 'editor', 'Carol does not have Editor access');
  await api(`/ui/api/prompt-registry/templates/${encodeURIComponent(prompt.template_key)}`, {
    method: 'PUT',
    session: sessions.bob,
    json: { name: 'Reader must not edit' },
    expected: [404],
  });
  const { data: updated } = await api(`/ui/api/prompt-registry/templates/${encodeURIComponent(prompt.template_key)}`, {
    method: 'PUT',
    session: sessions.carol,
    json: { name: `Support Prompt edited by Carol ${runId}` },
  });
  assert(updated?.access?.effective_role === 'editor', 'Editor update lost Editor access');
  return 'Reader denied; Editor update succeeded';
});

await scenario('Multiple team memberships use the strongest matching grant', async () => {
  const erinItems = await listData('/ui/api/prompt-registry/templates', sessions.erin);
  const erinPrompt = itemBy(erinItems, 'template_key', prompt.template_key);
  assert(erinPrompt?.access?.effective_role === 'editor', 'Reader+Editor memberships did not resolve to Editor');
  return 'Reader + Editor resolved to Editor';
});

await scenario('Shared model keeps its owner private credential opaque', async () => {
  const bobItems = await listData('/ui/api/models', sessions.bob);
  const bobModel = itemBy(bobItems, 'deployment_id', model.deployment_id);
  assert(bobModel?.access?.effective_role === 'reader', 'Bob cannot read the shared model');
  assert(bobModel?.credential_binding?.credential_access === 'opaque', 'Private credential binding is not opaque');
  assert(bobModel?.named_credential_id == null, 'Private credential id leaked to model reader');
  await api(`/ui/api/models/${encodeURIComponent(model.deployment_id)}`, {
    method: 'PUT',
    session: sessions.bob,
    json: { display_name: 'Reader must not edit model' },
    expected: [404],
  });
  const { data: updated } = await api(`/ui/api/models/${encodeURIComponent(model.deployment_id)}`, {
    method: 'PUT',
    session: sessions.carol,
    json: { display_name: `Shared Support Model edited by Carol ${runId}` },
  });
  assert(updated?.access?.effective_role === 'editor', 'Carol did not retain Editor access');
  assert(updated?.credential_binding?.credential_access === 'opaque', 'Editor received private credential visibility');
  return 'Reader denied edit; Editor updated without credential disclosure';
});

await scenario('A private model group can be shared after creation', async () => {
  const bobBefore = await listData('/ui/api/route-groups', sessions.bob);
  assert(!itemBy(bobBefore, 'group_key', routeGroup.group_key), 'Bob saw the private route group');
  const { data: accessUpdate } = await api(`/ui/api/assets/${routeGroup.access.managed_asset_id}/access`, {
    method: 'PUT',
    session: sessions.alice,
    json: {
      grants: [
        { subject_type: 'organization', subject_id: ids.northOrg, access_role: 'editor' },
      ],
      expected_policy_version: routeGroup.access.policy_version,
    },
  });
  assert(accessUpdate?.access?.visibility === 'organization', 'Route group did not become organization-shared');
  const { data: bobDetail } = await api(`/ui/api/route-groups/${encodeURIComponent(routeGroup.group_key)}`, {
    session: sessions.bob,
  });
  assert(bobDetail?.group?.access?.effective_role === 'editor', 'Bob did not gain organization Editor access');
  await api(`/ui/api/route-groups/${encodeURIComponent(routeGroup.group_key)}`, {
    method: 'PUT',
    session: sessions.bob,
    json: { name: `North Route edited by Bob ${runId}` },
  });
  await api(`/ui/api/route-groups/${encodeURIComponent(routeGroup.group_key)}`, {
    session: sessions.dave,
    expected: [404],
  });
  return 'private → organization Editor worked; other org stayed denied';
});

await scenario('Organization-shared MCP is readable but not writable', async () => {
  const { data: daveDetail } = await api(`/ui/api/mcp-servers/${mcp.mcp_server_id}`, {
    session: sessions.dave,
  });
  assert(daveDetail?.server?.access?.effective_role === 'reader', 'South member did not receive Reader access');
  await api(`/ui/api/mcp-servers/${mcp.mcp_server_id}`, {
    method: 'PATCH',
    session: sessions.dave,
    json: { name: 'Reader must not edit MCP' },
    expected: [404],
  });
  await api(`/ui/api/mcp-servers/${mcp.mcp_server_id}`, {
    session: sessions.bob,
    expected: [404],
  });
  return 'South org can read; write and unrelated-org access denied';
});

await scenario('Only a platform admin can publish an asset publicly', async () => {
  await api('/ui/api/named-credentials', {
    method: 'POST',
    session: sessions.alice,
    json: {
      name: `Forbidden Public Credential ${runId}`,
      provider: 'openai',
      connection_config: { api_key: `sk-forbidden-${runId}`, api_base: 'https://api.openai.com/v1' },
      access: {
        grants: [{ subject_type: 'public', subject_id: null, access_role: 'reader' }],
        visibility: 'public',
      },
    },
    expected: [403],
  });
  for (const session of [sessions.bob, sessions.dave]) {
    const items = await listData('/ui/api/prompt-registry/templates', session);
    const visible = itemBy(items, 'template_key', publicPrompt.template_key);
    assert(visible?.access?.effective_role === 'reader', 'Public prompt is not visible as Reader');
  }
  await api(`/ui/api/prompt-registry/templates/${encodeURIComponent(publicPrompt.template_key)}`, {
    method: 'PUT',
    session: sessions.dave,
    json: { name: 'Public reader must not edit' },
    expected: [404],
  });
  return 'creator publish denied; admin public asset readable across orgs';
});

await scenario('Editors cannot take ownership, reshare, or delete', async () => {
  await api(`/ui/api/assets/${prompt.access.managed_asset_id}/access`, {
    method: 'PUT',
    session: sessions.carol,
    json: { grants: [], expected_policy_version: prompt.access.policy_version },
    expected: [403],
  });
  await api(`/ui/api/prompt-registry/templates/${encodeURIComponent(prompt.template_key)}`, {
    method: 'DELETE',
    session: sessions.carol,
    expected: [404],
  });
  return 'reshare and delete remained Owner-only';
});

const failures = results.filter((result) => result.status === 'FAIL');
console.log('\nFixture summary');
console.log(JSON.stringify({
  run_id: runId,
  base_url: baseUrl,
  organizations: [ids.northOrg, ids.southOrg],
  teams: [ids.alphaTeam, ids.betaTeam, ids.southTeam],
  users: emails,
  password,
  assets: {
    named_credential_id: credential.credential_id,
    model_deployment_id: model.deployment_id,
    model_api_id: apiModelId,
    prompt_template_key: prompt.template_key,
    route_group_key: routeGroup.group_key,
    mcp_server_id: mcp.mcp_server_id,
    public_prompt_template_key: publicPrompt.template_key,
  },
  scenarios: results,
}, null, 2));

if (failures.length > 0) process.exit(1);
