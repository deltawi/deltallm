import { useEffect, useRef, useState } from 'react';
import { Zap } from 'lucide-react';
import clsx from 'clsx';
import { normalizeBranding, type UIBranding } from '../lib/branding';
import { useBranding } from '../lib/brandingContext';

interface BrandLogoProps {
  variant?: 'mark' | 'expanded';
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
    branding.logo_full_url || '',
    branding.logo_mark_url || '',
  ]);
  const [failures, setFailures] = useState<{ key: string; urls: string[] }>({ key: failureKey, urls: [] });
  const retryCounts = useRef(new Map<string, number>());
  const retryTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const failedUrls = failures.key === failureKey ? failures.urls : [];
  const candidates = variant === 'expanded'
    ? [branding.logo_full_url, branding.logo_mark_url]
    : [branding.logo_mark_url];
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

  if (variant === 'expanded' && visibleUrl && visibleUrl === branding.logo_full_url) {
    return (
      <div className={clsx('flex min-w-0 items-center', className)}>
        <img
          src={visibleUrl}
          alt={branding.instance_name}
          className={clsx('h-8 w-auto max-w-full object-contain object-left', fullClassName)}
          onError={() => markFailed(visibleUrl)}
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
          className={clsx(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] border border-brand-secondary/20 bg-brand-secondary-soft shadow-sm',
            markClassName,
          )}
        >
          <Zap className="h-1/2 w-1/2 fill-brand-secondary text-brand-secondary-ink" />
        </span>
      )}
      {variant === 'expanded' && (
        <span className={clsx('truncate text-lg font-bold text-gray-900', nameClassName)}>
          {branding.instance_name}
        </span>
      )}
    </div>
  );
}
