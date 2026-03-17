import type { ReactNode } from 'react';
import { ChevronRight, type LucideIcon } from 'lucide-react';

export type SummaryItem = {
  label: string;
  value: ReactNode;
  icon?: LucideIcon;
  iconClassName?: string;
};

export type BreadcrumbItem = {
  label: ReactNode;
  onClick?: () => void;
  icon?: LucideIcon;
};

export type DetailStatTone = 'blue' | 'green' | 'amber' | 'violet' | 'indigo' | 'gray';

type BreadcrumbsProps = {
  items: BreadcrumbItem[];
};

type SummaryStripProps = {
  items: SummaryItem[];
};

type DetailMetricCardProps = {
  icon: LucideIcon;
  label: string;
  value: string;
  sub?: string;
  tone?: DetailStatTone;
};

type TextTabsProps<T extends string> = {
  items: Array<{
    id: T;
    label: ReactNode;
  }>;
  active: T;
  onChange: (id: T) => void;
};

type IconTabsProps<T extends string> = {
  items: Array<{
    id: T;
    label: ReactNode;
    icon: LucideIcon;
    count?: ReactNode;
  }>;
  active: T;
  onChange: (id: T) => void;
  variant?: 'line' | 'card';
};

const toneClasses: Record<DetailStatTone, { bg: string; icon: string }> = {
  blue: { bg: 'bg-blue-50', icon: 'text-blue-600' },
  green: { bg: 'bg-green-50', icon: 'text-green-500' },
  amber: { bg: 'bg-amber-50', icon: 'text-amber-500' },
  violet: { bg: 'bg-violet-50', icon: 'text-violet-600' },
  indigo: { bg: 'bg-indigo-50', icon: 'text-indigo-600' },
  gray: { bg: 'bg-gray-100', icon: 'text-gray-500' },
};

export function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <div className="mb-3 flex items-center gap-1.5 text-xs text-gray-400">
      {items.map((item, index) => {
        const Icon = item.icon;
        const content = (
          <>
            {Icon && <Icon className="h-3 w-3" />}
            <span className={index === items.length - 1 ? 'font-medium text-gray-600' : undefined}>{item.label}</span>
          </>
        );

        return (
          <div key={index} className="flex items-center gap-1.5">
            {item.onClick ? (
              <button
                onClick={item.onClick}
                className="flex items-center gap-1 text-xs text-gray-400 transition-colors hover:text-gray-700"
              >
                {content}
              </button>
            ) : (
              <span className="flex items-center gap-1">{content}</span>
            )}
            {index < items.length - 1 && <ChevronRight className="h-3 w-3" />}
          </div>
        );
      })}
    </div>
  );
}

export function SummaryStrip({ items }: SummaryStripProps) {
  return (
    <div className="flex flex-wrap gap-8 border-b border-gray-100 bg-white px-6 py-3">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <div key={item.label} className="flex items-center gap-2">
            {Icon && <Icon className={`h-3.5 w-3.5 ${item.iconClassName || 'text-gray-500'}`} />}
            <span className="text-xs text-gray-500">{item.label}</span>
            <span className="text-xs font-semibold text-gray-900">{item.value}</span>
          </div>
        );
      })}
    </div>
  );
}

export function ContentCard({ children }: { children: ReactNode }) {
  return <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">{children}</div>;
}

export function PanelCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-2xl border border-gray-200 bg-white p-5 shadow-sm ${className}`.trim()}>{children}</div>;
}

export function DetailMetricCard({
  icon: Icon,
  label,
  value,
  sub,
  tone = 'blue',
}: DetailMetricCardProps) {
  const classes = toneClasses[tone];
  return (
    <div className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
      <div className={`shrink-0 rounded-lg p-2 ${classes.bg}`}>
        <Icon className={`h-4 w-4 ${classes.icon}`} />
      </div>
      <div>
        <p className="leading-none text-lg font-bold text-gray-900">{value}</p>
        <p className="mt-0.5 text-xs text-gray-400">{label}</p>
        {sub && <p className="text-[10px] text-gray-400">{sub}</p>}
      </div>
    </div>
  );
}

export function TextTabs<T extends string>({ items, active, onChange }: TextTabsProps<T>) {
  return (
    <>
      {items.map((item) => (
        <button
          key={item.id}
          onClick={() => onChange(item.id)}
          className={`border-b-2 pb-3 text-sm font-medium transition-colors ${
            active === item.id
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
          }`}
        >
          {item.label}
        </button>
      ))}
    </>
  );
}

export function InlineStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">{label}</span>
      <span className="text-sm font-semibold text-gray-900">{value}</span>
    </div>
  );
}

export function IconTabs<T extends string>({
  items,
  active,
  onChange,
  variant = 'line',
}: IconTabsProps<T>) {
  if (variant === 'card') {
    return (
      <div className="flex overflow-x-auto border-b border-gray-100">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onChange(item.id)}
              className={`flex items-center gap-2 whitespace-nowrap border-b-2 px-5 py-4 text-sm font-medium transition-colors ${
                active === item.id
                  ? 'border-blue-600 bg-blue-50/40 text-blue-600'
                  : 'border-transparent text-gray-500 hover:bg-gray-50 hover:text-gray-800'
              }`}
            >
              <Icon className="h-4 w-4" />
              {item.label}
              {item.count !== undefined ? (
                <span
                  className={`ml-0.5 rounded px-1.5 py-0.5 text-xs font-semibold ${
                    active === item.id ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                  }`}
                >
                  {item.count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="mb-4 flex gap-1 border-b border-gray-200">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onChange(item.id)}
            className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition ${
              active === item.id
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <Icon className="h-4 w-4" />
            {item.label}
            {item.count !== undefined ? (
              <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-semibold text-gray-500">
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
