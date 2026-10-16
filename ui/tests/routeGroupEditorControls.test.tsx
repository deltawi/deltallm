import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';

import ConfirmDialog from '../src/components/ConfirmDialog';
import SideDrawer from '../src/components/SideDrawer';
import { DeploymentsCard } from '../src/components/route-groups/AdvancedRoutingCards';
import { ContextCard } from '../src/components/route-groups/AdvancedRoutingOptions';
import SimpleRoutingChoices from '../src/components/route-groups/SimpleRoutingChoices';
import type { SimpleRouting } from '../src/lib/routeGroupRouting';
import type { RouteGroupMemberDetail } from '../src/lib/api';
import {
  buildPolicyFromGuided,
  GUIDED_POLICY_DEFAULTS,
  moveGuidedPolicyMember,
  orderedGuidedMemberIds,
  type PolicyGuidedValues,
} from '../src/lib/routeGroups';

function setup() {
  const dom = new JSDOM('<button id="opener">Open</button><div id="root"></div>', { url: 'http://localhost' });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return this.parentNode; } });
  // Frames run when a test flushes them, after React has committed, like a browser paint.
  const frames: FrameRequestCallback[] = [];
  dom.window.requestAnimationFrame = (callback) => frames.push(callback);
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = {
    window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator,
    requestAnimationFrame: dom.window.requestAnimationFrame, IS_REACT_ACT_ENVIRONMENT: true,
  };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const root = createRoot(document.getElementById('root')!);
  return {
    dom,
    root,
    flushFrames: () => act(async () => { frames.splice(0).forEach((callback) => callback(0)); }),
    async close() {
      await act(async () => root.unmount());
      dom.window.close();
      for (const key of Object.keys(values)) {
        if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
        else Reflect.deleteProperty(globalThis, key);
      }
    },
  };
}

const member = (id: string, name: string, weight: number, priority: number, enabled = true): RouteGroupMemberDetail => ({
  membership_id: `m-${id}`, route_group_id: 'g1', deployment_id: id, enabled, weight, priority, model_name: name, provider: 'openai', healthy: true,
});
const MEMBERS = [member('late', 'Late', 2, 5), member('first', 'First', 8, 0), member('paused', 'Paused', 1, 1, false)];
const key = (env: ReturnType<typeof setup>, name: string, shiftKey = false) => act(async () => {
  document.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: name, shiftKey, bubbles: true }));
});

test('order controls reorder deployments, serialize the same order, and keep focus in the row', async () => {
  const env = setup();
  let current: PolicyGuidedValues = { ...GUIDED_POLICY_DEFAULTS, strategy: 'priority-based-routing', memberIds: ['late', 'first'] };
  function Harness() {
    const [values, setValues] = useState(current);
    return (
      <DeploymentsCard values={values} disabled={false} changed={false} onChange={(next) => { current = next; setValues(next); }}
        members={MEMBERS} draftShares={null} liveShares={null} onManageMembers={() => {}} />
    );
  }
  try {
    await act(async () => env.root.render(<Harness />));
    assert.deepEqual(orderedGuidedMemberIds(current, MEMBERS), ['first', 'late']);
    assert.equal(document.querySelector<HTMLButtonElement>('[aria-label="Move First up"]')?.disabled, true);
    const up = document.querySelector<HTMLButtonElement>('[aria-label="Move Late up"]')!;
    up.focus();
    await act(async () => up.click());
    await env.flushFrames();
    assert.equal(current.memberSelection, 'explicit');
    assert.deepEqual(current.memberIds, ['late', 'first']);
    assert.deepEqual(buildPolicyFromGuided({ server_hint: 'preserved' }, current).members, [
      { deployment_id: 'late', enabled: true, priority: 0 }, { deployment_id: 'first', enabled: true, priority: 1 },
    ]);
    // Late is first now, so its up control is disabled and focus moves to its down control.
    assert.equal(up.disabled, true);
    assert.equal(document.activeElement, document.querySelector('[aria-label="Move Late down"]'));
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Move Late down"]')!.click());
    await env.flushFrames();
    assert.deepEqual(current.memberIds, ['first', 'late']);
    assert.equal(document.activeElement, document.querySelector('[aria-label="Move Late up"]'), 'focus stays with the moved model');
    assert.equal(document.querySelector<HTMLInputElement>('[aria-label="Include Paused"]')?.disabled, true);
    assert.equal(moveGuidedPolicyMember(current, MEMBERS, 'first', -1), current);
    assert.equal(moveGuidedPolicyMember(current, MEMBERS, 'missing', 1), current);
  } finally { await env.close(); }
});

test('context-aware routing controls follow workload support and stay keyboard-focusable', async () => {
  const env = setup();
  function Harness({ workloadMode }: { workloadMode: string }) {
    const [values, setValues] = useState<PolicyGuidedValues>(GUIDED_POLICY_DEFAULTS);
    return <ContextCard values={values} workloadMode={workloadMode} disabled={false} changed={false} onChange={setValues} />;
  }
  try {
    await act(async () => env.root.render(<Harness workloadMode="chat" />));
    assert.equal(document.querySelector('select'), null, 'settings stay hidden while the feature is off');
    const toggle = document.querySelector<HTMLButtonElement>('[aria-label="Context-aware routing"]')!;
    assert.equal(toggle.getAttribute('aria-checked'), 'false');
    await act(async () => toggle.click());
    const selects = [...document.querySelectorAll('select')];
    assert.equal(selects.length, 2);
    assert.equal(selects[0].value, 'eligible-only');
    assert.ok(selects.every((select) => select.tabIndex === 0 && !select.disabled));
    const numbers = [...document.querySelectorAll<HTMLInputElement>('input[type="number"]')];
    assert.equal(numbers.length, 2);
    assert.ok(numbers.every((input) => input.min === '0' && input.step === '1' && !input.disabled));
  } finally { await env.close(); }

  const unsupported = renderToStaticMarkup(<ContextCard values={GUIDED_POLICY_DEFAULTS} workloadMode="rerank" disabled={false} changed={false} onChange={() => {}} />);
  assert.match(unsupported, /This group uses rerank/);
  assert.doesNotMatch(unsupported, /aria-label="Context-aware routing"/);
  const saved = renderToStaticMarkup(<ContextCard values={{ ...GUIDED_POLICY_DEFAULTS, contextMode: 'smallest-sufficient' }} workloadMode="rerank" disabled={false} changed={false} onChange={() => {}} />);
  assert.match(saved, /cannot be published for this workload/);
  assert.match(saved, />Turn it off</);
});

test('a confirmation over a side drawer owns Escape and Tab, then the drawer gets them back', async () => {
  const env = setup();
  let drawerClosed = 0;
  function Harness() {
    const [confirming, setConfirming] = useState(false);
    return (
      <SideDrawer title="Review" description="Check the change." saving={false} onClose={() => { drawerClosed += 1; }}
        footer={<button type="button" onClick={() => setConfirming(true)}>Discard</button>}>
        <input aria-label="Note" />
        <ConfirmDialog open={confirming} title="Discard the draft?" description="The draft is deleted." confirmLabel="Discard draft" destructive
          onClose={() => setConfirming(false)} onConfirm={() => setConfirming(false)} />
      </SideDrawer>
    );
  }
  try {
    const opener = document.getElementById('opener') as HTMLButtonElement;
    opener.focus();
    await act(async () => env.root.render(<Harness />));
    await env.flushFrames();
    const drawer = document.querySelector<HTMLElement>('[role="dialog"]')!;
    assert.ok(drawer.contains(document.activeElement), 'focus moves into the drawer');
    assert.equal(document.body.style.overflow, 'hidden', 'the page behind the drawer does not scroll');
    const discard = [...drawer.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === 'Discard')!;
    discard.focus();
    await act(async () => discard.click());
    const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]');
    assert.equal(dialogs.length, 2);
    const confirm = dialogs[1];
    assert.ok(confirm.contains(document.activeElement), 'focus moves into the confirmation');

    const inConfirm = [...confirm.querySelectorAll<HTMLButtonElement>('button')].filter((item) => !item.disabled);
    inConfirm.at(-1)!.focus();
    await key(env, 'Tab');
    assert.ok(confirm.contains(document.activeElement), 'Tab wraps inside the confirmation, not the drawer');

    await key(env, 'Escape');
    assert.equal(drawerClosed, 0, 'Escape closes only the confirmation');
    assert.equal(document.body.style.overflow, 'hidden', 'closing the confirmation keeps the drawer scroll lock');
    assert.equal(document.querySelectorAll('[role="dialog"]').length, 1);
    assert.equal(document.activeElement, discard, 'focus returns to the control that opened the confirmation');

    const focusable = [...drawer.querySelectorAll<HTMLElement>('button, input')].filter((item) => !item.hasAttribute('disabled'));
    focusable.at(-1)!.focus();
    await key(env, 'Tab');
    assert.equal(document.activeElement, focusable[0], 'Tab wraps inside the drawer again');
    await key(env, 'Escape');
    assert.equal(drawerClosed, 1);
    await act(async () => env.root.render(null));
    assert.equal(document.body.style.overflow, '');
  } finally { await env.close(); }
});

test('backup rows keep focus when they move between the order and the unused list', async () => {
  const env = setup();
  const members = MEMBERS.map((item) => ({ ...item, enabled: true }));
  function Harness() {
    const [routing, setRouting] = useState<SimpleRouting>({ choice: 'backup', order: ['first', 'late', 'paused'], percents: {}, retry: 'on' });
    return <SimpleRoutingChoices routing={routing} members={members} disabled={false} onChange={setRouting} />;
  }
  try {
    await act(async () => env.root.render(<Harness />));
    const stop = document.querySelector<HTMLButtonElement>('[aria-label="Stop using Late"]')!;
    stop.focus();
    await act(async () => stop.click());
    await env.flushFrames();
    assert.equal(document.activeElement?.textContent, 'Add as backup', 'focus moves to the control that brings the model back');
    assert.equal(document.activeElement?.closest('[data-row-id]')?.getAttribute('data-row-id'), 'late');
    await act(async () => (document.activeElement as HTMLButtonElement).click());
    await env.flushFrames();
    assert.equal(document.activeElement?.getAttribute('aria-label'), 'Move Late up', 'focus stays on the model after it is added back');
  } finally { await env.close(); }
});
