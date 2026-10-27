import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import ReportingRangeControl from '../src/components/ReportingRangeControl';
import { compactReportingRangeSelection, type ReportingRangeKey, type ReportingRangeSelectionKey } from '../src/lib/reportingRange';

test('compact range control keeps the applied custom value separate from its edit action', () => {
  assert.equal(compactReportingRangeSelection('30d', false), '30d');
  assert.equal(compactReportingRangeSelection('month', false), 'month');
  assert.equal(compactReportingRangeSelection('custom', false), 'custom-current');
  assert.equal(compactReportingRangeSelection('custom', true), 'custom-action');
});

async function withControl(
  props: { value: ReportingRangeSelectionKey; startDate?: string; endDate?: string; customLabel?: string },
  run: (ctx: { presets: ReportingRangeKey[]; applied: Array<[string, string]> }) => Promise<void>,
) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    Node: dom.window.Node,
    IS_REACT_ACT_ENVIRONMENT: true,
    requestAnimationFrame: (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0),
  };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const presets: ReportingRangeKey[] = [];
  const applied: Array<[string, string]> = [];
  const root = createRoot(document.getElementById('root')!);
  try {
    await act(async () => root.render(
      <ReportingRangeControl
        {...props}
        allowCustom
        ariaLabel="Usage period"
        onPresetChange={(key) => presets.push(key)}
        onCustomApply={(start, end) => applied.push([start, end])}
      />,
    ));
    await run({ presets, applied });
  } finally {
    await act(async () => root.unmount());
    dom.window.close();
    for (const key of Object.keys(values)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
}

const button = (name: string) => [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === name);

test('custom button names the active calendar preset and the menu offers calendar ranges', async () => {
  await withControl({ value: 'month', startDate: '2026-10-01', endDate: '2026-10-27' }, async ({ presets }) => {
    const opener = button('This month');
    assert.ok(opener, 'the custom segment shows the calendar preset');
    assert.equal(opener.getAttribute('aria-pressed'), 'true');
    const options = [...document.querySelectorAll('option')].map((option) => option.value);
    assert.ok(options.includes('today') && options.includes('last_month'));

    await act(async () => opener.click());
    assert.ok(document.querySelector('[role="dialog"]'));
    assert.equal(button('This month')?.getAttribute('aria-pressed'), 'true');
    const start = document.querySelector<HTMLInputElement>('input[type="date"]');
    assert.equal(start?.value, '2026-10-01');

    await act(async () => button('Last month')!.click());
    assert.deepEqual(presets, ['last_month']);
    assert.equal(document.querySelector('[role="dialog"]'), null);
  });
});

test('an applied custom range shows its label on the custom button', async () => {
  await withControl(
    { value: 'custom', startDate: '2026-09-01', endDate: '2026-09-15', customLabel: 'Sep 1 – Sep 15, 2026' },
    async () => {
      assert.ok(button('Sep 1 – Sep 15, 2026'));
      assert.equal(button('30 days')?.getAttribute('aria-pressed'), 'false');
    },
  );
});
