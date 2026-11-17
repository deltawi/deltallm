import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import SpendChangeCell from '../src/components/usage/SpendChangeCell';

const PERIOD = { start_date: '2026-11-11', end_date: '2026-11-17', previous_start_date: '2026-11-04', previous_end_date: '2026-11-10' };

function setup() {
  const dom = new JSDOM('<table><tbody><tr id="row"><td id="root"></td></tr></tbody></table>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const globals = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value });
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

test('keyboard focus opens the spend card, Escape closes it, and keys do not reach the row', async () => {
  const env = setup();
  let rowKeys = 0;
  document.getElementById('row')!.addEventListener('keydown', () => { rowKeys += 1; });
  await act(async () => env.root.render(<SpendChangeCell current={1.36} previous={0.0063} period={PERIOD} />));
  const cell = document.querySelector<HTMLElement>('#root > span')!;
  assert.equal(cell.tabIndex, 0);
  assert.match(cell.textContent ?? '', />100×/);
  await act(async () => cell.focus());
  const tooltip = document.querySelector('[role="tooltip"]');
  assert.ok(tooltip);
  assert.match(tooltip.textContent ?? '', /\$0\.0063/);
  assert.match(tooltip.textContent ?? '', /\+\$1\.35/);
  assert.equal(cell.getAttribute('aria-describedby'), tooltip.id);
  await act(async () => cell.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  await act(async () => cell.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
  assert.equal(rowKeys, 0);
  assert.equal(document.querySelector('[role="tooltip"]'), null);
  await env.cleanup();
});

test('a negative previous total is shown as no comparison, not as no change', async () => {
  const env = setup();
  await act(async () => env.root.render(<SpendChangeCell current={5} previous={-10} period={PERIOD} />));
  const text = document.getElementById('root')!.textContent ?? '';
  assert.doesNotMatch(text, /0%/);
  assert.doesNotMatch(text, /Same spend/);
  assert.match(text, /negative spend total/);
  await env.cleanup();
});
