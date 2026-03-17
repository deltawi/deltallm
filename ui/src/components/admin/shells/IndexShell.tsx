import type { ReactNode } from 'react';
import { ChevronRight, type LucideIcon } from 'lucide-react';
import { SummaryStrip, type SummaryItem } from './primitives';

type IndexShellProps = {
  title: string;
  titleIcon?: LucideIcon;
  count?: number | null;
  description?: ReactNode;
  eyebrowPrefix?: ReactNode;
  eyebrowCurrent?: ReactNode;
  action?: ReactNode;
  intro?: ReactNode;
  summary?: ReactNode;
  toolbar?: ReactNode;
  summaryItems?: SummaryItem[];
  notice?: ReactNode;
  children: ReactNode;
};

export default function IndexShell({
  title,
  titleIcon: TitleIcon,
  count,
  description,
  eyebrowPrefix = 'Platform',
  eyebrowCurrent,
  action,
  intro,
  summary,
  toolbar,
  summaryItems = [],
  notice,
  children,
}: IndexShellProps) {
  const showEyebrow = eyebrowPrefix || eyebrowCurrent || title;

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            {showEyebrow && (
              <div className="mb-1 flex items-center gap-1.5 text-xs text-gray-400">
                {eyebrowPrefix ? <span>{eyebrowPrefix}</span> : null}
                {eyebrowPrefix && (eyebrowCurrent || title) ? <ChevronRight className="h-3 w-3" /> : null}
                <span className="font-medium text-gray-600">{eyebrowCurrent || title}</span>
              </div>
            )}
            <h1 className="flex items-center gap-2 text-xl font-bold text-gray-900">
              {TitleIcon ? <TitleIcon className="h-5 w-5 text-blue-600" /> : null}
              {title}
              {typeof count === 'number' && (
                <span className="ml-1 inline-flex h-5 min-w-[1.5rem] items-center justify-center rounded-full bg-gray-100 px-1.5 text-xs font-semibold text-gray-600">
                  {count}
                </span>
              )}
            </h1>
            {description ? <p className="mt-1 text-sm text-gray-500">{description}</p> : null}
          </div>
          {action}
        </div>
        {toolbar ? <div className="mt-4">{toolbar}</div> : null}
      </div>

      {intro ? <div className="mx-6 mt-4">{intro}</div> : null}
      {summary ? <div className="mx-6 mt-4">{summary}</div> : null}
      {summaryItems.length > 0 ? <SummaryStrip items={summaryItems} /> : null}
      {notice ? <div className="mx-6 mt-4">{notice}</div> : null}

      <div className="px-6 py-4">{children}</div>
    </div>
  );
}
