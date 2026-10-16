import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { KeyRound } from 'lucide-react';
import { IconTabs } from '../src/components/admin/shells';
import ApiKeyEditorDialog from '../src/components/api-keys/ApiKeyEditorDialog';
import ApiKeySecretDialog from '../src/components/api-keys/ApiKeySecretDialog';
import { emptyKeyForm } from '../src/lib/apiKeyForm';

function setup() {
  const dom = new JSDOM('<button id="trigger">Open editor</button><div id="root"></div>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const globals = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return this.parentNode; } });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'attachEvent', { value: () => {} });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'detachEvent', { value: () => {} });
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
const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === text)!;
test('key dialog traps focus, supports keyboard tabs, keeps values, locks close while saving, and restores focus', async () => {
  const env = setup();
  const trigger = document.getElementById('trigger')!;
  trigger.focus();
  let closes = 0;
  let submitted = 0;
  const props = {
    title: 'Create API Key', form: { ...emptyKeyForm(), key_name: 'Support', team_id: 'team-1', rph_limit: '123' },
    onChange: () => {}, onTeamChange: () => {}, teams: [{ team_id: 'team-1', team_alias: 'Platform' }],
    selfService: false, editing: false, policy: null, serviceAccounts: [], serviceAccountsLoading: false,
    newServiceAccountName: '', onNewServiceAccountNameChange: () => {}, creatingServiceAccount: false,
    onCreateServiceAccount: () => {}, saving: false, saveDisabled: false, error: null,
    onClose: () => { closes += 1; }, onSave: () => { submitted += 1; },
    assetAccess: { title: 'Key access', mode: 'inherit' as const, targets: [], selectedKeys: [], onSelectedKeysChange: () => {} },
  };
  try {
    await act(async () => env.root.render(<ApiKeyEditorDialog {...props} />));
    assert.equal((document.activeElement as HTMLInputElement).value, 'Support');
    const details = document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')!;
    details.focus();
    await act(async () => details.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'End', bubbles: true })));
    assert.equal(document.activeElement?.textContent, 'Limits');
    assert.equal(document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.textContent, 'Limits');
    assert.equal(document.querySelector<HTMLInputElement>('input[value="123"]')?.value, '123');
    assert.ok(document.querySelector('input[type="datetime-local"]'));
    assert.equal(document.querySelector('[role="tabpanel"]:not([hidden])')?.getAttribute('aria-labelledby'), document.activeElement?.id);
    const save = button('Create key');
    save.focus();
    await act(async () => save.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })));
    assert.equal(document.activeElement?.getAttribute('aria-label'), 'Close dialog');
    await act(async () => button('Create key').click());
    assert.equal(submitted, 1);
    await act(async () => env.root.render(<ApiKeyEditorDialog {...props} saving />));
    await act(async () => document.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(closes, 0);
    assert.equal(button('Cancel').disabled, true);
    await act(async () => env.root.render(<ApiKeyEditorDialog {...props} />));
    await act(async () => document.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(closes, 1);
    await act(async () => env.root.render(null));
    assert.equal(document.activeElement, trigger);
    assert.equal(document.body.style.overflow, '');
  } finally { await env.cleanup(); }
});

test('copy confirms only after clipboard success and reports clipboard failure', async () => {
  const env = setup();
  const secret = 'test-only-secret';
  let fail = true;
  let copied = '';
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { if (fail) throw new Error('Unavailable'); copied = text; } } });
  try {
    await act(async () => env.root.render(<ApiKeySecretDialog secret={secret} onClose={() => {}} />));
    await act(async () => button('Copy key').click());
    assert.match(document.querySelector('[role="alert"]')?.textContent || '', /Could not copy/);
    assert.equal(copied, '');
    fail = false;
    await act(async () => button('Copy key').click());
    assert.equal(copied, secret);
    assert.match(document.querySelector('[role="status"]')?.textContent || '', /copied/);
    assert.equal(document.querySelector('[role="alert"]'), null);
    assert.equal(window.localStorage.length, 0);
    assert.equal(window.sessionStorage.length, 0);
    await act(async () => env.root.render(null));
    assert.ok(!document.body.textContent?.includes(secret));
  } finally { await env.cleanup(); }
});

for (const variant of ['line', 'card', 'segment'] as const) {
  test(`${variant} tabs support arrow keys, wrapping, Home, End, and panel references`, async () => {
    const env = setup();
    const items = ['one', 'two', 'three'].map((id) => ({ id, label: id, icon: KeyRound }));
    function Harness() {
      const [active, setActive] = useState('one');
      return <><IconTabs id="tabs" label="Example" items={items} active={active} onChange={setActive} variant={variant} />
        {items.map(({ id }) => <div key={id} id={`tabs-panel-${id}`} role="tabpanel" aria-labelledby={`tabs-tab-${id}`} hidden={active !== id}>{id}</div>)}
      </>;
    }
    try {
      await act(async () => env.root.render(<Harness />));
      document.getElementById('tabs-tab-one')!.focus();
      for (const [key, expected] of [['ArrowLeft', 'three'], ['ArrowRight', 'one'], ['End', 'three'], ['Home', 'one'], ['ArrowRight', 'two']]) {
        await act(async () => document.activeElement!.dispatchEvent(new env.dom.window.KeyboardEvent('keydown', { key, bubbles: true })));
        assert.equal(document.activeElement?.id, `tabs-tab-${expected}`);
        assert.equal(document.activeElement?.getAttribute('aria-selected'), 'true');
        assert.equal(document.querySelectorAll('[role="tab"][tabindex="0"]').length, 1);
        const panel = document.getElementById(document.activeElement!.getAttribute('aria-controls')!)!;
        assert.ok(panel);
        assert.equal(panel.hidden, false);
      }
    } finally { await env.cleanup(); }
  });
}
