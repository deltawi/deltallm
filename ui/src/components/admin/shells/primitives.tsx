import { useRef, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, type LucideIcon } from 'lucide-react';
import { tabClass } from './tabClass';

export type SummaryItem = {
  label: string;
  value: ReactNode;
  icon?: LucideIcon;
  iconClassName?: string;
};

export type BreadcrumbItem = {
  label: ReactNode;
  to?: string;
  onClick?: () => void;
  icon?: LucideIcon;
};

export type DetailStatTone = 'blue' | 'green' | 'amber' | 'violet' | 'indigo' | 'gray';

type BreadcrumbsProps = {
  items: BreadcrumbItem[];
  className?: string;
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
  label?: string;
  items: Array<{
    id: T;
    label: ReactNode;
  }>;
  active: T;
  onChange: (id: T) => void;
};

type IconTabsProps<T extends string> = {
  id?: string;
  label?: string;
  items: ReadonlyArray<{
    id: T;
    label: ReactNode;
    icon?: LucideIcon;
    count?: ReactNode;
  }>;
  active: T;
  onChange: (id: T) => void;
  // header: page tabs that sit on the bottom edge of the page header band.
  // segment: small pill buttons that switch a view inside a card or drawer.
  variant?: 'line' | 'card' | 'header' | 'segment';
};

const toneClasses: Record<DetailStatTone, { bg: string; icon: string }> = {
  blue: { bg: 'bg-blue-50', icon: 'text-brand-primary-ink' },
  green: { bg: 'bg-green-50', icon: 'text-green-500' },
  amber: { bg: 'bg-amber-50', icon: 'text-amber-500' },
  violet: { bg: 'bg-violet-50', icon: 'text-brand-secondary-ink' },
  indigo: { bg: 'bg-indigo-50', icon: 'text-brand-secondary-ink' },
  gray: { bg: 'bg-gray-100', icon: 'text-gray-500' },
};

export function Breadcrumbs({ items, className = 'mb-3' }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb" className={`${className} flex flex-wrap items-center gap-1.5 text-xs text-gray-400`}>
      {items.map((item, index) => {
        const Icon = item.icon;
        const content = (
          <>
            {Icon && <Icon className="h-3 w-3" />}
            <span
              aria-current={index === items.length - 1 ? 'page' : undefined}
              className={index === items.length - 1 ? 'font-medium text-gray-600' : undefined}
            >
              {item.label}
            </span>
          </>
        );

        return (
          <div key={index} className="flex items-center gap-1.5">
            {item.to ? (
              <Link to={item.to} className="flex items-center gap-1 text-xs text-gray-400 transition-colors hover:text-gray-700">
                {content}
              </Link>
            ) : item.onClick ? (
              <button
                type="button"
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
    </nav>
  );
}

// Square avatar for the record a detail page shows. Uses the brand colors so it follows theme customization.
export function EntityAvatar({ icon: Icon, initial }: { icon?: LucideIcon; initial?: string }) {
  return (
    <div aria-hidden="true" className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-brand-primary text-brand-on-primary shadow-sm">
      {Icon ? <Icon className="h-6 w-6" /> : <span className="text-lg font-bold">{initial?.charAt(0).toUpperCase()}</span>}
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

export function TextTabs<T extends string>({ items, active, onChange, label }: TextTabsProps<T>) {
  const focusTab = (current: HTMLButtonElement, index: number) => {
    current.parentElement
      ?.querySelector<HTMLElement>(`[role="tab"][data-tab-index="${index}"]`)
      ?.focus();
  };
  const moveFocus = (current: HTMLButtonElement, currentIndex: number, direction: -1 | 1) => {
    const nextIndex = (currentIndex + direction + items.length) % items.length;
    onChange(items[nextIndex].id);
    requestAnimationFrame(() => focusTab(current, nextIndex));
  };

  return (
    <div role="tablist" aria-label={label} className="contents">
      {items.map((item, index) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          aria-selected={active === item.id}
          tabIndex={active === item.id ? 0 : -1}
          data-tab-index={index}
          onClick={() => onChange(item.id)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') {
              event.preventDefault();
              moveFocus(event.currentTarget, index, -1);
            } else if (event.key === 'ArrowRight') {
              event.preventDefault();
              moveFocus(event.currentTarget, index, 1);
            } else if (event.key === 'Home') {
              event.preventDefault();
              onChange(items[0].id);
              requestAnimationFrame(() => focusTab(event.currentTarget, 0));
            } else if (event.key === 'End') {
              event.preventDefault();
              const lastIndex = items.length - 1;
              onChange(items[lastIndex].id);
              requestAnimationFrame(() => focusTab(event.currentTarget, lastIndex));
            }
          }}
          className={`border-b-2 pb-3 text-sm font-medium transition-colors ${
            active === item.id
              ? 'border-brand-primary text-brand-primary-ink'
              : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
          }`}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function IconTabs<T extends string>({
  items,
  active,
  onChange,
  variant = 'line',
  id,
  label = 'Sections',
}: IconTabsProps<T>) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  return (
    <div role="tablist" aria-label={label} className={variant === 'segment'
      ? 'inline-flex max-w-full overflow-x-auto rounded-lg border border-gray-200 bg-white p-0.5'
      : `flex overflow-x-auto ${variant === 'card' ? 'border-b border-gray-100' : variant === 'header' ? 'gap-1' : 'mb-4 gap-1 border-b border-gray-200'}`}>
      {items.map((item, index) => {
        const Icon = item.icon;
        const selected = active === item.id;
        return (
          <button
            key={item.id}
            ref={(element) => { buttons.current[index] = element; }}
            type="button"
            role="tab"
            id={id ? `${id}-tab-${item.id}` : undefined}
            aria-controls={id ? `${id}-panel-${item.id}` : undefined}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(item.id)}
            onKeyDown={(event) => {
              let next: number;
              if (event.key === 'ArrowRight') next = (index + 1) % items.length;
              else if (event.key === 'ArrowLeft') next = (index - 1 + items.length) % items.length;
              else if (event.key === 'Home') next = 0;
              else if (event.key === 'End') next = items.length - 1;
              else return;
              event.preventDefault();
              onChange(items[next].id);
              buttons.current[next]?.focus();
            }}
            className={variant === 'card'
              ? `flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-5 py-4 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${selected ? 'border-brand-primary bg-blue-50/40 text-brand-primary-ink' : 'border-transparent text-gray-500 hover:bg-gray-50 hover:text-gray-800'}`
              : variant === 'segment'
                ? `flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${selected ? 'bg-brand-primary-soft text-brand-primary-ink' : 'text-gray-600 hover:text-gray-900'}`
                : tabClass(selected)}
          >
            {Icon ? <Icon className="h-4 w-4" aria-hidden="true" /> : null}
            {item.label}
            {item.count !== undefined && <span className={`rounded font-semibold ${variant === 'segment' ? 'px-1 text-[10px]' : 'px-1.5 py-0.5 text-xs'} ${variant === 'card' && selected ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'}`}>{item.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
