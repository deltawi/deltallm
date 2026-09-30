import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import NamedCredentialForm from '../src/components/NamedCredentialForm';
import type { NamedCredential } from '../src/lib/api';
import { matchingNamedCredentialDetail } from '../src/lib/namedCredentialEditing';

function credential(credentialId: string, name: string): NamedCredential {
  return {
    credential_id: credentialId,
    name,
    provider: 'openai',
    connection_config: { api_base: 'https://api.example.test/v1' },
    credentials_present: true,
  };
}

test('credential detail is never reused for another selected credential', () => {
  const first = credential('credential-a', 'Credential A');
  const second = credential('credential-b', 'Credential B');

  assert.equal(matchingNamedCredentialDetail(second, first), null);
  assert.equal(matchingNamedCredentialDetail(second, second), second);
  assert.equal(matchingNamedCredentialDetail(null, second), null);
});

test('same-credential detail refresh does not reset unsaved settings', async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>');
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', {
    configurable: true,
    value: () => undefined,
  });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', {
    configurable: true,
    value: () => undefined,
  });
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
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const render = (initialCredential: NamedCredential) => (
    <NamedCredentialForm
      initialCredential={initialCredential}
      providerPresets={[{
        provider: 'openai',
        api_base: 'https://api.openai.com/v1',
        compat: 'openai',
        supported_modes: ['chat'],
      }]}
      onSave={async () => undefined}
      onCancel={() => undefined}
    />
  );

  try {
    await act(async () => { root.render(render(credential('credential-a', 'Original'))); });
    const nameInput = document.querySelector<HTMLInputElement>('input[data-autofocus="true"]');
    assert.ok(nameInput);
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    )?.set;
    act(() => {
      nameInput.focus();
      setter?.call(nameInput, 'Unsaved local name');
      nameInput.dispatchEvent(new window.Event('input', { bubbles: true }));
      nameInput.dispatchEvent(new window.Event('change', { bubbles: true }));
      nameInput.dispatchEvent(new window.KeyboardEvent('keyup', { bubbles: true, key: 'e' }));
    });
    assert.equal(nameInput.value, 'Unsaved local name');

    await act(async () => {
      root.render(render(credential('credential-a', 'Refetched server name')));
    });
    assert.equal(nameInput.value, 'Unsaved local name');
  } finally {
    await act(async () => { root.unmount(); });
    dom.window.close();
    for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT']) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
});
