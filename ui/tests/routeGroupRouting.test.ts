import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildSimpleRoutingPolicy,
  chooseSimpleRouting,
  defaultSimplePolicy,
  describeSimpleRouting,
  memberLabels,
  memberTrafficShares,
  moveBackup,
  percentagesFromWeights,
  plannedTrafficShares,
  readSimpleRetry,
  readSimpleRouting,
  routingLabel,
  simpleRoutingChanges,
  splitEvenly,
  validateSimpleRouting,
  addBackup,
  removeBackup,
  withSplitMembers,
  type RoutingMember,
  type SimpleRouting,
  type TextSegment,
} from '../src/lib/routeGroupRouting';
import { jsonLineDiff, policyChanges, policyChangeSummary, publisherLabel } from '../src/lib/routeGroupPolicyDiff';
import { draftIssues, modelsSummary, sharingSummary } from '../src/lib/routeGroupSummary';
import { playgroundPathFor, routeGroupSnippets, tokenPricePerMillion } from '../src/lib/routeGroupSnippets';
import { createdGroupToast, type GroupSetup } from '../src/lib/routeGroupCreate';
import { withInheritedStrategy } from '../src/lib/useAdvancedRoutingDraft';
import { lockBodyScroll } from '../src/lib/bodyScrollLock';

const MEMBERS: RoutingMember[] = [
  { deployment_id: 'dep-a', model_name: 'gpt-4o', provider: 'openai', enabled: true, weight: null, priority: 1, healthy: true },
  { deployment_id: 'dep-b', model_name: 'claude-sonnet', provider: 'anthropic', enabled: true, weight: null, priority: 0, healthy: true },
  { deployment_id: 'dep-c', model_name: 'glm-4.6', provider: 'zai', enabled: true, weight: null, priority: 2, healthy: false },
  { deployment_id: 'dep-off', model_name: 'paused', provider: 'openai', enabled: false, weight: null, priority: 3, healthy: true },
];
const RETRY_ON = { max_attempts: 2, retryable_error_classes: ['rate_limit', 'timeout'] };
const text = (segments: TextSegment[]) => segments.map((part) => (typeof part === 'string' ? part : part.strong)).join('');

test('no policy reads as spread evenly with the gateway retry default', () => {
  const { routing, reasons } = readSimpleRouting(null, null, MEMBERS);
  assert.deepEqual(reasons, []);
  assert.equal(routing?.choice, 'even');
  assert.equal(routing?.retry, 'default');
  assert.deepEqual(routing?.order, ['dep-a', 'dep-b', 'dep-c']);
  assert.equal(routingLabel(null, null, 3), 'Spread evenly');
  assert.equal(routingLabel(null, 'least-busy', 3), 'Least busy');
  assert.equal(routingLabel({ strategy: 'weighted' }, null, 1), 'Single model');
});

test('retry settings map to on, off, default, or custom', () => {
  assert.equal(readSimpleRetry(undefined), 'default');
  assert.equal(readSimpleRetry({}), 'default');
  assert.equal(readSimpleRetry(RETRY_ON), 'on');
  assert.equal(readSimpleRetry({ max_attempts: 0 }), 'off');
  assert.equal(readSimpleRetry({ max_attempts: 2 }), null);
  assert.equal(readSimpleRetry({ max_attempts: 3, retryable_error_classes: ['timeout', 'rate_limit'] }), null);
  assert.equal(readSimpleRetry({ max_attempts: 2, retryable_error_classes: ['timeout', 'rate_limit'], backoff: 1 }), null);
});

test('primary and backup orders by policy priority and ignores paused members', () => {
  const policy = {
    strategy: 'priority-based-routing',
    members: [
      { deployment_id: 'dep-a', enabled: true, priority: 1 },
      { deployment_id: 'dep-b', enabled: true, priority: 0 },
      { deployment_id: 'dep-off', enabled: true, priority: 2 },
    ],
    retry: RETRY_ON,
  };
  const { routing } = readSimpleRouting(policy, null, MEMBERS);
  assert.deepEqual(routing?.order, ['dep-b', 'dep-a']);
  assert.equal(routing?.retry, 'on');
  const inherited = readSimpleRouting({ strategy: 'priority-based-routing' }, null, MEMBERS).routing;
  assert.deepEqual(inherited?.order, ['dep-b', 'dep-a', 'dep-c']);
});

test('custom split reads weights as percentages and unlisted members as zero', () => {
  const policy = {
    strategy: 'weighted',
    members: [
      { deployment_id: 'dep-a', enabled: true, weight: 3 },
      { deployment_id: 'dep-b', enabled: true, weight: 1 },
    ],
  };
  const routing = readSimpleRouting(policy, null, MEMBERS).routing!;
  assert.deepEqual(routing.percents, { 'dep-a': '75', 'dep-b': '25', 'dep-c': '0' });
  assert.equal(validateSimpleRouting(routing, MEMBERS), null);
  assert.deepEqual(buildSimpleRoutingPolicy(routing, MEMBERS), {
    strategy: 'weighted',
    members: [
      { deployment_id: 'dep-a', enabled: true, weight: 75 },
      { deployment_id: 'dep-b', enabled: true, weight: 25 },
    ],
  });
  assert.equal(validateSimpleRouting({ ...routing, percents: { ...routing.percents, 'dep-c': '10' } }, MEMBERS),
    'Shares must add up to 100%. They add up to 110%.');
  assert.equal(validateSimpleRouting({ ...routing, percents: { ...routing.percents, 'dep-c': '1.5' } }, MEMBERS),
    'Each share must be a whole number from 0 to 100.');
});

test('advanced features keep the policy out of simple routing and say why', () => {
  const cases: Array<[Record<string, unknown>, string]> = [
    [{ strategy: 'least-busy' }, 'It uses the Least busy strategy.'],
    [{ selector: { kind: 'llm-tier' } }, 'It uses a model selector.'],
    [{ context: { mode: 'eligible-only' } }, 'It uses context-aware routing.'],
    [{ timeouts: { global_ms: 30000 } }, 'It sets a request timeout.'],
    [{ retry: { max_attempts: 5 } }, 'It uses custom retry settings.'],
    [{ members: [{ deployment_id: 'dep-a', lane: 'economy' }] }, 'It has per-model settings that only advanced routing shows.'],
    [{ strategy: 'simple-shuffle', members: [{ deployment_id: 'dep-a' }] }, 'It uses only some of the group’s models.'],
    [{ custom_flag: true }, 'It has settings that only the JSON view shows.'],
  ];
  for (const [policy, reason] of cases) {
    const read = readSimpleRouting(policy, null, MEMBERS);
    assert.equal(read.routing, null, JSON.stringify(policy));
    assert.ok(read.reasons.includes(reason), `${JSON.stringify(policy)} -> ${read.reasons.join(' ')}`);
  }
  // A context tombstone is not a context block.
  assert.equal(readSimpleRouting({ context: null }, null, MEMBERS).routing?.choice, 'even');
});

test('choices build the documented policy shapes', () => {
  const even: SimpleRouting = { choice: 'even', order: [], percents: {}, retry: 'on' };
  assert.deepEqual(buildSimpleRoutingPolicy(even, MEMBERS), {
    strategy: 'simple-shuffle', retry: { max_attempts: 2, retryable_error_classes: ['timeout', 'rate_limit'] },
  });
  assert.deepEqual(defaultSimplePolicy(), buildSimpleRoutingPolicy(even, []));
  const backup = chooseSimpleRouting({ ...even, retry: 'off' }, 'backup', MEMBERS);
  assert.deepEqual(buildSimpleRoutingPolicy(moveBackup(backup, 'dep-b', -1), MEMBERS), {
    strategy: 'priority-based-routing',
    members: [
      { deployment_id: 'dep-b', enabled: true, priority: 0 },
      { deployment_id: 'dep-a', enabled: true, priority: 1 },
      { deployment_id: 'dep-c', enabled: true, priority: 2 },
    ],
    retry: { max_attempts: 0 },
  });
  const split = chooseSimpleRouting(even, 'split', MEMBERS);
  assert.deepEqual(split.percents, { 'dep-a': '34', 'dep-b': '33', 'dep-c': '33' });
  assert.deepEqual(splitEvenly(['x', 'y']), { x: '50', y: '50' });
  assert.deepEqual(percentagesFromWeights({ a: 1, b: 1, c: 1 }), { a: 34, b: 33, c: 33 });
  assert.deepEqual(percentagesFromWeights({ a: 0 }), { a: 0 });
});

test('sentences describe each choice in plain words', () => {
  const read = readSimpleRouting({ strategy: 'simple-shuffle', retry: RETRY_ON }, null, MEMBERS).routing!;
  assert.equal(text(describeSimpleRouting(read, MEMBERS)),
    'Each request goes to one of your 3 models, picked at random, so each gets about 33% of traffic. If a call times out or is rate limited, it is retried on another model.');
  const backup = chooseSimpleRouting(read, 'backup', MEMBERS);
  assert.equal(text(describeSimpleRouting(backup, MEMBERS)),
    'Every request goes to gpt-4o. If it fails, it moves to claude-sonnet, then glm-4.6. Timeouts and rate limits count as failures.');
  assert.equal(text(describeSimpleRouting({ ...backup, retry: 'off' }, MEMBERS)),
    'Every request goes to gpt-4o. While it is unhealthy, requests move to claude-sonnet, then glm-4.6. Failed calls are not retried.');
  const split = { ...read, choice: 'split' as const, percents: { 'dep-a': '60', 'dep-b': '40', 'dep-c': '0' } };
  assert.equal(text(describeSimpleRouting(split, MEMBERS)),
    'Traffic is shared by your percentages: gpt-4o 60%, claude-sonnet 40%. If a call times out or is rate limited, it is retried on another model.');
  assert.equal(text(describeSimpleRouting(read, MEMBERS.slice(0, 1))), 'Every request goes to gpt-4o.');
});

test('change lines name what Save will do', () => {
  const baseline = readSimpleRouting({ strategy: 'weighted', members: [
    { deployment_id: 'dep-a', weight: 50 }, { deployment_id: 'dep-b', weight: 50 },
  ] }, null, MEMBERS).routing!;
  const edited = { ...baseline, percents: { ...baseline.percents, 'dep-a': '60', 'dep-b': '40' }, retry: 'on' as const };
  assert.deepEqual(simpleRoutingChanges(baseline, edited, MEMBERS), ['gpt-4o 50% → 60%', 'claude-sonnet 50% → 40%', 'Retries on']);
  const backup = readSimpleRouting({ strategy: 'priority-based-routing', members: [
    { deployment_id: 'dep-a', priority: 0 }, { deployment_id: 'dep-b', priority: 1 },
  ] }, null, MEMBERS).routing!;
  const withNew = addBackup(backup, 'dep-c');
  assert.deepEqual(withNew.order, ['dep-a', 'dep-b', 'dep-c']);
  assert.equal(addBackup(withNew, 'dep-c'), withNew);
  assert.deepEqual(simpleRoutingChanges(backup, withNew, MEMBERS), ['Add glm-4.6 as backup 2']);
  assert.deepEqual(simpleRoutingChanges(backup, removeBackup(backup, 'dep-b'), MEMBERS), ['Stop using claude-sonnet']);
  assert.equal(removeBackup(removeBackup(backup, 'dep-b'), 'dep-a').order.length, 1);
  const split = readSimpleRouting({ strategy: 'weighted', members: [{ deployment_id: 'dep-a', weight: 1 }] }, null, MEMBERS).routing!;
  const widened = withSplitMembers({ ...split, percents: { 'dep-a': '100' } }, MEMBERS);
  assert.deepEqual(widened.percents, { 'dep-a': '100', 'dep-b': '0', 'dep-c': '0' });
  assert.deepEqual(simpleRoutingChanges({ ...split, percents: { 'dep-a': '100' } }, widened, MEMBERS), []);
  assert.deepEqual(simpleRoutingChanges(backup, moveBackup(backup, 'dep-b', -1), MEMBERS), ['Primary: claude-sonnet']);
  assert.deepEqual(simpleRoutingChanges(baseline, chooseSimpleRouting(baseline, 'even', MEMBERS), MEMBERS), ['Custom split → Spread evenly']);
});

test('traffic shares skip paused and unhealthy members', () => {
  const even = memberTrafficShares({ strategy: 'simple-shuffle' }, null, MEMBERS);
  assert.deepEqual(even['dep-a'], { kind: 'percent', value: 50 });
  assert.deepEqual(even['dep-c'], { kind: 'off', reason: 'unhealthy' });
  assert.deepEqual(even['dep-off'], { kind: 'off', reason: 'paused' });
  const backup = memberTrafficShares({ strategy: 'priority-based-routing', members: [{ deployment_id: 'dep-b', priority: 0 }] }, null, MEMBERS);
  assert.deepEqual(backup['dep-b'], { kind: 'rank', rank: 0 });
  assert.deepEqual(backup['dep-a'], { kind: 'off', reason: 'unused' });
  assert.deepEqual(memberTrafficShares({ strategy: 'least-busy' }, null, MEMBERS)['dep-a'], { kind: 'varies' });
});

test('duplicate model names gain the deployment ID', () => {
  const labels = memberLabels([
    { deployment_id: 'one', model_name: 'gpt-4o', enabled: true, weight: null, priority: null },
    { deployment_id: 'two', model_name: 'gpt-4o', enabled: true, weight: null, priority: null },
    { deployment_id: 'three', model_name: null, enabled: true, weight: null, priority: null },
  ]);
  assert.deepEqual(labels, { one: 'gpt-4o (one)', two: 'gpt-4o (two)', three: 'three' });
});

test('policy diff lists human changes and a history summary', () => {
  const names = { 'dep-a': 'gpt-4o', 'dep-b': 'claude-sonnet' };
  const before = { strategy: 'weighted', members: [{ deployment_id: 'dep-a', weight: 3 }, { deployment_id: 'dep-b', weight: 2 }], timeouts: { global_ms: 45000 }, retry: { max_attempts: 1, retryable_error_classes: ['timeout'] } };
  const after = { strategy: 'weighted', members: [{ deployment_id: 'dep-a', weight: 5 }], timeouts: { global_seconds: 60 }, retry: { max_attempts: 2, retryable_error_classes: ['timeout', 'rate_limit'] }, selector: { classifier_deployment_id: 'dep-b' } };
  assert.deepEqual(policyChanges(before, after, names), [
    { label: 'gpt-4o', before: 'weight 3', after: 'weight 5' },
    { label: 'claude-sonnet', before: 'Used', after: 'Not used' },
    { label: 'Request timeout', before: '45 s', after: '60 s' },
    { label: 'Retries', before: '1 retry', after: '2 retries' },
    { label: 'Retry on', before: 'Timeout', after: 'Timeout, Rate limit' },
    { label: 'Model selector', before: 'Off', after: 'On, claude-sonnet decides' },
  ]);
  assert.equal(policyChangeSummary(before, after, names), 'gpt-4o weight 3 → weight 5; claude-sonnet Used → Not used; 4 more');
  assert.equal(policyChangeSummary(null, { strategy: 'priority-based-routing' }), 'First version: Primary & backup');
  assert.equal(policyChangeSummary(before, before), 'Same settings as the version before');
  assert.deepEqual(policyChanges({}, { members: [{ deployment_id: 'dep-a' }] }, names), [
    { label: 'Models', before: 'All group models', after: 'gpt-4o' },
  ]);
  const diff = jsonLineDiff({ timeouts: { global_ms: 45000 } }, { timeouts: { global_ms: 60000 } });
  assert.deepEqual(diff.filter((line) => line.kind !== 'same'), [
    { kind: 'removed', text: '    "global_ms": 45000' },
    { kind: 'added', text: '    "global_ms": 60000' },
  ]);
});

test('snippets call the group key and include prompt variables', () => {
  const snippets = routeGroupSnippets('support-bot', 'chat', 'DeltaLLM', ['customer_message'], 'https://gw.example/v1');
  assert.match(snippets.curl, /https:\/\/gw\.example\/v1\/chat\/completions/);
  assert.match(snippets.curl, /"model":"support-bot"/);
  assert.match(snippets.curl, /"prompt_variables":\{"customer_message":"<customer_message>"\}/);
  assert.match(snippets.python, /model="support-bot"/);
  assert.match(snippets.javascript, /model: "support-bot"/);
  assert.match(routeGroupSnippets('voice', 'audio_speech', 'DeltaLLM', [], 'https://gw.example/v1').curl, /audio\/speech[\s\S]*-o speech\.mp3/);
  assert.equal(playgroundPathFor('support/bot', 'chat'), '/playground?model=support%2Fbot&mode=chat');
  assert.equal(playgroundPathFor('embed', 'embedding'), null);
  assert.equal(tokenPricePerMillion({ input_cost_per_token: 0.0000025, output_cost_per_token: 0.00001 }), '$2.50 / $10.00');
  assert.equal(tokenPricePerMillion({}), null);
});

test('planned traffic shares follow the strategy, typed weights, and the router default weight', () => {
  const active = MEMBERS.slice(0, 2);
  assert.deepEqual(plannedTrafficShares(null, null, active), { 'dep-a': 50, 'dep-b': 50 });
  assert.deepEqual(plannedTrafficShares({ strategy: 'priority-based-routing' }, null, active), { 'dep-b': 100, 'dep-a': 0 });
  const weighted = [{ ...active[0], weight: 8 }, { ...active[1], weight: 2 }];
  assert.deepEqual(plannedTrafficShares({ strategy: 'weighted' }, null, weighted), { 'dep-a': 80, 'dep-b': 20 });
  assert.deepEqual(plannedTrafficShares({ strategy: 'weighted' }, null, active), { 'dep-a': 50, 'dep-b': 50 }, 'unset weights count as 1');
  assert.deepEqual(plannedTrafficShares({ strategy: 'weighted', members: [{ deployment_id: 'dep-a', weight: 3 }, { deployment_id: 'dep-b', weight: 1 }] }, null, active),
    { 'dep-a': 75, 'dep-b': 25 });
  assert.deepEqual(plannedTrafficShares({}, 'weighted', weighted), { 'dep-a': 80, 'dep-b': 20 }, 'an inherited strategy uses the group default');
  assert.equal(plannedTrafficShares({ strategy: 'least-busy' }, null, active), null);
  assert.equal(plannedTrafficShares({ selector: { lanes: [] } }, null, active), null);
});

test('live routing labels use the group strategy when the policy inherits it', () => {
  const cases: Array<{ policy: Record<string, unknown>; strategy: string | null; label: string }> = [
    { policy: { timeouts: { global_ms: 1000 } }, strategy: 'simple-shuffle', label: 'Spread evenly' },
    { policy: { strategy: null }, strategy: 'least-busy', label: 'Least busy' },
    { policy: { strategy: 'weighted' }, strategy: 'simple-shuffle', label: 'Custom split' },
    { policy: { mode: 'fallback' }, strategy: 'simple-shuffle', label: 'Primary & backup' },
    { policy: { mode: 'weighted' }, strategy: 'simple-shuffle', label: 'Custom split' },
    { policy: {}, strategy: null, label: 'Spread evenly' },
    { policy: { selector: { lanes: [] } }, strategy: 'weighted', label: 'Model selector' },
  ];
  for (const scenario of cases) assert.equal(routingLabel(scenario.policy, scenario.strategy, 2), scenario.label, JSON.stringify(scenario));
});

test('publisher, header, and draft check texts never show raw IDs', () => {
  assert.equal(publisherLabel({ published_by: 'user-1', published_by_email: 'ops@example.com' }), 'ops@example.com');
  assert.equal(publisherLabel({ published_by: 'admin_api' }), 'the master key');
  assert.equal(publisherLabel({ published_by: 'user-1', published_by_email: null }), 'another user');
  assert.equal(publisherLabel({ published_by: null, status: 'draft' }), 'not published');
  assert.equal(publisherLabel({ published_by: null, status: 'published' }), 'unknown');

  const access = (grants: Array<{ subject_type: string; subject_id: string }>, visibility = 'private') => ({ visibility, grants }) as never;
  assert.equal(sharingSummary(access([])), 'Private');
  assert.equal(sharingSummary(access([], 'public')), 'Public');
  assert.equal(sharingSummary(access([{ subject_type: 'team', subject_id: 't1' }]), [{ id: 't1', label: 'Support' }]), 'Shared with Support');
  assert.equal(sharingSummary(access([{ subject_type: 'team', subject_id: 't1' }, { subject_type: 'organization', subject_id: 'o1' }]), [{ id: 't1', label: 'Support' }]),
    'Shared with 1 team and 1 organization');

  assert.deepEqual(modelsSummary([]), { text: 'No models yet', warn: true });
  assert.deepEqual(modelsSummary(MEMBERS.slice(0, 2) as never), { text: '2 models, all healthy', warn: false });
  assert.deepEqual(modelsSummary(MEMBERS as never), { text: '4 models, 1 unhealthy', warn: true });
  assert.deepEqual(modelsSummary([...MEMBERS, { ...MEMBERS[0], deployment_id: 'dep-gone', healthy: null }] as never),
    { text: '5 models, 1 unhealthy, 1 not found', warn: true }, 'no health record matches the Models tab, not "unhealthy"');

  const issues = draftIssues('Pick at least one deployment.', MEMBERS as never, ['dep-a', 'dep-c', 'missing']);
  assert.deepEqual(issues.map((issue) => issue.level), ['error', 'warning', 'ok']);
  assert.match(issues[1].text, /glm-4\.6 is unhealthy/);
  assert.equal(issues[2].text, '1 included deployment is healthy.');
});

test('a simple save keeps paused models at their place and share', () => {
  const backupDoc = { strategy: 'priority-based-routing', members: [
    { deployment_id: 'dep-off', enabled: true, priority: 0 },
    { deployment_id: 'dep-a', enabled: true, priority: 1 },
    { deployment_id: 'dep-b', enabled: true, priority: 2 },
  ] };
  const backup = readSimpleRouting(backupDoc, null, MEMBERS).routing!;
  assert.deepEqual(backup.order, ['dep-a', 'dep-b'], 'the editor shows only models that take traffic');
  assert.deepEqual(backup.paused, [{ id: 'dep-off', index: 0, weight: null }]);
  const swapped = moveBackup(backup, 'dep-b', -1);
  assert.deepEqual(buildSimpleRoutingPolicy(swapped, MEMBERS).members, [
    { deployment_id: 'dep-off', enabled: true, priority: 0 },
    { deployment_id: 'dep-b', enabled: true, priority: 1 },
    { deployment_id: 'dep-a', enabled: true, priority: 2 },
  ], 'the paused primary stays first, so it is primary again when it is back');
  assert.deepEqual(simpleRoutingChanges(backup, backup, MEMBERS), []);

  const splitDoc = { strategy: 'weighted', members: [
    { deployment_id: 'dep-a', enabled: true, weight: 60 },
    { deployment_id: 'dep-off', enabled: true, weight: 20 },
    { deployment_id: 'dep-b', enabled: true, weight: 20 },
  ] };
  const split = readSimpleRouting(splitDoc, null, MEMBERS).routing!;
  assert.deepEqual(split.percents, { 'dep-a': '75', 'dep-b': '25', 'dep-c': '0' });
  assert.deepEqual(buildSimpleRoutingPolicy(split, MEMBERS).members, [
    { deployment_id: 'dep-a', enabled: true, weight: 75 },
    { deployment_id: 'dep-b', enabled: true, weight: 25 },
    { deployment_id: 'dep-off', enabled: true, weight: 20 },
  ]);
  const resumed = MEMBERS.map((member) => (member.deployment_id === 'dep-off' ? { ...member, enabled: true } : member));
  assert.equal(readSimpleRouting(splitDoc, null, resumed).routing?.percents['dep-off'], '20');
});

test('create messages say which setup step failed and keep warnings', () => {
  const setup = (patch: Partial<GroupSetup>): GroupSetup => ({ total: 2, added: 2, failed: null, error: null, warnings: [], ...patch });
  assert.deepEqual(createdGroupToast('support', [], setup({})),
    { tone: 'success', title: 'Model group created', message: '"support" is ready. It spreads requests evenly across 2 models.' });
  assert.equal(createdGroupToast('support', [], null).message, '"support" is ready for configuration.');
  const warned = createdGroupToast('support', ['Group key is long.'], setup({ warnings: ['Model is slow.'] }));
  assert.equal(warned.tone, 'info');
  assert.match(warned.message, /Group key is long\. Model is slow\./);
  const models = createdGroupToast('support', [], setup({ added: 1, failed: 'models', error: 'Deployment not found.' }));
  assert.equal(models.tone, 'error');
  assert.equal(models.message, '1 of 2 models were added. Deployment not found. Add the other models on the Models tab.');
  const routing = createdGroupToast('support', [], setup({ failed: 'routing', error: 'Policy rejected.' }));
  assert.equal(routing.message, 'The models were added, but the routing was not saved. Policy rejected. Save it on the Routing tab.');
});

test('advanced routing keeps the group default strategy when the policy has none', () => {
  assert.deepEqual(withInheritedStrategy({}, 'least-busy'), { strategy: 'least-busy' });
  assert.deepEqual(withInheritedStrategy({ timeouts: { global_ms: 1000 } }, null), { timeouts: { global_ms: 1000 }, strategy: 'simple-shuffle' });
  const legacy = { mode: 'fallback' };
  assert.equal(withInheritedStrategy(legacy, 'least-busy'), legacy);
  const explicit = { strategy: 'weighted' };
  assert.equal(withInheritedStrategy(explicit, 'least-busy'), explicit);
  assert.deepEqual(policyChanges(null, withInheritedStrategy({}, 'least-busy'), {}, 'least-busy'), []);
});

test('stacked dialogs unlock page scroll only when the last one closes, in any order', () => {
  const body = { style: { overflow: 'auto' } };
  const doc = { body } as unknown as Document;
  const drawer = lockBodyScroll(doc);
  const confirm = lockBodyScroll(doc);
  assert.equal(body.style.overflow, 'hidden');
  drawer();
  assert.equal(body.style.overflow, 'hidden', 'the confirmation is still open');
  drawer();
  confirm();
  assert.equal(body.style.overflow, 'auto');
  const again = lockBodyScroll(doc);
  assert.equal(body.style.overflow, 'hidden');
  again();
  assert.equal(body.style.overflow, 'auto');
});
