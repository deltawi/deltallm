import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Link } from 'react-router-dom';
import { tabClass } from './tabClass';

export type HeaderTabLink = {
  to: string;
  label: ReactNode;
  icon?: LucideIcon;
  active: boolean;
};

type HeaderTabBarProps = {
  label: string;
  // Route tabs; leave empty on a page that only has controls.
  items?: HeaderTabLink[];
  // Page-wide controls such as scope and period, right-aligned on wide screens.
  controls?: ReactNode;
};

// PageShell `tabs` row: route tabs on the header's bottom edge with the page's controls beside them.
// On narrow screens the controls stack above the tabs, so the tabs stay on the edge.
export default function HeaderTabBar({ label, items = [], controls }: HeaderTabBarProps) {
  const hasTabs = items.length > 0;
  return (
    <div className={`flex flex-col-reverse gap-3 xl:flex-row xl:items-end xl:justify-between ${hasTabs ? '' : 'pb-4'}`}>
      {hasTabs ? (
        <nav aria-label={label} className="flex gap-1">
          {items.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              aria-current={item.active ? 'page' : undefined}
              className={tabClass(item.active)}
            >
              {item.icon ? <item.icon className="h-4 w-4" aria-hidden="true" /> : null}
              {item.label}
            </Link>
          ))}
        </nav>
      ) : null}
      {controls ? (
        <div className={`flex flex-wrap items-center gap-2.5 xl:ml-auto ${hasTabs ? 'xl:pb-2' : ''}`}>{controls}</div>
      ) : null}
    </div>
  );
}
