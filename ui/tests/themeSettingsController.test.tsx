import assert from 'node:assert/strict';
import test from 'node:test';
import { act, StrictMode, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import {
  useThemeSettingsController,
  type ThemeSettingsApi,
} from '../src/components/settings/useThemeSettingsController';
import { DEFAULT_BRANDING, type UIBranding } from '../src/lib/branding';

type Controller = ReturnType<typeof useThemeSettingsController>;
type Toast = {
  tone: 'success' | 'error' | 'info';
  title: string;
  message: string;
};

const CUSTOM_BRANDING: UIBranding = {
  ...DEFAULT_BRANDING,
  instance_name: 'Acme AI',
  primary_color: '#112233',
};

function installDom() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: 'https://admin.example.test/ui/settings',
  });
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    navigator: globalThis.navigator,
    File: globalThis.File,
    IS_REACT_ACT_ENVIRONMENT: (globalThis as typeof globalThis & {
      IS_REACT_ACT_ENVIRONMENT?: boolean;
    }).IS_REACT_ACT_ENVIRONMENT,
  };
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    navigator: { configurable: true, value: dom.window.navigator },
    File: { configurable: true, value: dom.window.File },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });
  return () => {
    dom.window.close();
    Object.defineProperties(globalThis, {
      window: { configurable: true, value: previous.window },
      document: { configurable: true, value: previous.document },
      navigator: { configurable: true, value: previous.navigator },
      File: { configurable: true, value: previous.File },
      IS_REACT_ACT_ENVIRONMENT: {
        configurable: true,
        value: previous.IS_REACT_ACT_ENVIRONMENT,
      },
    });
  };
}

function deferred<T>() {
  let resolvePromise: (value: T) => void = () => undefined;
  let rejectPromise: (reason: unknown) => void = () => undefined;
  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return { promise, resolve: resolvePromise, reject: rejectPromise };
}

function createApi(overrides: Partial<ThemeSettingsApi> = {}): ThemeSettingsApi {
  return {
    update: async (payload) => ({ ...DEFAULT_BRANDING, ...payload }),
    uploadAsset: async () => CUSTOM_BRANDING,
    deleteAsset: async () => CUSTOM_BRANDING,
    reset: async () => ({ ...DEFAULT_BRANDING, reconciliation_pending: false }),
    ...overrides,
  };
}

function mountController(api: ThemeSettingsApi, initialBranding = CUSTOM_BRANDING) {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const globalBranding: UIBranding[] = [];
  const toasts: Toast[] = [];
  let savedCount = 0;
  let controller: Controller | null = null;

  function Harness() {
    const nextController = useThemeSettingsController({
      initialBranding,
      api,
      setGlobalBranding: (branding) => globalBranding.push(branding),
      pushToast: (toast) => toasts.push(toast),
      onSaved: () => { savedCount += 1; },
    });
    useEffect(() => {
      controller = nextController;
    }, [nextController]);
    return null;
  }

  act(() => {
    root.render(
      <StrictMode>
        <Harness />
      </StrictMode>,
    );
  });

  return {
    current: () => {
      assert.ok(controller);
      return controller;
    },
    globalBranding,
    toasts,
    savedCount: () => savedCount,
    root,
    restoreDom,
  };
}

async function unmount(root: Root, restoreDom: () => void) {
  await act(async () => { root.unmount(); });
  restoreDom();
}

test('theme controller applies a committed reset and convergence-safe success copy', async () => {
  const mounted = mountController(createApi());
  try {
    act(() => mounted.current().openResetConfirmation());
    await act(async () => { await mounted.current().reset(); });

    assert.deepEqual(mounted.current().value, DEFAULT_BRANDING);
    assert.deepEqual(mounted.current().persisted, DEFAULT_BRANDING);
    assert.equal(mounted.current().resetConfirmationOpen, false);
    assert.deepEqual(mounted.globalBranding, [DEFAULT_BRANDING]);
    assert.equal(mounted.savedCount(), 1);
    assert.deepEqual(mounted.toasts, [{
      tone: 'success',
      title: 'Theme reset',
      message: 'DeltaLLM defaults were saved. Other replicas will converge through normal configuration refresh.',
    }]);
  } finally {
    await unmount(mounted.root, mounted.restoreDom);
  }
});

test('theme controller treats committed reconciliation as saved information', async () => {
  const mounted = mountController(createApi({
    reset: async () => ({ ...DEFAULT_BRANDING, reconciliation_pending: true }),
  }));
  try {
    act(() => mounted.current().openResetConfirmation());
    await act(async () => { await mounted.current().reset(); });

    assert.deepEqual(mounted.current().value, DEFAULT_BRANDING);
    assert.equal(mounted.current().error, null);
    assert.equal(mounted.toasts[0]?.tone, 'info');
    assert.match(mounted.toasts[0]?.message || '', /saved in PostgreSQL/);
  } finally {
    await unmount(mounted.root, mounted.restoreDom);
  }
});

test('theme controller preserves state after rejection and supports manual retry', async () => {
  let attempts = 0;
  const mounted = mountController(createApi({
    reset: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error('Required audit unavailable');
      return { ...DEFAULT_BRANDING, reconciliation_pending: false };
    },
  }));
  try {
    act(() => mounted.current().openResetConfirmation());
    await act(async () => { await mounted.current().reset(); });

    assert.deepEqual(mounted.current().value, CUSTOM_BRANDING);
    assert.deepEqual(mounted.current().persisted, CUSTOM_BRANDING);
    assert.equal(mounted.current().resetConfirmationOpen, true);
    assert.equal(mounted.current().error, 'Required audit unavailable');
    assert.deepEqual(mounted.globalBranding, []);

    await act(async () => { await mounted.current().reset(); });

    assert.equal(attempts, 2);
    assert.deepEqual(mounted.current().value, DEFAULT_BRANDING);
    assert.equal(mounted.current().resetConfirmationOpen, false);
  } finally {
    await unmount(mounted.root, mounted.restoreDom);
  }
});

test('theme controller prevents duplicate reset and ignores completion after unmount', async () => {
  const pending = deferred<Awaited<ReturnType<ThemeSettingsApi['reset']>>>();
  let attempts = 0;
  const mounted = mountController(createApi({
    reset: () => {
      attempts += 1;
      return pending.promise;
    },
  }));
  let first: Promise<void>;
  let duplicate: Promise<void>;
  act(() => {
    first = mounted.current().reset();
    duplicate = mounted.current().reset();
  });

  assert.equal(attempts, 1);
  await act(async () => { mounted.root.unmount(); });
  pending.resolve({ ...DEFAULT_BRANDING, reconciliation_pending: false });
  await Promise.all([first!, duplicate!]);

  assert.deepEqual(mounted.globalBranding, []);
  assert.deepEqual(mounted.toasts, []);
  mounted.restoreDom();
});

test('theme controller preserves save, asset, discard, and validation behavior', async () => {
  let updateCount = 0;
  let uploadCount = 0;
  let deleteCount = 0;
  const mounted = mountController(createApi({
    update: async (payload) => {
      updateCount += 1;
      return { ...CUSTOM_BRANDING, ...payload };
    },
    uploadAsset: async () => {
      uploadCount += 1;
      return { ...CUSTOM_BRANDING, logo_mark_url: '/branding/new-mark.svg' };
    },
    deleteAsset: async () => {
      deleteCount += 1;
      return { ...CUSTOM_BRANDING, logo_mark_url: null };
    },
  }));
  try {
    act(() => mounted.current().setValue({ ...CUSTOM_BRANDING, instance_name: 'Draft' }));
    act(() => mounted.current().discard());
    assert.equal(mounted.current().value.instance_name, 'Acme AI');

    act(() => mounted.current().setValue({ ...CUSTOM_BRANDING, primary_color: '#123456' }));
    await act(async () => { await mounted.current().save(); });
    assert.equal(updateCount, 1);
    assert.equal(mounted.current().persisted.primary_color, '#123456');

    const file = new File([new Uint8Array([1])], 'mark.png', { type: 'image/png' });
    await act(async () => { await mounted.current().upload('logo_mark', file); });
    assert.equal(uploadCount, 1);
    assert.equal(mounted.current().value.logo_mark_url, '/branding/new-mark.svg');

    await act(async () => { await mounted.current().remove('logo_mark'); });
    assert.equal(deleteCount, 1);
    assert.equal(mounted.current().value.logo_mark_url, null);

    act(() => mounted.current().setValue({ ...CUSTOM_BRANDING, instance_name: '   ' }));
    await act(async () => { await mounted.current().save(); });
    assert.equal(updateCount, 1);
    assert.equal(mounted.current().error, 'Instance name is required.');
  } finally {
    await unmount(mounted.root, mounted.restoreDom);
  }
});
