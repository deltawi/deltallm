import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import ModelForm from '../src/components/ModelForm';
import { ToastProvider } from '../src/components/ToastProvider';
import { EMPTY_FORM } from '../src/components/modelFormShared';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'content-type': 'application/json' },
});

function installDom() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: 'https://admin.example.test/models/new',
  });
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
  return () => {
    dom.window.close();
    for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT']) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  };
}

async function waitFor(check: () => boolean) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (check()) return;
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
  assert.fail('Timed out waiting for UI state.');
}

function changeValue(element: HTMLInputElement | HTMLSelectElement, value: string) {
  if (element instanceof window.HTMLInputElement) {
    element.focus();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    setter?.call(element, value);
    element.dispatchEvent(new window.Event('input', { bubbles: true }));
    element.dispatchEvent(new window.Event('change', { bubbles: true }));
    element.dispatchEvent(new window.KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
    return;
  }
  element.value = value;
  element.dispatchEvent(new window.Event('change', { bubbles: true }));
}

function selectWithOption(optionText: string): HTMLSelectElement | undefined {
  return Array.from(document.querySelectorAll('select')).find((select) => (
    Array.from(select.options).some((option) => option.textContent?.includes(optionText))
  ));
}

test('creator model creation derives a read-only API ID from the single Model Name field', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const originalFetch = globalThis.fetch;

  globalThis.fetch = async (input) => {
    const url = new URL(String(input), 'https://admin.example.test');
    if (url.pathname === '/ui/api/provider-presets') return json({ data: [] });
    if (url.pathname === '/ui/api/named-credentials') return json({ data: [] });
    throw new Error(`Unexpected request: ${url.pathname}`);
  };

  try {
    await act(async () => {
      root.render(
        <ToastProvider>
          <ModelForm
            initialValues={{
              ...EMPTY_FORM,
              api_namespace: 'ale-smi-k7m4q',
              credential_source: 'named',
            }}
            namespaceRequired
            allowInlineCredentials={false}
            onSubmit={async () => undefined}
            onCancel={() => undefined}
          />
        </ToastProvider>,
      );
    });

    const nameInput = document.querySelector<HTMLInputElement>('input[placeholder="Customer Support Model"]');
    assert.ok(nameInput);
    act(() => changeValue(nameInput, 'Customer Support'));

    const generatedId = document.querySelector<HTMLElement>('[aria-label="Generated API Model ID"]');
    assert.ok(generatedId);
    assert.equal(generatedId.textContent, 'ale-smi-k7m4q/customer-support');
    assert.equal(nameInput.value, 'Customer Support');
    assert.equal(document.querySelector('input[aria-label="Creator namespace"]'), null);
    assert.equal(document.querySelector('input[aria-label="API model slug"]'), null);
  } finally {
    await act(async () => { root.unmount(); });
    globalThis.fetch = originalFetch;
    restoreDom();
  }
});

test('model form creates and selects a provider-matched named credential in place', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const originalFetch = globalThis.fetch;
  let createBody: Record<string, unknown> | null = null;

  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), 'https://admin.example.test');
    if (url.pathname === '/ui/api/provider-presets') {
      return json({ data: [{ provider: 'openai', api_base: 'https://api.openai.com/v1', compat: 'openai', supported_modes: ['chat'] }] });
    }
    if (url.pathname === '/ui/api/named-credentials' && init?.method === 'POST') {
      createBody = JSON.parse(String(init.body));
      return json({
        credential_id: 'credential-new',
        name: 'Model credential',
        provider: 'openai',
        connection_config: { api_key: '***REDACTED***' },
        credentials_present: true,
        usage_count: 0,
        access: {
          managed_asset_id: 'asset-credential-new',
          asset_kind: 'named_credential',
          governance_source: 'creator',
          owner_account_id: 'account-1',
          visibility: 'team',
          subject_id: 'team-1',
          access_role: 'reader',
          grants: [{ subject_type: 'team', subject_id: 'team-1', access_role: 'reader' }],
          effective_role: 'owner',
          policy_version: 1,
          capabilities: {
            read: true,
            write: true,
            manage_access: true,
            delete: true,
            platform_admin_override: false,
          },
        },
      });
    }
    if (url.pathname === '/ui/api/named-credentials') return json({ data: [] });
    throw new Error(`Unexpected request: ${url.pathname}`);
  };

  try {
    await act(async () => {
      root.render(
        <ToastProvider>
          <ModelForm
            initialValues={{ ...EMPTY_FORM, credential_source: 'named' }}
            allowInlineCredentials={false}
            namedCredentialDefaultAccess={{
              grants: [{ subject_type: 'team', subject_id: 'team-1', access_role: 'reader' }],
            }}
            namedCredentialTeamOptions={[{ id: 'team-1', label: 'Engineering' }]}
            onSubmit={async () => undefined}
            onCancel={() => undefined}
          />
        </ToastProvider>,
      );
    });

    await waitFor(() => Boolean(selectWithOption('OpenAI')));
    const providerSelect = selectWithOption('OpenAI');
    assert.ok(providerSelect);
    await act(async () => {
      changeValue(providerSelect, 'openai');
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    await waitFor(() => Boolean(selectWithOption('Create new credential')));
    const credentialSelect = selectWithOption('Create new credential');
    assert.ok(credentialSelect);
    const createOption = Array.from(credentialSelect.options).find((option) => option.textContent?.includes('Create new credential'));
    assert.ok(createOption);
    act(() => changeValue(credentialSelect, createOption.value));

    await waitFor(() => Boolean(document.querySelector('[role="dialog"]')));
    const dialog = document.querySelector('[role="dialog"]');
    assert.ok(dialog);
    const dialogProvider = Array.from(dialog.querySelectorAll('select')).find((select) => (
      Array.from(select.options).some((option) => option.textContent?.includes('OpenAI'))
    ));
    assert.ok(dialogProvider);
    assert.equal(dialogProvider.value, 'openai');
    assert.equal(dialogProvider.disabled, true);

    const nameInput = dialog.querySelector<HTMLInputElement>('input[data-autofocus="true"]');
    const apiKeyInput = dialog.querySelector<HTMLInputElement>('input[type="password"]');
    assert.ok(nameInput);
    assert.ok(apiKeyInput);
    act(() => {
      changeValue(nameInput, 'Model credential');
      changeValue(apiKeyInput, 'sk-test');
    });

    const createButton = Array.from(dialog.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Create credential',
    );
    assert.ok(createButton);
    await act(async () => {
      createButton.click();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    await waitFor(() => !document.querySelector('[role="dialog"]'));
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
    assert.equal(credentialSelect.value, 'credential-new');
    assert.deepEqual(createBody, {
      name: 'Model credential',
      provider: 'openai',
      connection_config: { api_key: 'sk-test' },
      access: {
        grants: [{ subject_type: 'team', subject_id: 'team-1', access_role: 'reader' }],
        visibility: 'team',
        subject_id: 'team-1',
        access_role: 'reader',
      },
    });
  } finally {
    await act(async () => { root.unmount(); });
    globalThis.fetch = originalFetch;
    restoreDom();
  }
});

test('model form can start credential creation before a provider is selected', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const originalFetch = globalThis.fetch;

  globalThis.fetch = async (input) => {
    const url = new URL(String(input), 'https://admin.example.test');
    if (url.pathname === '/ui/api/provider-presets') {
      return json({ data: [{ provider: 'openai', api_base: 'https://api.openai.com/v1', compat: 'openai', supported_modes: ['chat'] }] });
    }
    if (url.pathname === '/ui/api/named-credentials') return json({ data: [] });
    throw new Error(`Unexpected request: ${url.pathname}`);
  };

  try {
    await act(async () => {
      root.render(
        <ToastProvider>
          <ModelForm
            initialValues={{ ...EMPTY_FORM, credential_source: 'named' }}
            allowInlineCredentials={false}
            onSubmit={async () => undefined}
            onCancel={() => undefined}
          />
        </ToastProvider>,
      );
    });

    await waitFor(() => Boolean(selectWithOption('Create new credential')));
    const credentialSelect = selectWithOption('Create new credential');
    assert.ok(credentialSelect);
    const createOption = Array.from(credentialSelect.options).find((option) => option.textContent?.includes('Create new credential'));
    assert.ok(createOption);
    act(() => changeValue(credentialSelect, createOption.value));

    await waitFor(() => Boolean(document.querySelector('[role="dialog"]')));
    const dialog = document.querySelector('[role="dialog"]');
    assert.ok(dialog);
    const dialogProvider = Array.from(dialog.querySelectorAll('select')).find((select) => (
      Array.from(select.options).some((option) => option.textContent?.includes('OpenAI'))
    ));
    assert.ok(dialogProvider);
    assert.equal(dialogProvider.value, '');
    assert.equal(dialogProvider.disabled, false);
  } finally {
    await act(async () => { root.unmount(); });
    globalThis.fetch = originalFetch;
    restoreDom();
  }
});
