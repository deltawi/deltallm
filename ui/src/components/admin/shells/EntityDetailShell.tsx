import type { ReactNode } from 'react';
import { Breadcrumbs, type BreadcrumbItem } from './primitives';

type EntityDetailShellProps = {
  breadcrumbs: BreadcrumbItem[];
  avatar: ReactNode;
  title: string;
  badges?: ReactNode;
  meta?: ReactNode;
  action?: ReactNode;
  metrics?: ReactNode;
  tabs?: ReactNode;
  notice?: ReactNode;
  children: ReactNode;
};

export default function EntityDetailShell({
  breadcrumbs,
  avatar,
  title,
  badges,
  meta,
  action,
  metrics,
  tabs,
  notice,
  children,
}: EntityDetailShellProps) {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="border-b border-gray-200 bg-white">
        <div className="px-6 py-4">
          <Breadcrumbs items={breadcrumbs} />

          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-4">
              {avatar}
              <div>
                <div className="flex flex-wrap items-center gap-2.5">
                  <h1 className="text-xl font-bold text-gray-900">{title}</h1>
                  {badges}
                </div>
                {meta ? <div className="mt-1">{meta}</div> : null}
              </div>
            </div>
            {action}
          </div>

          {metrics ? <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">{metrics}</div> : null}
          {tabs ? <div className="mt-5 -mb-px flex gap-6">{tabs}</div> : null}
        </div>
      </div>

      <div className="px-6 py-5">
        {notice ? <div className="mb-4">{notice}</div> : null}
        {children}
      </div>
    </div>
  );
}
