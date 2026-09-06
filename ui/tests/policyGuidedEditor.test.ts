import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement, useState } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';

import PolicyGuidedEditor from '../src/components/PolicyGuidedEditor';
import { GUIDED_POLICY_DEFAULTS, type PolicyGuidedValues } from '../src/lib/routeGroups';

function Harness({ workloadMode = 'chat' }: { workloadMode?: string }) {
  const [values, setValues] = useState<PolicyGuidedValues>(GUIDED_POLICY_DEFAULTS);
  return createElement(PolicyGuidedEditor, {
    values,
    onChange: setValues,
    strategyOptions: ['weighted'],
    memberOptions: [],
    workloadMode,
  });
}

test('guided context controls follow workload support and remain keyboard-focusable', async () => {
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
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);

  try {
    await act(async () => root.render(createElement(Harness)));
    assert.match(document.body.textContent || '', /Context capacity/);
    const contextSelect = Array.from(document.querySelectorAll('select')).find(
      (select) => select.parentElement?.textContent?.includes('Routing behavior'),
    );
    const tokenInputs = Array.from(document.querySelectorAll('input[type="number"]')).filter(
      (input) => input.parentElement?.textContent?.includes('tokens'),
    ) as HTMLInputElement[];
    assert.ok(contextSelect);
    assert.equal(contextSelect.value, 'disabled');
    assert.equal(contextSelect.tabIndex, 0);
    assert.equal(tokenInputs.length, 2);
    assert.ok(tokenInputs.every((input) => input.disabled));

    await act(async () => {
      contextSelect.value = 'smallest-sufficient';
      contextSelect.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });

    assert.ok(tokenInputs.every((input) => !input.disabled));
    assert.ok(tokenInputs.every((input) => input.min === '0' && input.step === '1'));

    await act(async () => root.unmount());
  } finally {
    dom.window.close();
    Object.defineProperties(globalThis, {
      window: { configurable: true, value: previousWindow },
      document: { configurable: true, value: previousDocument },
      IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: previousActEnvironment },
    });
  }
});

test('guided context controls are unavailable for unsupported workload modes', () => {
  const html = renderToStaticMarkup(createElement(PolicyGuidedEditor, {
    values: GUIDED_POLICY_DEFAULTS,
    onChange: () => undefined,
    strategyOptions: ['weighted'],
    memberOptions: [],
    workloadMode: 'rerank',
  }));

  assert.match(html, /Context capacity unavailable/);
  assert.match(html, /This group uses rerank/);
  assert.doesNotMatch(html, /smallest-sufficient/);
});
