import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import { useRoutePolicySimulation } from '../src/lib/useRoutePolicySimulation';

interface PendingFetch {
  signal: AbortSignal;
  resolve: (response: Response) => void;
}

function response(iterations: number): Response {
  return new Response(JSON.stringify({
    group_key: 'support',
    iterations,
    basis: 'live_state_dry_run',
    warnings: [],
    prompt: null,
    effective_metadata: {},
    summary: {
      selected_requests: iterations,
      no_selection_requests: 0,
      served_requests: iterations,
      failed_requests: 0,
      fallback_requests: 0,
      timed_out_requests: 0,
      total_attempts: iterations,
    },
    reason_counts: {},
    selections: [],
    served_deployments: [],
    terminal_outcomes: { success: iterations },
    sample_decision: null,
    sample_attempts: [],
  }), { headers: { 'content-type': 'application/json' } });
}

test('policy simulation aborts changed scenarios and rejects stale completion', async () => {
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
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => (
    new Promise<Response>((resolve) => requests.push({
      signal: init?.signal as AbortSignal,
      resolve,
    }))
  )) as typeof fetch;

  function Probe({ fingerprint, iterations }: { fingerprint: string; iterations: number }) {
    const simulation = useRoutePolicySimulation('support', fingerprint);
    return createElement('div', null,
      createElement('button', {
        id: 'run',
        onClick: () => { void simulation.run({ iterations }); },
      }, 'run'),
      createElement('span', { id: 'value' }, simulation.data?.iterations ?? 'empty'),
      createElement('span', { id: 'loading' }, String(simulation.loading)),
      createElement('span', { id: 'stale' }, String(simulation.stale)),
    );
  }

  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);

  try {
    await act(async () => root.render(createElement(Probe, { fingerprint: 'old', iterations: 1 })));
    await act(async () => document.getElementById('run')?.click());
    assert.equal(document.getElementById('loading')?.textContent, 'true');
    assert.equal(requests.length, 1);

    await act(async () => root.render(createElement(Probe, { fingerprint: 'new', iterations: 2 })));
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(document.getElementById('loading')?.textContent, 'false');
    await act(async () => document.getElementById('run')?.click());
    assert.equal(requests.length, 2);

    await act(async () => {
      requests[1].resolve(response(2));
      await Promise.resolve();
    });
    assert.equal(document.getElementById('value')?.textContent, '2');

    await act(async () => {
      requests[0].resolve(response(1));
      await Promise.resolve();
    });
    assert.equal(document.getElementById('value')?.textContent, '2');

    await act(async () => root.render(createElement(Probe, { fingerprint: 'changed', iterations: 3 })));
    assert.equal(document.getElementById('stale')?.textContent, 'true');
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
