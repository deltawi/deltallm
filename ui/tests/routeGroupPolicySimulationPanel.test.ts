import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import RouteGroupPolicySimulationPanel from '../src/components/route-groups/RouteGroupPolicySimulationPanel';

interface PendingFetch {
  input: RequestInfo | URL;
  init?: RequestInit;
  resolve: (response: Response) => void;
  reject: (error: Error) => void;
}

const MEMBERS = [
  {
    membership_id: 'member-1',
    route_group_id: 'group-1',
    deployment_id: 'dep-a',
    enabled: true,
    weight: 1,
    priority: 0,
  },
  {
    membership_id: 'member-2',
    route_group_id: 'group-1',
    deployment_id: 'dep-b',
    enabled: true,
    weight: 1,
    priority: 1,
  },
];

function simulationResponse(): Response {
  return new Response(JSON.stringify({
    group_key: 'support',
    iterations: 100,
    basis: 'live_state_dry_run',
    warnings: [],
    prompt: null,
    effective_metadata: {},
    summary: {
      selected_requests: 100,
      no_selection_requests: 0,
      served_requests: 100,
      failed_requests: 0,
      fallback_requests: 10,
      timed_out_requests: 0,
      total_attempts: 110,
    },
    reason_counts: { weighted: 100 },
    selections: [{ deployment_id: 'dep-a', count: 100, ratio: 1 }],
    served_deployments: [{ deployment_id: 'dep-a', count: 100, ratio: 1 }],
    terminal_outcomes: { success: 100 },
    sample_decision: null,
    sample_attempts: [{
      iteration: 1,
      attempt: 1,
      deployment_id: 'dep-a',
      outcome: 'success',
      transition: 'primary',
    }],
  }), { headers: { 'content-type': 'application/json' } });
}

test('policy simulation panel covers permission, loading, results, stale, error, and responsive states', async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>');
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  const previousActEnvironment = (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
  ).IS_REACT_ACT_ENVIRONMENT;
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });
  const requests: PendingFetch[] = [];
  globalThis.fetch = (async (input, init) => new Promise<Response>((resolve, reject) => {
    requests.push({ input, init, resolve, reject });
  })) as typeof fetch;
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const renderPanel = (canSimulate: boolean, policy: Record<string, unknown>) => (
    root.render(createElement(RouteGroupPolicySimulationPanel, {
      groupKey: 'support',
      policy,
      policyError: null,
      members: MEMBERS,
      canSimulate,
    }))
  );

  try {
    await act(async () => renderPanel(false, { mode: 'weighted' }));
    assert.match(document.body.textContent || '', /Simulation permission required/);

    await act(async () => renderPanel(true, { mode: 'weighted' }));
    assert.match(document.body.textContent || '', /No simulation results yet/);
    const runButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Run simulation'),
    );
    assert.ok(runButton);
    await act(async () => runButton.click());
    assert.match(document.body.textContent || '', /Simulating/);

    await act(async () => {
      requests[0].resolve(simulationResponse());
      await Promise.resolve();
    });
    assert.match(document.body.textContent || '', /Served by/);
    assert.match(document.body.textContent || '', /Sample attempt trace/);
    assert.match(document.body.innerHTML, /md:hidden/);
    assert.match(document.body.innerHTML, /hidden overflow-x-auto md:block/);

    const depBSelect = Array.from(document.querySelectorAll('select')).find(
      (select) => select.parentElement?.textContent?.includes('dep-b'),
    );
    assert.ok(depBSelect);
    await act(async () => {
      depBSelect.value = 'timeout';
      depBSelect.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });

    const explicitPolicy = {
      strategy: 'weighted',
      members: [{ deployment_id: 'dep-a' }],
    };
    await act(async () => renderPanel(true, explicitPolicy));
    assert.doesNotMatch(document.body.textContent || '', /dep-b/);

    await act(async () => renderPanel(true, { strategy: 'weighted' }));
    const restoredDepBSelect = Array.from(document.querySelectorAll('select')).find(
      (select) => select.parentElement?.textContent?.includes('dep-b'),
    );
    assert.ok(restoredDepBSelect);
    assert.equal(restoredDepBSelect.value, 'success');

    await act(async () => renderPanel(true, explicitPolicy));
    assert.match(document.body.textContent || '', /results are stale/);

    const rerunButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Run again'),
    );
    assert.ok(rerunButton);
    await act(async () => rerunButton.click());
    assert.equal(typeof requests[1].init?.body, 'string');
    const requestBody = JSON.parse(String(requests[1].init?.body));
    assert.deepEqual(requestBody.outcomes, []);
    assert.equal(requestBody.input_tokens, 0);
    assert.equal(requestBody.requested_output_tokens, null);
    await act(async () => {
      requests[1].reject(new Error('simulation unavailable'));
      await Promise.resolve();
    });
    assert.match(document.body.textContent || '', /simulation unavailable/);
    assert.match(document.body.textContent || '', /Served by/);
    await act(async () => root.unmount());
  } finally {
    globalThis.fetch = previousFetch;
    dom.window.close();
    Object.defineProperties(globalThis, {
      window: { configurable: true, value: previousWindow },
      document: { configurable: true, value: previousDocument },
      IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: previousActEnvironment },
    });
  }
});
