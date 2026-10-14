import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import PageShell from './PageShell';
import type { SummaryItem } from './primitives';

type IndexShellProps = {
  title: string;
  titleIcon?: LucideIcon;
  count?: number | null;
  description?: ReactNode;
  eyebrowPrefix?: ReactNode;
  eyebrowCurrent?: ReactNode;
  action?: ReactNode;
  summary?: ReactNode;
  toolbar?: ReactNode;
  tabs?: ReactNode;
  summaryItems?: SummaryItem[];
  notice?: ReactNode;
  children: ReactNode;
};

// Top-level and list pages: PageShell with the menu section as the breadcrumb.
export default function IndexShell({
  title,
  titleIcon,
  eyebrowPrefix,
  eyebrowCurrent,
  action,
  ...rest
}: IndexShellProps) {
  const trail = [
    ...(eyebrowPrefix ? [{ label: eyebrowPrefix }] : []),
    { label: eyebrowCurrent || title },
  ];
  return <PageShell {...rest} title={title} icon={titleIcon} breadcrumbs={trail} actions={action} />;
}
