import type { ReactNode } from 'react';

type RecordDetailShellProps = {
  backAction?: ReactNode;
  header: ReactNode;
  intro?: ReactNode;
  children: ReactNode;
  containerClassName?: string;
};

export default function RecordDetailShell({
  backAction,
  header,
  intro,
  children,
  containerClassName = 'mx-auto max-w-7xl space-y-6 p-4 sm:p-6',
}: RecordDetailShellProps) {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className={containerClassName}>
        {backAction}
        {header}
        {intro}
        {children}
      </div>
    </div>
  );
}
