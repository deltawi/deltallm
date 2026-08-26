import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import ThemeSettingsPanel from '../src/components/settings/ThemeSettingsPanel';
import { DEFAULT_BRANDING, type UIBranding } from '../src/lib/branding';
import {
  BrandingContext,
  type BrandingContextValue,
} from '../src/lib/brandingContext';
import type { ThemeMutation } from '../src/lib/settingsTheme';

function installDom() {
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="root"></div></body></html>', {
    url: 'https://admin.example.test/ui/settings',
  });
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    navigator: globalThis.navigator,
    HTMLElement: globalThis.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: (globalThis as typeof globalThis & {
      IS_REACT_ACT_ENVIRONMENT?: boolean;
    }).IS_REACT_ACT_ENVIRONMENT,
  };

  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    navigator: { configurable: true, value: dom.window.navigator },
    HTMLElement: { configurable: true, value: dom.window.HTMLElement },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });

  return () => {
    dom.window.close();
    Object.defineProperties(globalThis, {
      window: { configurable: true, value: previous.window },
      document: { configurable: true, value: previous.document },
      navigator: { configurable: true, value: previous.navigator },
      HTMLElement: { configurable: true, value: previous.HTMLElement },
      IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: previous.IS_REACT_ACT_ENVIRONMENT },
    });
  };
}

function findButton(label: string): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button')).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  assert.ok(button, `Expected button: ${label}`);
  return button;
}

const customizedBranding: UIBranding = {
  ...DEFAULT_BRANDING,
  instance_name: 'Acme AI',
  logo_mark_url: '/branding/mark.svg',
};

const brandingContext: BrandingContextValue = {
  branding: customizedBranding,
  assetRevision: 0,
  refreshBranding: async () => customizedBranding,
  setBranding: () => undefined,
};

test('theme reset confirmation stays distinct from discard and invokes reset once', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  let resetCount = 0;
  let discardCount = 0;

  function Harness() {
    const [resetOpen, setResetOpen] = useState(false);
    return (
      <BrandingContext.Provider value={brandingContext}>
        <ThemeSettingsPanel
          value={customizedBranding}
          error={null}
          dirty
          resetDisabled={false}
          resetConfirmationOpen={resetOpen}
          mutation={null}
          onChange={() => undefined}
          onUpload={async () => undefined}
          onRemove={async () => undefined}
          onDiscard={() => { discardCount += 1; }}
          onOpenReset={() => setResetOpen(true)}
          onCloseReset={() => setResetOpen(false)}
          onConfirmReset={() => {
            resetCount += 1;
            setResetOpen(false);
          }}
        />
      </BrandingContext.Provider>
    );
  }

  try {
    await act(async () => { root.render(<Harness />); });

    act(() => findButton('Discard changes').click());
    assert.equal(discardCount, 1);
    assert.equal(resetCount, 0);

    act(() => findButton('Reset to DeltaLLM defaults').click());
    assert.match(document.querySelector('[role="dialog"]')?.textContent || '', /permanently deleted/);
    assert.equal(resetCount, 0);

    act(() => document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' })));
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(resetCount, 0);

    act(() => findButton('Reset to DeltaLLM defaults').click());
    act(() => findButton('Reset theme').click());
    assert.equal(resetCount, 1);
    assert.equal(document.querySelector('[role="dialog"]'), null);
  } finally {
    await act(async () => { root.unmount(); });
    restoreDom();
  }
});

test('theme reset locks the dialog and controls while the mutation is pending', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  let closeCount = 0;

  function renderPanel(mutation: ThemeMutation, resetDisabled = false) {
    return (
      <BrandingContext.Provider value={brandingContext}>
        <ThemeSettingsPanel
          value={resetDisabled ? DEFAULT_BRANDING : customizedBranding}
          error={null}
          dirty={!resetDisabled}
          resetDisabled={resetDisabled}
          resetConfirmationOpen={!resetDisabled}
          mutation={mutation}
          onChange={() => undefined}
          onUpload={async () => undefined}
          onRemove={async () => undefined}
          onDiscard={() => undefined}
          onOpenReset={() => undefined}
          onCloseReset={() => { closeCount += 1; }}
          onConfirmReset={() => undefined}
        />
      </BrandingContext.Provider>
    );
  }

  try {
    await act(async () => { root.render(renderPanel('reset')); });

    assert.equal(findButton('Working...').disabled, true);
    assert.equal(findButton('Cancel').disabled, true);
    assert.equal(document.querySelector<HTMLInputElement>('#theme-instance-name')?.disabled, true);
    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Close dialog"]')?.click());
    assert.equal(closeCount, 0);

    await act(async () => { root.render(renderPanel(null, true)); });
    assert.equal(findButton('Reset to DeltaLLM defaults').disabled, true);
    assert.equal(findButton('Discard changes').disabled, true);
  } finally {
    await act(async () => { root.unmount(); });
    restoreDom();
  }
});
