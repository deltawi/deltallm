import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveUiAccess, type UIAccessKey } from '../src/lib/authorization';
import {
  NAV_FOOTER_ITEMS,
  NAV_SECTIONS,
  navSectionLabel,
  usageNavLabel,
  visibleNavFooterItems,
  visibleNavSections,
} from '../src/lib/navigation';

const ACTION_ONLY_KEYS: UIAccessKey[] = ['model_admin', 'mcp_approvals', 'organization_create', 'team_create'];

function labels(sections: ReturnType<typeof visibleNavSections>) {
  return sections.map((section) => [section.label ?? '', section.items.map((item) => item.label)]);
}

test('every page appears in the menu exactly once', () => {
  const pageKeys = Object.keys(resolveUiAccess('master_key', null)).filter(
    (key) => !ACTION_ONLY_KEYS.includes(key as UIAccessKey),
  );
  const menuKeys = [...NAV_SECTIONS.flatMap((section) => section.items), ...NAV_FOOTER_ITEMS].map((item) => item.pageKey);
  assert.deepEqual([...menuKeys].sort(), [...pageKeys].sort());
});

test('platform administrators see every section and Settings in the footer', () => {
  const access = resolveUiAccess('master_key', null);
  assert.deepEqual(labels(visibleNavSections(access)), [
    ['', ['Dashboard', 'Playground']],
    ['Gateway', ['Models', 'Route groups', 'Provider credentials', 'Prompts', 'MCP', 'Guardrails']],
    ['Access', ['API keys', 'Organizations', 'Teams', 'People', 'Tiers']],
    ['Activity', ['Usage', 'Batch jobs', 'Audit logs']],
  ]);
  assert.deepEqual(visibleNavFooterItems(access).map((item) => item.label), ['Settings']);
});

test('members see only their pages and empty sections are hidden', () => {
  const access = resolveUiAccess('session', {
    authenticated: true,
    role: 'org_user',
    effective_permissions: ['org.read', 'team.read', 'spend.read.self'],
    ui_access: { models: true, playground: true, teams: true, organizations: true },
  });
  assert.deepEqual(labels(visibleNavSections(access)), [
    ['', ['Playground']],
    ['Gateway', ['Models']],
    ['Access', ['Organizations', 'Teams']],
  ]);
  assert.deepEqual(visibleNavFooterItems(access), []);
});

test('usage is labelled "My usage" only for accounts limited to their own spend', () => {
  assert.equal(usageNavLabel(true, []), 'Usage');
  assert.equal(usageNavLabel(false, ['spend.read']), 'Usage');
  assert.equal(usageNavLabel(false, ['spend.read.team', 'spend.read.self']), 'Usage');
  assert.equal(usageNavLabel(false, ['spend.read.self']), 'My usage');
});

test('breadcrumb prefix follows the menu section', () => {
  assert.equal(navSectionLabel('/models'), 'Gateway');
  assert.equal(navSectionLabel('/keys'), 'Access');
  assert.equal(navSectionLabel('/audit'), 'Activity');
  assert.equal(navSectionLabel('/playground'), undefined);
  assert.equal(navSectionLabel('/settings'), undefined);
});
