import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import StarRepoNudge from '../src/components/StarRepoNudge';
import { DEFAULT_BRANDING, type UIBranding } from '../src/lib/branding';
import { BrandingContext } from '../src/lib/brandingContext';
import { STAR_NUDGE_DISMISSED_KEY } from '../src/lib/starNudge';

const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
const globals = {
  window: dom.window,
  document: dom.window.document,
  HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node,
  StorageEvent: dom.window.StorageEvent,
  IS_REACT_ACT_ENVIRONMENT: true,
};
for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value });

async function render(branding: UIBranding = DEFAULT_BRANDING, copies = 1): Promise<Root> {
  const container = document.getElementById('root')!;
  const root = createRoot(container);
  await act(async () => root.render(
    <BrandingContext.Provider value={{ branding, assetRevision: 0, setBranding() {}, async refreshBranding() { return branding; } }}>
      {Array.from({ length: copies }, (_, index) => <StarRepoNudge key={index} />)}
    </BrandingContext.Provider>,
  ));
  return root;
}

const cards = () => [...document.querySelectorAll('p')].filter((node) => node.textContent === 'Enjoying DeltaLLM?');

test('white-labeled instances never show the star card', async () => {
  window.localStorage.clear();
  const root = await render({ ...DEFAULT_BRANDING, instance_name: 'Acme AI' });
  assert.equal(cards().length, 0);
  await act(async () => root.unmount());
  const logoRoot = await render({ ...DEFAULT_BRANDING, logo_full_url: '/assets/logo.svg' });
  assert.equal(cards().length, 0);
  await act(async () => logoRoot.unmount());
});

test('dismissing hides every copy and stays hidden after a reload', async () => {
  window.localStorage.clear();
  const root = await render(DEFAULT_BRANDING, 2);
  assert.equal(cards().length, 2);
  const link = document.querySelector('a');
  assert.equal(link?.getAttribute('href'), 'https://github.com/deltawi/deltallm');
  assert.equal(link?.getAttribute('target'), '_blank');

  const dismiss = document.querySelector<HTMLButtonElement>('button[aria-label="Dismiss star reminder"]')!;
  await act(async () => dismiss.click());
  assert.equal(cards().length, 0);
  assert.equal(window.localStorage.getItem(STAR_NUDGE_DISMISSED_KEY), '1');
  await act(async () => root.unmount());

  const again = await render();
  assert.equal(cards().length, 0);
  await act(async () => again.unmount());
});
