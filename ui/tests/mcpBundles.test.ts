import assert from 'node:assert/strict';
import test from 'node:test';
import type { MCPBundle } from '../src/lib/api/mcpBundles';
import {
  BUNDLE_KEY_PATTERN,
  bundleEndpointUrl,
  bundleToolSnippet,
  createPayload,
  draftFromBundle,
  emptyBundleDraft,
  exposedToolName,
  findNameConflicts,
  memberToolNames,
  previewMemberInputs,
  suggestBundleKey,
  updatePayload,
  validateDraft,
  type BundleDraft,
  type BundleMemberDraft,
} from '../src/lib/mcpBundles';

const TOOLS = { 'srv-gh': ['create_issue', 'search'], 'srv-jira': ['search', 'create_issue'] };

function member(overrides: Partial<BundleMemberDraft> = {}): BundleMemberDraft {
  return { mcp_server_id: 'srv-gh', server_key: 'github', server_name: 'GitHub', all_tools: true, tool_names: [], name_prefix: '', ...overrides };
}

function draft(members: BundleMemberDraft[], overrides: Partial<BundleDraft> = {}): BundleDraft {
  return { ...emptyBundleDraft('global'), bundle_key: 'eng', name: 'Eng', members, ...overrides };
}

const JIRA = member({ mcp_server_id: 'srv-jira', server_key: 'jira', server_name: 'Jira' });

test('exposed names use the prefix and an underscore', () => {
  assert.equal(exposedToolName('gh', 'search'), 'gh_search');
  assert.equal(exposedToolName('  ', 'search'), 'search');
  assert.equal(exposedToolName(null, 'search'), 'search');
});

test('member tools follow the server order and keep unknown names', () => {
  assert.deepEqual(memberToolNames(member(), TOOLS['srv-gh']), ['create_issue', 'search']);
  assert.deepEqual(
    memberToolNames(member({ all_tools: false, tool_names: ['search', 'gone'] }), TOOLS['srv-gh']),
    ['search', 'gone'],
  );
});

test('two servers without a prefix conflict on a shared tool name', () => {
  const conflicts = findNameConflicts([member(), JIRA], TOOLS);
  assert.deepEqual(conflicts.map((item) => item.name).sort(), ['create_issue', 'search']);
  assert.deepEqual(conflicts.find((item) => item.name === 'search')?.sources, ['github.search', 'jira.search']);
  assert.deepEqual(findNameConflicts([member({ name_prefix: 'gh' }), JIRA], TOOLS), []);
});

test('validation names the problems that stop a save', () => {
  assert.deepEqual(validateDraft(draft([member({ name_prefix: 'gh' }), JIRA]), TOOLS), []);
  const problems = validateDraft(
    draft([member({ all_tools: false, tool_names: [] }), JIRA, member({ mcp_server_id: 'srv-x', server_key: 'x', name_prefix: 'a b' })], {
      bundle_key: '-bad',
      name: ' ',
      owner_scope_type: 'team',
      owner_scope_id: '',
    }),
    { ...TOOLS, 'srv-x': ['tool_search'] },
  );
  assert.ok(problems.some((item) => item.startsWith('Bundle key')));
  assert.ok(problems.includes('Name is required.'));
  assert.ok(problems.includes('Select the team that owns the bundle.'));
  assert.ok(problems.includes('Select at least one tool of "github".'));
  assert.ok(problems.some((item) => item.includes('name prefix of "x"')));
  const reserved = validateDraft(draft([member({ mcp_server_id: 'srv-x', server_key: 'x', name_prefix: 'mcp' })]), { 'srv-x': ['tool_search'] });
  assert.ok(reserved.some((item) => item.includes('"mcp_tool_search" is reserved')));
  const conflict = validateDraft(draft([member(), JIRA]), TOOLS);
  assert.ok(conflict.some((item) => item.includes('github.search and jira.search')));
});

test('payloads trim values and send all_tools or a tool list', () => {
  const value = draft(
    [member({ name_prefix: ' gh ' }), { ...JIRA, all_tools: false, tool_names: ['search'] }],
    { bundle_key: ' Eng ', name: ' Eng ', description: ' ', owner_scope_type: 'organization', owner_scope_id: 'org-1' },
  );
  assert.deepEqual(createPayload(value), {
    bundle_key: 'eng',
    name: 'Eng',
    description: null,
    owner_scope_type: 'organization',
    owner_scope_id: 'org-1',
    members: [
      { mcp_server_id: 'srv-gh', all_tools: true, name_prefix: 'gh' },
      { mcp_server_id: 'srv-jira', all_tools: false, tool_names: ['search'], name_prefix: null },
    ],
  });
  assert.deepEqual(Object.keys(updatePayload(value)).sort(), ['description', 'members', 'name']);
  assert.equal(createPayload({ ...value, owner_scope_type: 'global' }).owner_scope_id, null);
});

test('the preview leaves out a member with no selected tool', () => {
  const inputs = previewMemberInputs([member({ all_tools: false, tool_names: [] }), JIRA]);
  assert.deepEqual(inputs.map((item) => item.mcp_server_id), ['srv-jira']);
});

test('a saved bundle becomes an editable draft', () => {
  const bundle: MCPBundle = {
    mcp_bundle_id: 'b-1',
    bundle_key: 'eng',
    name: 'Eng',
    description: null,
    owner_scope_type: 'team',
    owner_scope_id: 'team-1',
    enabled: true,
    tool_count: 3,
    capabilities: { can_mutate: true },
    members: [{
      mcp_server_id: 'srv-gh', server_key: 'github', server_name: 'GitHub', server_enabled: true,
      all_tools: false, tool_names: ['search'], name_prefix: null, tool_count: 1, server_tool_count: 2,
    }],
  };
  const value = draftFromBundle(bundle);
  assert.equal(value.owner_scope_id, 'team-1');
  assert.deepEqual(value.members[0], member({ all_tools: false, tool_names: ['search'] }));
});

test('keys, endpoint and snippet', () => {
  assert.equal(suggestBundleKey('  Eng Assist! '), 'eng-assist');
  assert.ok(BUNDLE_KEY_PATTERN.test(suggestBundleKey('Eng Assist')));
  assert.equal(bundleEndpointUrl('eng', 'https://gw.example.com'), 'https://gw.example.com/mcp/eng');
  assert.deepEqual(JSON.parse(bundleToolSnippet('eng')), { tools: [{ type: 'mcp', server: 'eng' }] });
});
