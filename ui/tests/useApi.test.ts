import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import { useApi } from '../src/lib/hooks';

interface PendingRequest {
  signal: AbortSignal;
  resolve: (value: string) => void;
}

test('useApi aborts replaced reads and rejects stale completion', async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>');
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const previousActEnvironment = (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
  ).IS_REACT_ACT_ENVIRONMENT;
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });

  const requests: PendingRequest[] = [];
  function Probe({ queryKey }: { queryKey: string }) {
    const result = useApi(
      (signal) => new Promise<string>((resolve) => requests.push({ signal, resolve })),
      [queryKey],
    );
    return createElement('div', { id: 'value' }, result.data ?? 'empty');
  }

  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);

  try {
    await act(async () => {
      root.render(createElement(Probe, { queryKey: 'old' }));
    });
    assert.equal(requests.length, 1);

    await act(async () => {
      root.render(createElement(Probe, { queryKey: 'new' }));
    });
    assert.equal(requests.length, 2);
    assert.equal(requests[0].signal.aborted, true);

    await act(async () => {
      requests[1].resolve('new-value');
      await Promise.resolve();
    });
    assert.equal(document.getElementById('value')?.textContent, 'new-value');

    await act(async () => {
      requests[0].resolve('stale-value');
      await Promise.resolve();
    });
    assert.equal(document.getElementById('value')?.textContent, 'new-value');

    await act(async () => root.unmount());
    assert.equal(requests[1].signal.aborted, true);
  } finally {
    dom.window.close();
    Object.defineProperties(globalThis, {
      window: { configurable: true, value: previousWindow },
      document: { configurable: true, value: previousDocument },
      IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: previousActEnvironment },
    });
  }
});
