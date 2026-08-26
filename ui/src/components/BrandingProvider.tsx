import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { branding as brandingApi } from '../lib/api';
import {
  brandingCssVariables,
  DEFAULT_BRANDING,
  normalizeBranding,
  sameBranding,
  type UIBranding,
} from '../lib/branding';
import { BrandingContext } from '../lib/brandingContext';

const BRANDING_BOOTSTRAP_TIMEOUT_MS = 3_000;

function defaultFaviconUrl(): string {
  return new URL('/favicon.svg', document.baseURI).toString();
}

export default function BrandingProvider({ children }: { children: ReactNode }) {
  const [branding, setBrandingState] = useState<UIBranding>(DEFAULT_BRANDING);
  const [assetRevision, setAssetRevision] = useState(0);
  const [ready, setReady] = useState(false);
  const requestGeneration = useRef(0);
  const brandingRef = useRef<UIBranding>(DEFAULT_BRANDING);
  const [defaultFavicon] = useState(defaultFaviconUrl);

  const applyBranding = useCallback((nextBranding: UIBranding, forceAssetRevision = false) => {
    const normalized = normalizeBranding(nextBranding);
    const changed = !sameBranding(brandingRef.current, normalized);
    if (changed) {
      brandingRef.current = normalized;
      setBrandingState(normalized);
    }
    if (changed || forceAssetRevision) setAssetRevision((revision) => revision + 1);
    setReady(true);
    return normalized;
  }, []);

  const setBranding = useCallback((nextBranding: UIBranding) => {
    requestGeneration.current += 1;
    applyBranding(nextBranding, true);
  }, [applyBranding]);

  const refreshBranding = useCallback(async () => {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    const nextBranding = normalizeBranding(await brandingApi.get());
    if (requestGeneration.current === generation) applyBranding(nextBranding);
    return nextBranding;
  }, [applyBranding]);

  useEffect(() => {
    if (!ready) return undefined;

    const refreshAfterReturn = () => {
      void refreshBranding().catch(() => undefined);
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') refreshAfterReturn();
    };

    window.addEventListener('focus', refreshAfterReturn);
    document.addEventListener('visibilitychange', refreshWhenVisible);
    return () => {
      window.removeEventListener('focus', refreshAfterReturn);
      document.removeEventListener('visibilitychange', refreshWhenVisible);
    };
  }, [ready, refreshBranding]);

  useEffect(() => {
    const controller = new AbortController();
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    const timeout = window.setTimeout(() => controller.abort(), BRANDING_BOOTSTRAP_TIMEOUT_MS);

    brandingApi.get(controller.signal)
      .then((nextBranding) => {
        if (requestGeneration.current === generation) applyBranding(nextBranding);
      })
      .catch(() => {
        if (requestGeneration.current === generation) applyBranding(DEFAULT_BRANDING);
      })
      .finally(() => window.clearTimeout(timeout));

    return () => {
      if (requestGeneration.current === generation) requestGeneration.current += 1;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [applyBranding]);

  useLayoutEffect(() => {
    if (!ready) return undefined;

    const root = document.documentElement;
    const variables = brandingCssVariables(branding);
    Object.entries(variables).forEach(([name, value]) => root.style.setProperty(name, value));

    document.title = `${branding.instance_name} Admin`;
    let favicon = document.querySelector<HTMLLinkElement>('link[rel~="icon"]');
    if (!favicon) {
      favicon = document.createElement('link');
      favicon.rel = 'icon';
      document.head.appendChild(favicon);
    }

    const activeFavicon = favicon;
    const customFavicon = branding.favicon_url;
    const intendedUrl = customFavicon || defaultFavicon;
    const handleFaviconError = () => {
      if (activeFavicon.href !== new URL(intendedUrl, document.baseURI).toString()) return;
      activeFavicon.onerror = null;
      activeFavicon.type = 'image/svg+xml';
      activeFavicon.href = defaultFavicon;
    };

    activeFavicon.onerror = customFavicon ? handleFaviconError : null;
    if (customFavicon) activeFavicon.removeAttribute('type');
    else activeFavicon.type = 'image/svg+xml';
    activeFavicon.href = intendedUrl;

    return () => {
      if (activeFavicon.onerror === handleFaviconError) activeFavicon.onerror = null;
    };
  }, [branding, defaultFavicon, ready]);

  const value = useMemo(() => ({ branding, assetRevision, refreshBranding, setBranding }), [
    assetRevision,
    branding,
    refreshBranding,
    setBranding,
  ]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50" role="status" aria-live="polite">
        <span className="h-7 w-7 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" aria-hidden="true" />
        <span className="sr-only">Loading application</span>
      </div>
    );
  }

  return <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>;
}
