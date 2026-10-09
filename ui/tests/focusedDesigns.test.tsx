import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import { KeyRound } from 'lucide-react';
import { IconTabs } from '../src/components/admin/shells';
import ApiKeyEditorDialog from '../src/components/api-keys/ApiKeyEditorDialog';
import ApiKeySecretDialog from '../src/components/api-keys/ApiKeySecretDialog';
import PolicyMemberPicker from '../src/components/route-groups/PolicyMemberPicker';
import RouteGroupPolicyEditor from '../src/components/route-groups/RouteGroupPolicyEditor';
import RouteGroupHero from '../src/components/route-groups/RouteGroupHero';
import { AuthProvider } from '../src/lib/auth';
import { emptyKeyForm } from '../src/lib/apiKeyForm';
import { buildPolicyFromGuided, configuredWeightShares, GUIDED_POLICY_DEFAULTS, moveGuidedPolicyMember, orderedGuidedMemberIds, toGuidedPolicy, type PolicyGuidedValues } from '../src/lib/routeGroups';
import type { RouteGroup, RouteGroupMemberDetail, RoutePolicy } from '../src/lib/api';

function setup() {
  const dom = new JSDOM('<button id="trigger">Open editor</button><div id="root"></div>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const globals = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return this.parentNode; } });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { value: () => {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { value: () => {} });
  const root = createRoot(document.getElementById('root')!);
  return { dom, root, cleanup: async () => {
    await act(async () => root.unmount());
    dom.window.close();
    for (const key of Object.keys(globals)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  } };
}
const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === text)!;
const members = [
  { deployment_id: 'late', enabled: true, weight: 2, priority: 5 },
  { deployment_id: 'first', enabled: true, weight: 8, priority: 0 },
  { deployment_id: 'disabled', enabled: false, weight: 1, priority: 1 },
];

test('priority controls reorder inherited and explicit lists and serialize the same order', async () => {
  const env = setup();
  let current: PolicyGuidedValues = { ...GUIDED_POLICY_DEFAULTS, strategy: 'priority-based-routing', memberIds: ['late', 'first'] };
  function Harness() {
    const [values, setValues] = useState(current);
    return <PolicyMemberPicker values={values} members={members} onChange={(next) => { current = next; setValues(next); }} />;
  }
  try {
    await act(async () => env.root.render(<Harness />));
    assert.deepEqual(orderedGuidedMemberIds(current, members), ['first', 'late']);
    assert.equal(document.querySelector<HTMLButtonElement>('[aria-label="Move first earlier"]')?.disabled, true);
    const up = document.querySelector<HTMLButtonElement>('[aria-label="Move late earlier"]')!;
    up.focus();
    assert.equal(document.activeElement, up);
    await act(async () => up.click());
    assert.equal(current.memberSelection, 'explicit');
    assert.deepEqual(current.memberIds, ['late', 'first']);
    assert.deepEqual(buildPolicyFromGuided({ server_hint: 'preserved' }, current).members, [{ deployment_id: 'late', enabled: true, priority: 0 }, { deployment_id: 'first', enabled: true, priority: 1 }]);
    assert.equal(document.activeElement, up, 'focus stays on the moved deployment');
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Move late later"]')!.click());
    assert.deepEqual(current.memberIds, ['first', 'late']);
    assert.equal(document.querySelector<HTMLButtonElement>('[aria-label="Include disabled"]')?.disabled, true);
    assert.equal(moveGuidedPolicyMember(current, members, 'first', -1), current);
    assert.equal(moveGuidedPolicyMember(current, members, 'missing', 1), current);
  } finally { await env.cleanup(); }
});

test('stored priorities and known weighted shares use the current policy and membership', () => {
  const values = toGuidedPolicy({ strategy: 'priority-based-routing', members: [{ deployment_id: 'late', priority: 4 }, { deployment_id: 'first', priority: 0 }] }, members);
  assert.deepEqual(values.memberIds, ['first', 'late']);
  const weighted = { ...values, strategy: 'weighted' };
  assert.deepEqual(configuredWeightShares(weighted, members), { first: 80, late: 20 });
  assert.equal(configuredWeightShares(weighted, members.map((member) => ({ ...member, weight: null }))), null);
  const unknownPriorities = members.map((member) => ({ ...member, priority: null }));
  assert.deepEqual(orderedGuidedMemberIds({ ...values, memberSelection: 'inherit' }, unknownPriorities), ['late', 'first']);
});

test('key dialog traps focus, supports keyboard tabs, keeps values, locks close while saving, and restores focus', async () => {
  const env = setup();
  const trigger = document.getElementById('trigger')!;
  trigger.focus();
  let closes = 0;
  let submitted = 0;
  const props = {
    title: 'Create API Key', form: { ...emptyKeyForm(), key_name: 'Support', team_id: 'team-1', rph_limit: '123' },
    onChange: () => {}, onTeamChange: () => {}, teams: [{ team_id: 'team-1', team_alias: 'Platform' }],
    selfService: false, editing: false, policy: null, serviceAccounts: [], serviceAccountsLoading: false,
    newServiceAccountName: '', onNewServiceAccountNameChange: () => {}, creatingServiceAccount: false,
    onCreateServiceAccount: () => {}, saving: false, saveDisabled: false, error: null,
    onClose: () => { closes += 1; }, onSave: () => { submitted += 1; },
    assetAccess: { title: 'Key access', mode: 'inherit' as const, targets: [], selectedKeys: [], onSelectedKeysChange: () => {} },
  };
  try {
    await act(async () => env.root.render(<ApiKeyEditorDialog {...props} />));
    assert.equal((document.activeElement as HTMLInputElement).value, 'Support');
    const details = document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')!;
    details.focus();
    await act(async () => details.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'End', bubbles: true })));
    assert.equal(document.activeElement?.textContent, 'Limits');
    assert.equal(document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.textContent, 'Limits');
    assert.equal(document.querySelector<HTMLInputElement>('input[value="123"]')?.value, '123');
    assert.ok(document.querySelector('input[type="datetime-local"]'));
    assert.equal(document.querySelector('[role="tabpanel"]:not([hidden])')?.getAttribute('aria-labelledby'), document.activeElement?.id);
    const save = button('Create key');
    save.focus();
    await act(async () => save.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })));
    assert.equal(document.activeElement?.getAttribute('aria-label'), 'Close dialog');
    await act(async () => button('Create key').click());
    assert.equal(submitted, 1);
    await act(async () => env.root.render(<ApiKeyEditorDialog {...props} saving />));
    await act(async () => document.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(closes, 0);
    assert.equal(button('Cancel').disabled, true);
    await act(async () => env.root.render(<ApiKeyEditorDialog {...props} />));
    await act(async () => document.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(closes, 1);
    await act(async () => env.root.render(null));
    assert.equal(document.activeElement, trigger);
    assert.equal(document.body.style.overflow, '');
  } finally { await env.cleanup(); }
});

test('copy confirms only after clipboard success and reports clipboard failure', async () => {
  const env = setup();
  const secret = 'test-only-secret';
  let fail = true;
  let copied = '';
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { if (fail) throw new Error('Unavailable'); copied = text; } } });
  try {
    await act(async () => env.root.render(<ApiKeySecretDialog secret={secret} onClose={() => {}} />));
    await act(async () => button('Copy key').click());
    assert.match(document.querySelector('[role="alert"]')?.textContent || '', /Could not copy/);
    assert.equal(copied, '');
    fail = false;
    await act(async () => button('Copy key').click());
    assert.equal(copied, secret);
    assert.match(document.querySelector('[role="status"]')?.textContent || '', /copied/);
    assert.equal(document.querySelector('[role="alert"]'), null);
    assert.equal(window.localStorage.length, 0);
    assert.equal(window.sessionStorage.length, 0);
    await act(async () => env.root.render(null));
    assert.ok(!document.body.textContent?.includes(secret));
  } finally { await env.cleanup(); }
});

test('policy live summary uses the published strategy and read-only controls stay locked', () => {
  const published = { route_policy_id: 'p1', version: 2, status: 'published', policy_json: { strategy: 'weighted' } } as RoutePolicy;
  const props = { routeGroupId: 'group-1', workloadMode: 'chat', guidedPolicy: { ...GUIDED_POLICY_DEFAULTS, strategy: 'priority-based-routing' }, members: [] as RouteGroupMemberDetail[], guidedPreview: '{}', simulationPolicy: {}, canSimulate: true, canWrite: false, policyText: '{}', policyMessage: null, policyError: null, isPolicyBusy: false, policyAction: null, showAdvancedJson: false, hasMembers: false, onToggleAdvancedJson: () => {}, onGuidedPolicyChange: () => {}, onPolicyTextChange: () => {}, onValidate: () => {}, onSaveDraft: () => {}, onPublish: () => {}, policies: [published], loadingPolicies: false, hasPoliciesError: false };
  const html = renderToStaticMarkup(<AuthProvider><RouteGroupPolicyEditor {...props} /></AuthProvider>);
  assert.match(html, /Weighted · Published v2/);
  assert.match(html, /Draft editor/);
  assert.match(html, /do not have permission/);
  assert.match(html, /<fieldset disabled/);
  assert.match(html, /Add at least one deployment/);
  const unavailable = renderToStaticMarkup(<AuthProvider><RouteGroupPolicyEditor {...props} canWrite hasMembers hasPoliciesError /></AuthProvider>);
  assert.match(unavailable, /Live policy unavailable/);
  assert.match(unavailable, /<fieldset disabled/);
  assert.match(unavailable, /<button[^>]+disabled[^>]*>Publish<\/button>/);
  assert.match(renderToStaticMarkup(<AuthProvider><RouteGroupPolicyEditor {...props} loadingPolicies /></AuthProvider>), /Loading live policy/);
});

test('both live policy displays use the group strategy when the policy inherits it', () => {
  const group: RouteGroup = { route_group_id: 'group-1', group_key: 'support', name: 'Support',
    mode: 'chat', routing_strategy: 'simple-shuffle', enabled: true, member_count: 1, metadata: null };
  const props = { routeGroupId: group.route_group_id, workloadMode: group.mode, guidedPolicy: GUIDED_POLICY_DEFAULTS,
    members: [] as RouteGroupMemberDetail[], guidedPreview: '{}', simulationPolicy: {}, canSimulate: true,
    canWrite: true, policyText: '{}', policyMessage: null, policyError: null, isPolicyBusy: false,
    policyAction: null, showAdvancedJson: true, hasMembers: true, onToggleAdvancedJson: () => {},
    onGuidedPolicyChange: () => {}, onPolicyTextChange: () => {}, onValidate: () => {},
    onSaveDraft: () => {}, onPublish: () => {}, loadingPolicies: false, hasPoliciesError: false };
  const cases: Array<{ policy: Record<string, unknown>; strategy: string | null; label: string; heroLabel?: string }> = [
    { policy: { timeouts: { global_ms: 1000 } }, strategy: 'simple-shuffle', label: 'Shuffle' },
    { policy: { strategy: null }, strategy: 'least-busy', label: 'Least Busy' },
    { policy: { strategy: 'weighted' }, strategy: 'simple-shuffle', label: 'Weighted' },
    { policy: { mode: 'fallback' }, strategy: 'simple-shuffle', label: 'Priority', heroLabel: 'Primary &amp; fallback' },
    { policy: { mode: 'weighted' }, strategy: 'simple-shuffle', label: 'Weighted' },
    { policy: {}, strategy: null, label: 'Group default' },
  ];
  for (const scenario of cases) {
    const published = { route_policy_id: 'p1', version: 2, status: 'published', policy_json: scenario.policy } as RoutePolicy;
    const hero = renderToStaticMarkup(<RouteGroupHero group={{ ...group, routing_strategy: scenario.strategy }} publishedPolicy={published}
      members={1} healthyMembers={1} unknownHealth={0} missingMembers={0} promptKey={null} teamOptions={[]}
      organizationOptions={[]} canWrite canDelete onEdit={() => {}} onDelete={() => {}} />);
    const editor = renderToStaticMarkup(<AuthProvider><RouteGroupPolicyEditor {...props} policies={[published]} defaultStrategy={scenario.strategy} /></AuthProvider>);
    assert.ok(hero.includes(`Published v2 · ${scenario.heroLabel || scenario.label}`), hero);
    assert.ok(editor.includes(`${scenario.label} · Published v2`), editor);
  }
  const inheritedDefault = renderToStaticMarkup(<AuthProvider><RouteGroupPolicyEditor {...props} policies={[]} defaultStrategy={null} /></AuthProvider>);
  assert.match(inheritedDefault, />Group default<\/p>/);
  assert.doesNotMatch(inheritedDefault, /Group default · Group default/);
});

for (const variant of ['line', 'card'] as const) {
  test(`${variant} tabs support arrow keys, wrapping, Home, End, and panel references`, async () => {
    const env = setup();
    const items = ['one', 'two', 'three'].map((id) => ({ id, label: id, icon: KeyRound }));
    function Harness() {
      const [active, setActive] = useState('one');
      return <><IconTabs id="tabs" label="Example" items={items} active={active} onChange={setActive} variant={variant} />
        {items.map(({ id }) => <div key={id} id={`tabs-panel-${id}`} role="tabpanel" aria-labelledby={`tabs-tab-${id}`} hidden={active !== id}>{id}</div>)}
      </>;
    }
    try {
      await act(async () => env.root.render(<Harness />));
      document.getElementById('tabs-tab-one')!.focus();
      for (const [key, expected] of [['ArrowLeft', 'three'], ['ArrowRight', 'one'], ['End', 'three'], ['Home', 'one'], ['ArrowRight', 'two']]) {
        await act(async () => document.activeElement!.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key, bubbles: true })));
        assert.equal(document.activeElement?.id, `tabs-tab-${expected}`);
        assert.equal(document.activeElement?.getAttribute('aria-selected'), 'true');
        assert.equal(document.querySelectorAll('[role="tab"][tabindex="0"]').length, 1);
        const panel = document.getElementById(document.activeElement!.getAttribute('aria-controls')!)!;
        assert.ok(panel);
        assert.equal(panel.hidden, false);
      }
    } finally { await env.cleanup(); }
  });
}
