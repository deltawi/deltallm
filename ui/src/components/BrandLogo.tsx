import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import {
  BUILT_IN_BRAND_ASSETS,
  DEFAULT_BRANDING,
  normalizeBranding,
  type UIBranding,
} from '../lib/branding';
import { useBranding } from '../lib/brandingContext';

interface BrandLogoProps {
  variant?: 'mark' | 'expanded' | 'reveal';
  className?: string;
  markClassName?: string;
  fullClassName?: string;
  nameClassName?: string;
  brandingOverride?: UIBranding;
}

export default function BrandLogo({
  variant = 'expanded',
  className,
  markClassName,
  fullClassName,
  nameClassName,
  brandingOverride,
}: BrandLogoProps) {
  const { branding: activeBranding, assetRevision } = useBranding();
  const branding = normalizeBranding(brandingOverride || activeBranding);
  const failureKey = JSON.stringify([
    assetRevision,
    variant,
    branding.instance_name,
    branding.logo_full_url || '',
    branding.logo_mark_url || '',
  ]);
  const [failures, setFailures] = useState<{ key: string; urls: string[] }>({ key: failureKey, urls: [] });
  const retryCounts = useRef(new Map<string, number>());
  const retryTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const failedUrls = failures.key === failureKey ? failures.urls : [];
  const builtInExpandedAsset = branding.instance_name === DEFAULT_BRANDING.instance_name
    ? variant === 'reveal'
      ? '/brand/deltallm-delta-reveal-light.svg'
      : BUILT_IN_BRAND_ASSETS.logo_full
    : BUILT_IN_BRAND_ASSETS.logo_mark;
  const candidates = variant !== 'mark'
    ? [branding.logo_full_url, branding.logo_mark_url, builtInExpandedAsset]
    : [branding.logo_mark_url, BUILT_IN_BRAND_ASSETS.logo_mark];
  const visibleUrl = candidates.find((candidate) => candidate && !failedUrls.includes(candidate)) || null;

  useEffect(() => {
    const retryPrefix = `${failureKey}:`;
    const timers = retryTimers.current;
    const counts = retryCounts.current;
    return () => {
      timers.forEach((timer, retryKey) => {
        if (!retryKey.startsWith(retryPrefix)) return;
        window.clearTimeout(timer);
        timers.delete(retryKey);
      });
      counts.forEach((_count, retryKey) => {
        if (retryKey.startsWith(retryPrefix)) counts.delete(retryKey);
      });
    };
  }, [failureKey]);

  const markFailed = (url: string) => {
    const retryKey = `${failureKey}:${url}`;
    setFailures((current) => {
      const currentUrls = current.key === failureKey ? current.urls : [];
      return currentUrls.includes(url) ? current : { key: failureKey, urls: [...currentUrls, url] };
    });

    if ((retryCounts.current.get(retryKey) || 0) >= 1) return;
    retryCounts.current.set(retryKey, 1);
    const timer = window.setTimeout(() => {
      retryTimers.current.delete(retryKey);
      setFailures((current) => (
        current.key === failureKey
          ? { key: failureKey, urls: current.urls.filter((failedUrl) => failedUrl !== url) }
          : current
      ));
    }, 5_000);
    retryTimers.current.set(retryKey, timer);
  };

  const fullLogoUrl = variant !== 'mark'
    && (
      visibleUrl === branding.logo_full_url
      || (visibleUrl === builtInExpandedAsset && builtInExpandedAsset !== BUILT_IN_BRAND_ASSETS.logo_mark)
    )
    ? visibleUrl
    : null;

  if (fullLogoUrl) {
    return (
      <div className={clsx('flex min-w-0 items-center', className)}>
        <img
          src={fullLogoUrl}
          alt={branding.instance_name}
          className={clsx('h-8 w-auto max-w-full object-contain object-left', fullClassName)}
          onError={() => markFailed(fullLogoUrl)}
        />
      </div>
    );
  }

  return (
    <div className={clsx('flex min-w-0 items-center gap-2.5', className)}>
      {visibleUrl ? (
        <img
          src={visibleUrl}
          alt={variant === 'mark' ? `${branding.instance_name} logo` : ''}
          className={clsx('h-8 w-8 shrink-0 object-contain', markClassName)}
          onError={() => markFailed(visibleUrl)}
        />
      ) : (
        <span
          role={variant === 'mark' ? 'img' : undefined}
          aria-label={variant === 'mark' ? `${branding.instance_name} logo` : undefined}
          aria-hidden={variant === 'mark' ? undefined : true}
          className={clsx(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-brand-secondary/20 bg-brand-secondary-soft text-brand-secondary-ink',
            markClassName,
          )}
        >
          Δ
        </span>
      )}
      {variant !== 'mark' && (
        <span className={clsx('truncate text-lg font-bold text-gray-900', nameClassName)}>
          {branding.instance_name}
        </span>
      )}
    </div>
  );
}
