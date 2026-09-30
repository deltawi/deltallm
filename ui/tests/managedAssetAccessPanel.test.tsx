import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import ManagedAssetAccessPanel from '../src/components/ManagedAssetAccessPanel';
import { ToastProvider } from '../src/components/ToastProvider';
import type { ManagedAssetAccess } from '../src/lib/api';

const privateOwnerAccess: ManagedAssetAccess = {
  managed_asset_id: 'asset-1',
  asset_kind: 'model',
  governance_source: 'creator',
  owner_account_id: 'account-1',
  visibility: 'private',
  subject_id: null,
  access_role: null,
  grants: [],
  effective_role: 'owner',
  policy_version: 2,
  capabilities: {
    read: true,
    write: true,
    manage_access: true,
    delete: true,
    platform_admin_override: false,
  },
};

function installDom() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: 'https://admin.example.test/models/model-1',
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

function selectValue(select: HTMLSelectElement, value: string) {
  select.value = value;
  select.dispatchEvent(new window.Event('change', { bubbles: true }));
}

test('shared access panel saves the same team and role workflow for every asset', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const originalFetch = globalThis.fetch;
  let requestBody: unknown = null;
  let savedCount = 0;
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), '/ui/api/assets/asset-1/access');
    assert.equal(init?.method, 'PUT');
    requestBody = JSON.parse(String(init?.body));
    return new Response(JSON.stringify({
      access: {
        ...privateOwnerAccess,
        visibility: 'team',
        subject_id: 'team-1',
        access_role: 'editor',
        grants: [{ subject_type: 'team', subject_id: 'team-1', access_role: 'editor' }],
        policy_version: 3,
      },
    }), { headers: { 'content-type': 'application/json' } });
  };

  try {
    await act(async () => {
      root.render(
        <ToastProvider>
          <ManagedAssetAccessPanel
            access={privateOwnerAccess}
            assetLabel="Model"
            teamOptions={[{ id: 'team-1', label: 'Engineering' }]}
            organizationOptions={[{ id: 'org-1', label: 'Acme' }]}
            allowPublic={false}
            onSaved={() => { savedCount += 1; }}
          />
        </ToastProvider>,
      );
    });

    const sharingButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Sharing & Access'),
    );
    assert.ok(sharingButton);
    act(() => sharingButton.click());

    const engineeringButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Engineering') && button.textContent?.includes('Add'),
    );
    assert.ok(engineeringButton);
    act(() => engineeringButton.click());

    const selects = Array.from(document.querySelectorAll('select'));
    assert.equal(selects.length, 1);
    act(() => selectValue(selects[0], 'editor'));

    const saveButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Save access',
    );
    assert.ok(saveButton);
    assert.equal(saveButton.disabled, false);
    await act(async () => { saveButton.click(); });

    assert.deepEqual(requestBody, {
      grants: [{ subject_type: 'team', subject_id: 'team-1', access_role: 'editor' }],
      visibility: 'team',
      subject_id: 'team-1',
      access_role: 'editor',
      expected_policy_version: 2,
    });
    assert.equal(savedCount, 1);
    assert.match(document.body.textContent || '', /Engineering/);
    assert.match(document.body.textContent || '', /Read \/ Write/);
    assert.equal(saveButton.disabled, true);
  } finally {
    await act(async () => { root.unmount(); });
    globalThis.fetch = originalFetch;
    restoreDom();
  }
});

test('shared access panel preserves post-commit refresh warnings', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    access: {
      ...privateOwnerAccess,
      grants: [{ subject_type: 'team', subject_id: 'team-1', access_role: 'reader' }],
      visibility: 'team',
      subject_id: 'team-1',
      access_role: 'reader',
      policy_version: 3,
    },
    warnings: ['Authorization refresh is delayed on another instance.'],
  }), { headers: { 'content-type': 'application/json' } });

  try {
    await act(async () => {
      root.render(
        <ToastProvider>
          <ManagedAssetAccessPanel
            access={privateOwnerAccess}
            assetLabel="Model"
            teamOptions={[{ id: 'team-1', label: 'Engineering' }]}
            organizationOptions={[]}
            allowPublic={false}
          />
        </ToastProvider>,
      );
    });
    const sharingButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Sharing & Access'),
    );
    assert.ok(sharingButton);
    act(() => sharingButton.click());
    const addButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Engineering') && button.textContent?.includes('Add'),
    );
    assert.ok(addButton);
    act(() => addButton.click());
    const saveButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Save access',
    );
    assert.ok(saveButton);
    await act(async () => { saveButton.click(); });

    assert.match(document.body.textContent || '', /Access saved with warning/);
    assert.match(document.body.textContent || '', /Authorization refresh is delayed/);
  } finally {
    await act(async () => { root.unmount(); });
    globalThis.fetch = originalFetch;
    restoreDom();
  }
});

test('shared access panel keeps Public visible but restricted for non-admin owners', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  try {
    await act(async () => {
      root.render(
        <ToastProvider>
          <ManagedAssetAccessPanel
            access={privateOwnerAccess}
            assetLabel="Prompt"
            teamOptions={[]}
            organizationOptions={[]}
            allowPublic={false}
          />
        </ToastProvider>,
      );
    });
    const sharingButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Sharing & Access'),
    );
    assert.ok(sharingButton);
    act(() => sharingButton.click());
    const publicTab = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Public',
    );
    assert.ok(publicTab);
    act(() => publicTab.click());
    const publicSwitch = document.querySelector<HTMLButtonElement>('button[role="switch"]');
    assert.ok(publicSwitch);
    assert.equal(publicSwitch.disabled, true);
    assert.match(document.body.textContent || '', /Only a platform admin can enable Public access/);
  } finally {
    await act(async () => { root.unmount(); });
    restoreDom();
  }
});

test('sharing tabs support roving keyboard focus and an associated panel', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  try {
    await act(async () => {
      root.render(
        <ToastProvider>
          <ManagedAssetAccessPanel
            access={privateOwnerAccess}
            assetLabel="Model"
            teamOptions={[]}
            organizationOptions={[]}
            allowPublic
          />
        </ToastProvider>,
      );
    });
    const sharingButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Sharing & Access'),
    );
    assert.ok(sharingButton);
    act(() => sharingButton.click());

    const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    assert.equal(tabs.length, 3);
    assert.equal(tabs[0].getAttribute('aria-selected'), 'true');
    assert.equal(tabs[0].tabIndex, 0);
    assert.equal(tabs[1].tabIndex, -1);

    tabs[0].focus();
    act(() => tabs[0].dispatchEvent(new window.KeyboardEvent('keydown', { bubbles: true, key: 'ArrowRight' })));
    assert.equal(document.activeElement, tabs[1]);
    assert.equal(tabs[1].getAttribute('aria-selected'), 'true');
    assert.equal(tabs[1].tabIndex, 0);
    assert.equal(tabs[0].tabIndex, -1);

    const controlledPanelId = tabs[1].getAttribute('aria-controls');
    assert.ok(controlledPanelId);
    const panel = document.getElementById(controlledPanelId);
    assert.ok(panel);
    assert.equal(panel.hidden, false);
    assert.equal(panel.getAttribute('aria-labelledby'), tabs[1].id);

    act(() => tabs[1].dispatchEvent(new window.KeyboardEvent('keydown', { bubbles: true, key: 'End' })));
    assert.equal(document.activeElement, tabs[2]);
    assert.equal(tabs[2].getAttribute('aria-selected'), 'true');
  } finally {
    await act(async () => { root.unmount(); });
    restoreDom();
  }
});

test('sharing audience search uses the bounded server endpoint and shows at most three results', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const originalFetch = globalThis.fetch;
  let requestUrl = '';
  globalThis.fetch = async (input) => {
    requestUrl = String(input);
    return new Response(JSON.stringify({
      data: [
        { id: 'team-1', label: 'Engineering One' },
        { id: 'team-2', label: 'Engineering Two' },
        { id: 'team-3', label: 'Engineering Three' },
        { id: 'team-4', label: 'Engineering Four' },
      ],
    }), { headers: { 'content-type': 'application/json' } });
  };
  try {
    await act(async () => {
      root.render(
        <ToastProvider>
          <ManagedAssetAccessPanel
            access={privateOwnerAccess}
            assetLabel="Model"
            teamOptions={[]}
            organizationOptions={[]}
            allowPublic={false}
          />
        </ToastProvider>,
      );
    });
    const sharingButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Sharing & Access'),
    );
    assert.ok(sharingButton);
    act(() => sharingButton.click());
    const search = document.querySelector<HTMLInputElement>('input[type="search"]');
    assert.ok(search);
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    act(() => {
      search.focus();
      setter?.call(search, 'engineering');
      search.dispatchEvent(new window.Event('input', { bubbles: true }));
      search.dispatchEvent(new window.Event('change', { bubbles: true }));
      search.dispatchEvent(new window.KeyboardEvent('keyup', { bubbles: true, key: 'g' }));
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    });
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });

    const url = new URL(requestUrl, 'https://admin.example.test');
    assert.equal(url.pathname, '/ui/api/assets/audience-options');
    assert.equal(url.searchParams.get('subject_type'), 'team');
    assert.equal(url.searchParams.get('search'), 'engineering');
    assert.equal(url.searchParams.get('limit'), '3');
    assert.match(document.body.textContent || '', /Engineering Three/);
    assert.doesNotMatch(document.body.textContent || '', /Engineering Four/);
  } finally {
    await act(async () => { root.unmount(); });
    globalThis.fetch = originalFetch;
    restoreDom();
  }
});
