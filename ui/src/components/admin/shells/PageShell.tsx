import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Breadcrumbs, SummaryStrip, type BreadcrumbItem, type SummaryItem } from './primitives';

export type PageShellProps = {
  // Menu section, list page, then the current page (e.g. Gateway > Models > gpt-4o).
  breadcrumbs?: BreadcrumbItem[];
  title: ReactNode;
  // Menu icon next to the title. Detail pages pass an avatar instead.
  icon?: LucideIcon;
  avatar?: ReactNode;
  count?: number | null;
  badges?: ReactNode;
  description?: ReactNode;
  // Identifiers, dates and similar facts under the title.
  meta?: ReactNode;
  actions?: ReactNode;
  // Key figures shown inside the header, e.g. DetailMetricCard tiles.
  stats?: ReactNode;
  // Search and filters for the whole page, inside the header.
  toolbar?: ReactNode;
  // Page tabs; they sit on the header's bottom edge.
  tabs?: ReactNode;
  summaryItems?: SummaryItem[];
  summary?: ReactNode;
  notice?: ReactNode;
  // form keeps long forms at a readable width, aligned with the header.
  width?: 'full' | 'form';
  children: ReactNode;
};

export default function PageShell({
  breadcrumbs = [],
  title,
  icon: Icon,
  avatar,
  count,
  badges,
  description,
  meta,
  actions,
  stats,
  toolbar,
  tabs,
  summaryItems = [],
  summary,
  notice,
  width = 'full',
  children,
}: PageShellProps) {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className={`border-b border-gray-200 bg-white px-6 pt-4 ${tabs ? '' : 'pb-4'}`}>
        {breadcrumbs.length > 0 ? <Breadcrumbs items={breadcrumbs} className="mb-1" /> : null}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className={`flex min-w-0 flex-1 basis-64 items-start ${avatar ? 'gap-4 pt-1' : ''}`}>
            {avatar}
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <h1 className="flex min-w-0 items-center gap-2 break-words text-xl font-bold text-gray-900">
                  {Icon ? <Icon className="h-5 w-5 shrink-0 text-brand-primary-ink" aria-hidden="true" /> : null}
                  <span className="min-w-0">{title}</span>
                  {typeof count === 'number' && (
                    <span className="ml-1 inline-flex h-5 min-w-[1.5rem] items-center justify-center rounded-full bg-gray-100 px-1.5 text-xs font-semibold text-gray-600">
                      {count}
                    </span>
                  )}
                </h1>
                {badges}
              </div>
              {description ? <div className="mt-1 text-sm text-gray-500">{description}</div> : null}
              {meta ? <div className="mt-1.5 text-xs text-gray-500">{meta}</div> : null}
            </div>
          </div>
          {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
        {stats ? <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(9.5rem,1fr))] gap-3">{stats}</div> : null}
        {toolbar ? <div className="mt-4">{toolbar}</div> : null}
        {tabs ? <div className="-mb-px mt-3">{tabs}</div> : null}
      </header>

      {summaryItems.length > 0 ? <SummaryStrip items={summaryItems} /> : null}
      {summary ? <div className="mx-6 mt-4">{summary}</div> : null}
      {notice ? <div className="mx-6 mt-4">{notice}</div> : null}

      <div className="px-6 py-5">
        {width === 'form' ? <div className="max-w-4xl">{children}</div> : children}
      </div>
    </div>
  );
}
