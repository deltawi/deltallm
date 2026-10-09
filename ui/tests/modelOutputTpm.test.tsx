import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import ModelOutputTpmEditor from '../src/components/admin/ModelOutputTpmEditor';
import { modelOutputRows, modelOutputPayload, type ModelOutputRow } from '../src/lib/modelOutputTpm';
import { MutationGuard } from '../src/lib/mutationGuard';
import { emptyModelPolicyForm, modelPolicyFormToPayload } from '../src/lib/tiers';

test('model output limits preserve exact IDs, clear explicitly, and reject unsafe values', () => {
  assert.equal(modelOutputPayload([]), null);
  const limits = Object.fromEntries([['models/a:b', 10], ['__proto__', 20]]);
  assert.deepEqual(modelOutputPayload(modelOutputRows(limits)), limits);
  for (const rows of [
    [{ model: '*', limit: '10' }], [{ model: ' m', limit: '10' }], [{ model: 'é'.repeat(129), limit: '10' }],
    [{ model: 'm', limit: '0' }], [{ model: 'm', limit: '2147483648' }], [{ model: 'm', limit: '1.5' }],
    [{ model: 'm', limit: '' }], [{ model: 'm', limit: '1' }, { model: 'm', limit: '2' }],
    Array.from({ length: 65 }, (_, i) => ({ model: String(i), limit: '1' })),
    Array.from({ length: 64 }, (_, i) => ({ model: String(i).padEnd(256, '\\'), limit: '2147483647' })),
  ]) assert.throws(() => modelOutputPayload(rows));
  assert.equal(Object.keys(modelOutputPayload(Array.from({ length: 64 }, (_, i) => ({ model: String(i), limit: '1' })))!).length, 64);
});

test('tier output TPM uses the strict bound in its typed form', () => {
  const form = { ...emptyModelPolicyForm(), callable_key: 'm', output_tpm_limit: '10' };
  assert.equal(modelPolicyFormToPayload(form).output_tpm_limit, 10);
  assert.equal(modelPolicyFormToPayload({ ...form, output_tpm_limit: '' }).output_tpm_limit, null);
  assert.throws(() => modelPolicyFormToPayload({ ...form, output_tpm_limit: '2147483648' }));
});

test('mutation ownership rejects duplicate submission and stale entity/session results', () => {
  const guard = new MutationGuard();
  guard.reset('key-a:principal-a');
  const first = guard.begin('key-a:principal-a')!;
  assert.equal(guard.begin('key-a:principal-a'), null);
  guard.reset('key-b:principal-a');
  assert.equal(first.signal.aborted, true);
  assert.equal(first.current(), false);
  const second = guard.begin('key-b:principal-a')!;
  first.finish();
  assert.equal(guard.begin('key-b:principal-a'), null);
  guard.reset('key-b:principal-b');
  assert.equal(second.signal.aborted, true);
  const third = guard.begin('key-b:principal-b')!;
  third.finish();
  assert.equal(third.current(), false);
  assert.ok(guard.begin('key-b:principal-b'));
});

for (const width of [375, 1024]) {
  test(`model output editor labels, keyboard focus, add/remove, and disabled state at ${width}px`, async () => {
    const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
    const previous = Object.getOwnPropertyDescriptors(globalThis);
    Object.defineProperty(dom.window, 'innerWidth', { value: width });
    for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document,
      HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true })) {
      Object.defineProperty(globalThis, key, { configurable: true, value });
    }
    function Editor({ disabled = false }: { disabled?: boolean }) {
      const [rows, setRows] = useState<ModelOutputRow[]>([{ model: 'm', limit: '10' }]);
      return <ModelOutputTpmEditor rows={rows} onChange={setRows} disabled={disabled} />;
    }
    const root = createRoot(document.getElementById('root')!);
    try {
      await act(async () => root.render(<Editor />));
      const inputs = [...document.querySelectorAll<HTMLInputElement>('input')];
      assert.equal(inputs.length, 2);
      for (const input of inputs) {
        assert.ok([...document.querySelectorAll('label')].some((label) => label.htmlFor === input.id));
      }
      const add = [...document.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Add model limit')!;
      add.focus();
      assert.equal(document.activeElement, add);
      await act(async () => add.click());
      assert.equal(document.querySelectorAll('input').length, 4);
      await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Remove model limit 2"]')!.click());
      assert.equal(document.querySelectorAll('input').length, 2);
      await act(async () => root.render(<Editor disabled />));
      assert.equal(document.querySelector('fieldset')?.disabled, true);
      assert.equal(add.disabled, true);
    } finally {
      await act(async () => root.unmount());
      dom.window.close();
      for (const key of ['window', 'document', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT']) {
        if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
        else Reflect.deleteProperty(globalThis, key);
      }
    }
  });
}

test('key, team, and tier adapters preserve model limits and clears in typed payloads', async () => {
  const { keys, teams, tiers } = await import('../src/lib/api');
  const previous = globalThis.fetch;
  const controller = new AbortController();
  const writes: Array<{ body: unknown; signal?: AbortSignal | null }> = [];
  globalThis.fetch = async (_url, init) => {
    writes.push({ body: JSON.parse(String(init?.body)), signal: init?.signal });
    return new Response('{}', { headers: { 'content-type': 'application/json' } });
  };
  try {
    await keys.update('k', { model_output_tpm_limit: null }, controller.signal);
    await teams.update('t', { model_output_tpm_limit: { 'models/a': 10 } }, controller.signal);
    await tiers.updateModelPolicy('tier', 'version', 'policy', { expected_revision: 1, output_tpm_limit: null });
    assert.deepEqual(writes.map(({ body }) => body), [
      { model_output_tpm_limit: null }, { model_output_tpm_limit: { 'models/a': 10 } },
      { expected_revision: 1, output_tpm_limit: null },
    ]);
    assert.equal(writes[0].signal, controller.signal);
    assert.equal(writes[1].signal, controller.signal);
  } finally { globalThis.fetch = previous; }
});
