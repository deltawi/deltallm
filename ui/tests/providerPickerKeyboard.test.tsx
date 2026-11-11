import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import ProviderPicker from '../src/components/models/ProviderPicker';
import type { ProviderPreset } from '../src/lib/api';

function installDom() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'https://admin.example.test/' });
  // React watches input changes with attachEvent in jsdom.
  for (const name of ['attachEvent', 'detachEvent']) {
    Object.defineProperty(dom.window.HTMLElement.prototype, name, { configurable: true, value: () => undefined });
  }
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    Object.defineProperty(globalThis, key, { configurable: true, value });
  }
  return () => {
    dom.window.close();
    for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT']) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  };
}

const PRESETS: ProviderPreset[] = [
  { provider: 'openai', api_base: 'https://api.openai.com/v1', compat: 'openai', supported_modes: ['chat', 'embedding'] },
  { provider: 'anthropic', api_base: 'https://api.anthropic.com/v1', compat: 'anthropic', supported_modes: ['chat'] },
  { provider: 'vllm', api_base: null, compat: 'openai', supported_modes: ['chat', 'embedding'] },
  { provider: 'openai_compatible', api_base: null, compat: 'openai', supported_modes: ['chat', 'embedding'] },
];

function Harness({ mode, onPick }: { mode: string; onPick: (value: string) => void }) {
  const [value, setValue] = useState('');
  return <ProviderPicker presets={PRESETS} value={value} mode={mode} onChange={(next) => { setValue(next); onPick(next); }} />;
}

function key(target: Element, name: string) {
  target.dispatchEvent(new window.KeyboardEvent('keydown', { key: name, bubbles: true }));
}

function type(input: HTMLInputElement, value: string) {
  input.focus();
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  input.dispatchEvent(new window.Event('change', { bubbles: true }));
  // React detects the change on key up in jsdom.
  input.dispatchEvent(new window.KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
}

test('provider picker filters, moves with arrow keys, selects with Enter and returns focus', async () => {
  const restore = installDom();
  const root = createRoot(document.getElementById('root')!);
  const picked: string[] = [];
  try {
    await act(async () => { root.render(<Harness mode="chat" onPick={(value) => picked.push(value)} />); });
    const trigger = document.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]');
    assert.ok(trigger);
    act(() => { trigger.focus(); key(trigger, 'ArrowDown'); });
    const search = document.querySelector<HTMLInputElement>('input[role="combobox"]');
    assert.ok(search);
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');
    assert.equal(document.querySelectorAll('[role="option"]').length, 4);

    await act(async () => { type(search, 'llm'); });
    // "llm" matches vLLM only; the highlight marks the match.
    const options = Array.from(document.querySelectorAll('[role="option"]'));
    assert.deepEqual(options.map((node) => node.id.split('-').pop()), ['vllm']);
    assert.ok(options[0].querySelector('mark'));

    await act(async () => { type(search, ''); });
    act(() => { key(search, 'ArrowDown'); });
    assert.match(search.getAttribute('aria-activedescendant') || '', /anthropic$/);
    act(() => { key(search, 'Enter'); });
    assert.deepEqual(picked, ['anthropic']);
    assert.equal(document.querySelector('input[role="combobox"]'), null);
    assert.equal(document.activeElement, trigger);
    assert.ok(trigger.textContent?.includes('Anthropic'));
  } finally {
    await act(async () => { root.unmount(); });
    restore();
  }
});

test('provider picker hides providers without the model type and offers the generic provider when nothing matches', async () => {
  const restore = installDom();
  const root = createRoot(document.getElementById('root')!);
  const picked: string[] = [];
  try {
    await act(async () => { root.render(<Harness mode="embedding" onPick={(value) => picked.push(value)} />); });
    const trigger = document.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]');
    assert.ok(trigger);
    act(() => trigger.click());
    const ids = Array.from(document.querySelectorAll('[role="option"]')).map((node) => node.id.split('-').pop());
    assert.ok(!ids.includes('anthropic'));
    assert.match(document.body.textContent || '', /1 provider is not shown because it does not support Embeddings: Anthropic/);

    const search = document.querySelector<HTMLInputElement>('input[role="combobox"]');
    assert.ok(search);
    await act(async () => { type(search, 'cohere'); });
    const generic = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes('Use OpenAI-compatible'));
    assert.ok(generic);
    act(() => generic.click());
    assert.deepEqual(picked, ['openai_compatible']);

    act(() => trigger.click());
    const reopened = document.querySelector<HTMLInputElement>('input[role="combobox"]');
    assert.ok(reopened);
    act(() => key(reopened, 'Escape'));
    assert.equal(document.querySelector('input[role="combobox"]'), null);
  } finally {
    await act(async () => { root.unmount(); });
    restore();
  }
});
