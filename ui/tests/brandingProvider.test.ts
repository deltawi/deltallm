import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import BrandLogo from '../src/components/BrandLogo';
import BrandingProvider from '../src/components/BrandingProvider';
import { DEFAULT_BRANDING, type UIBranding } from '../src/lib/branding';
import {
  BrandingContext,
  type BrandingContextValue,
  useBranding,
} from '../src/lib/brandingContext';

type PendingRequest = {
  resolve: (branding: UIBranding) => void;
};

function installDom() {
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="root"></div></body></html>', {
    url: 'https://admin.example.test/ui/',
  });
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    navigator: globalThis.navigator,
    IS_REACT_ACT_ENVIRONMENT: (globalThis as typeof globalThis & {
      IS_REACT_ACT_ENVIRONMENT?: boolean;
    }).IS_REACT_ACT_ENVIRONMENT,
  };

  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    navigator: { configurable: true, value: dom.window.navigator },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });

  return () => {
    dom.window.close();
    Object.defineProperties(globalThis, {
      window: { configurable: true, value: previous.window },
      document: { configurable: true, value: previous.document },
      navigator: { configurable: true, value: previous.navigator },
      IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: previous.IS_REACT_ACT_ENVIRONMENT },
    });
  };
}

function jsonResponse(branding: UIBranding): Response {
  return new Response(JSON.stringify(branding), {
    headers: { 'content-type': 'application/json' },
  });
}

test('branding bootstrap gates product UI, recovers the favicon, and ignores stale refreshes', async () => {
  const restoreDom = installDom();
  const originalFetch = globalThis.fetch;
  const requests: PendingRequest[] = [];
  globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
    const signal = init?.signal;
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    requests.push({ resolve: (branding) => resolve(jsonResponse(branding)) });
  })) as typeof fetch;

  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const savedBranding: UIBranding = { ...DEFAULT_BRANDING, instance_name: 'Saved Brand' };
  function Probe() {
    const { assetRevision, refreshBranding, setBranding } = useBranding();
    return createElement(
      'div',
      { id: 'product-ui', 'data-asset-revision': assetRevision },
      'Product UI',
      createElement('button', { id: 'refresh-branding', onClick: () => void refreshBranding() }, 'Refresh'),
      createElement('button', { id: 'save-branding', onClick: () => setBranding(savedBranding) }, 'Save'),
    );
  }

  try {
    await act(async () => {
      root.render(createElement(BrandingProvider, null, createElement(Probe)));
    });

    assert.equal(document.getElementById('product-ui'), null);
    assert.equal(document.querySelector('[role="status"]')?.textContent, 'Loading application');
    assert.equal(requests.length, 1);
    window.dispatchEvent(new window.Event('focus'));
    assert.equal(requests.length, 1);

    const initialBranding: UIBranding = {
      ...DEFAULT_BRANDING,
      instance_name: 'Acme AI',
      favicon_url: 'https://cdn.example.com/favicon.png',
      primary_color: '#7C3AED',
    };
    await act(async () => {
      requests[0].resolve(initialBranding);
      await Promise.resolve();
    });

    assert.ok(document.getElementById('product-ui'));
    assert.equal(document.title, 'Acme AI Admin');
    assert.equal(document.documentElement.style.getPropertyValue('--brand-primary'), '124 58 237');
    const favicon = document.querySelector<HTMLLinkElement>('link[rel~="icon"]');
    assert.ok(favicon);
    assert.equal(favicon.getAttribute('type'), null);
    assert.equal(favicon.href, 'https://cdn.example.com/favicon.png');

    favicon.dispatchEvent(new window.Event('error'));
    assert.equal(favicon.type, 'image/svg+xml');
    assert.equal(favicon.href, 'https://admin.example.test/favicon.svg');

    await act(async () => {
      document.querySelector<HTMLButtonElement>('#refresh-branding')!.click();
      await Promise.resolve();
    });
    assert.equal(requests.length, 2);

    act(() => document.querySelector<HTMLButtonElement>('#save-branding')!.click());
    await act(async () => {
      requests[1].resolve({ ...DEFAULT_BRANDING, instance_name: 'Stale Brand' });
      await Promise.resolve();
    });
    assert.equal(document.title, 'Saved Brand Admin');

    await act(async () => {
      window.dispatchEvent(new window.Event('focus'));
      await Promise.resolve();
    });
    assert.equal(requests.length, 3);

    const peerBranding: UIBranding = {
      ...savedBranding,
      instance_name: 'Peer Replica Brand',
      primary_color: '#112233',
    };
    await act(async () => {
      requests[2].resolve(peerBranding);
      await Promise.resolve();
    });
    assert.equal(document.title, 'Peer Replica Brand Admin');
    const revisionAfterPeerUpdate = document.getElementById('product-ui')?.getAttribute('data-asset-revision');

    await act(async () => {
      window.dispatchEvent(new window.Event('focus'));
      await Promise.resolve();
    });
    assert.equal(requests.length, 4);
    await act(async () => {
      requests[3].resolve(peerBranding);
      await Promise.resolve();
    });
    assert.equal(
      document.getElementById('product-ui')?.getAttribute('data-asset-revision'),
      revisionAfterPeerUpdate,
    );
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    restoreDom();
  }
});

test('failed logos retry once and a branding revision makes the same URL eligible again', async () => {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const branding: UIBranding = {
    ...DEFAULT_BRANDING,
    logo_mark_url: '/branding/mark.svg',
    logo_full_url: '/branding/wordmark.svg',
  };
  const retryCallbacks: Array<() => void> = [];
  const originalSetTimeout = window.setTimeout.bind(window);
  window.setTimeout = ((callback: TimerHandler) => {
    assert.equal(typeof callback, 'function');
    retryCallbacks.push(callback as () => void);
    return retryCallbacks.length;
  }) as typeof window.setTimeout;

  const renderLogo = (assetRevision: number) => {
    const value: BrandingContextValue = {
      branding,
      assetRevision,
      refreshBranding: async () => branding,
      setBranding: () => undefined,
    };
    root.render(createElement(
      BrandingContext.Provider,
      { value },
      createElement(BrandLogo, { variant: 'expanded', brandingOverride: branding }),
    ));
  };

  try {
    await act(async () => renderLogo(1));
    let image = document.querySelector<HTMLImageElement>('img');
    assert.equal(image?.getAttribute('src'), '/branding/wordmark.svg');

    act(() => image!.dispatchEvent(new window.Event('error', { bubbles: true })));
    image = document.querySelector<HTMLImageElement>('img');
    assert.equal(image?.getAttribute('src'), '/branding/mark.svg');
    assert.equal(retryCallbacks.length, 1);

    act(() => retryCallbacks[0]());
    image = document.querySelector<HTMLImageElement>('img');
    assert.equal(image?.getAttribute('src'), '/branding/wordmark.svg');

    act(() => image!.dispatchEvent(new window.Event('error', { bubbles: true })));
    assert.equal(document.querySelector<HTMLImageElement>('img')?.getAttribute('src'), '/branding/mark.svg');
    assert.equal(retryCallbacks.length, 1);

    await act(async () => renderLogo(2));
    assert.equal(document.querySelector<HTMLImageElement>('img')?.getAttribute('src'), '/branding/wordmark.svg');
  } finally {
    await act(async () => root.unmount());
    window.setTimeout = originalSetTimeout;
    restoreDom();
  }
});
