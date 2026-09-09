import assert from 'node:assert/strict';
import test from 'node:test';
import { buildPolicyFromGuided, restoreDraftPolicyTombstones, toGuidedPolicy, validateGuidedPolicy } from '../src/lib/routeGroups';
import { chooseSelector, eligibleClassifiers, selectorPublishConfirmation } from '../src/lib/routeGroupSelector';

const members = ['mini', 'large'].map((id) => ({ deployment_id: id, enabled: true, mode: 'chat', weight: null, priority: null }));
const enabled = () => chooseSelector(toGuidedPolicy({}, members), 'mini');

test('choosing a selector supplies safe defaults and explicit reviewable assignments', () => {
  const guided = enabled();
  assert.equal(validateGuidedPolicy(guided, members, 'chat'), null);
  assert.equal(guided.selector.defaultLane, 'quality');
  assert.equal(guided.selector.assignments.mini, 'economy');
  assert.equal(guided.selector.assignments.large, 'quality');
  assert.equal(guided.contextUnknownCapacity, 'exclude');
  const policy = buildPolicyFromGuided({}, guided);
  assert.deepEqual(policy.selector, {
    kind: 'llm-tier', classifier_deployment_id: 'mini', timeout_ms: 750, max_input_chars: 8000,
    default_lane: 'quality', lanes: guided.selector.lanes.map((lane) => ({ ...lane, rank: Number(lane.rank) })),
  });
  assert.deepEqual(toGuidedPolicy(policy, members).selector, guided.selector);
});

test('disabling is explicit and draft removal survives a published-selector round trip', () => {
  const base = buildPolicyFromGuided({}, enabled());
  const policy = buildPolicyFromGuided(base, chooseSelector(toGuidedPolicy(base, members), ''));
  assert.equal(policy.selector, null);
  assert.ok((policy.members as Array<Record<string, unknown>>).every((member) => !('lane' in member)));
  assert.equal(restoreDraftPolicyTombstones({ strategy: 'weighted' }, base).selector, null);
  assert.equal('selector' in buildPolicyFromGuided({}, toGuidedPolicy({}, members)), false);
  const validated = restoreDraftPolicyTombstones({ members: policy.members }, policy);
  assert.equal(buildPolicyFromGuided(validated, toGuidedPolicy(validated, members)).selector, null);
  assert.equal(selectorPublishConfirmation(validated, base)?.title, 'Disable the model selector?');
});

test('an explicit None choice removes the selector even when imported JSON omitted it', () => {
  const active = buildPolicyFromGuided({}, enabled());
  const imported = { strategy: 'least-busy', opaque: { retained: true } };
  const untouched = toGuidedPolicy(imported, members);
  assert.equal('selector' in buildPolicyFromGuided(imported, untouched), false);
  for (const before of [untouched, chooseSelector(untouched, 'mini')]) {
    const removed = chooseSelector(before, '');
    assert.equal(validateGuidedPolicy(removed, members, 'chat'), null);
    const payload = buildPolicyFromGuided(imported, removed);
    assert.equal(payload.selector, null);
    assert.deepEqual(payload.opaque, imported.opaque);
    assert.equal('selector' in imported, false);
    assert.equal(selectorPublishConfirmation(payload, active)?.title, 'Disable the model selector?');
    const roundTrip = JSON.parse(JSON.stringify(payload));
    assert.equal(buildPolicyFromGuided(roundTrip, toGuidedPolicy(roundTrip, members)).selector, null);
    const validated = restoreDraftPolicyTombstones({ strategy: payload.strategy }, payload);
    assert.equal(buildPolicyFromGuided(validated, toGuidedPolicy(validated, members)).selector, null);
    const reenabled = chooseSelector(toGuidedPolicy(roundTrip, members), 'mini');
    assert.equal(validateGuidedPolicy(reenabled, members, 'chat'), null);
    assert.equal((buildPolicyFromGuided(roundTrip, reenabled).selector as { classifier_deployment_id: string }).classifier_deployment_id, 'mini');
  }
});

test('explicit selector removal clears imported member lanes but preserves unrelated fields', () => {
  const imported = {
    members: members.map((member) => ({ ...member, lane: 'old-lane', opaque: 'retained' })),
  };
  const removed = chooseSelector(toGuidedPolicy(imported, members), '');
  const payload = buildPolicyFromGuided(imported, removed);
  assert.equal(payload.selector, null);
  for (const member of payload.members as Array<Record<string, unknown>>) {
    assert.equal('lane' in member, false);
    assert.equal(member.opaque, 'retained');
  }
  assert.equal(imported.members[0].lane, 'old-lane');
});

test('publication distinguishes omitted, removed, configured and unsupported selectors without rewriting input', () => {
  const active = buildPolicyFromGuided({}, enabled());
  const replacement = buildPolicyFromGuided(active, chooseSelector(toGuidedPolicy(active, members), 'large'));
  const cases: Array<{ policy: Record<string, unknown>; title: string; description: RegExp }> = [
    { policy: { strategy: 'least-busy' }, title: 'Publish model routing?', description: /selector unchanged.*customers continue to pay/ },
    { policy: { selector: null }, title: 'Disable the model selector?', description: /without classification/ },
    { policy: replacement, title: 'Publish model routing?', description: /sent to large.*customers pay/ },
    { policy: { selector: { kind: 'future', opaque: 'retained' } }, title: 'Publish model routing?', description: /cannot be interpreted.*server will validate.*customer charges/ },
  ];
  for (const { policy, title, description } of cases) {
    const before = structuredClone({ policy, active });
    const confirmation = selectorPublishConfirmation(Object.freeze(policy), Object.freeze(active));
    assert.equal(confirmation?.title, title);
    assert.match(confirmation!.description, description);
    assert.deepEqual({ policy, active }, before);
  }
  assert.match(selectorPublishConfirmation(active, null)!.description, /sent to mini.*Evaluation is optional/);
  assert.match(selectorPublishConfirmation({}, { selector: { kind: 'future' } })!.description, /selector unchanged/);
});

test('publication does not introduce a selector confirmation when no selector is configured', () => {
  for (const active of [null, {}, { selector: null }]) {
    assert.equal(selectorPublishConfirmation({ strategy: 'least-busy' }, active), null);
    assert.equal(selectorPublishConfirmation({ selector: null }, active), null);
  }
});

test('selector editing preserves unrelated opaque policy and member fields', () => {
  const base = buildPolicyFromGuided({ opaque: { version: 9 } }, enabled());
  (base.members as Array<Record<string, unknown>>)[0].opaque = { pricing: 'retained' };
  const result = buildPolicyFromGuided(base, toGuidedPolicy(base, members));
  assert.deepEqual(result.opaque, { version: 9 });
  assert.deepEqual((result.members as Array<Record<string, unknown>>)[0].opaque, { pricing: 'retained' });
});

test('unknown selector shapes are not silently replaced by guided editing', () => {
  const base = { selector: { kind: 'future', secret_reference: 'opaque' } };
  const guided = toGuidedPolicy(base, members);
  assert.match(validateGuidedPolicy(guided, members, 'chat')!, /Raw JSON/);
  assert.deepEqual(buildPolicyFromGuided(base, guided).selector, base.selector);
});

test('only concrete enabled selected chat members are offered', () => {
  assert.deepEqual(eligibleClassifiers([
    ...members, { ...members[0], deployment_id: 'disabled', enabled: false },
    { ...members[0], deployment_id: 'unknown', mode: undefined },
    { ...members[0], deployment_id: 'embedding', mode: 'embedding' },
  ], ['mini', 'disabled', 'unknown', 'embedding']).map((member) => member.deployment_id), ['mini']);
});

for (const [name, change, message] of [
  ['duplicate rank', (g: ReturnType<typeof enabled>) => { g.selector.lanes[1].rank = '0'; }, /ranks/],
  ['duplicate ID', (g: ReturnType<typeof enabled>) => { g.selector.lanes[1].id = 'economy'; }, /unique/],
  ['unknown assignment', (g: ReturnType<typeof enabled>) => { g.selector.assignments.large = 'other'; }, /Assign/],
  ['empty lane', (g: ReturnType<typeof enabled>) => { g.selector.assignments.large = 'economy'; }, /at least one/],
  ['timeout bound', (g: ReturnType<typeof enabled>) => { g.selector.timeoutMs = '5001'; }, /timeout/],
  ['input bound', (g: ReturnType<typeof enabled>) => { g.selector.maxInputChars = '255'; }, /input/],
  ['safe default', (g: ReturnType<typeof enabled>) => { g.selector.defaultLane = 'missing'; }, /safe-default/],
] as const) {
  test(`guided selector rejects ${name}`, () => {
    const guided = enabled();
    change(guided);
    assert.match(validateGuidedPolicy(guided, members, 'chat')!, message);
  });
}

test('unsupported modes and removed classifiers fail locally before publication', () => {
  assert.match(validateGuidedPolicy(enabled(), members, 'embedding')!, /chat groups/);
  assert.match(validateGuidedPolicy(enabled(), members.slice(1), 'chat')!, /enabled chat/);
});
