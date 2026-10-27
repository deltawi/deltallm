import type { ReactNode } from 'react';

// A titled card on a model detail tab. Rows go in KeyValueList, or any body in children.
export function DetailSection({ title, description, action, children }: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="min-w-0 rounded-xl border border-gray-200 bg-white shadow-sm">
      <header className={`flex flex-wrap items-center justify-between gap-3 px-4 py-3 ${children ? 'border-b border-gray-100' : ''}`}>
        <div className="min-w-0 flex-1 basis-56">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {description ? <p className="mt-0.5 text-xs text-gray-500">{description}</p> : null}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

export type KeyValueRow = { label: string; value: ReactNode; mono?: boolean };

export function KeyValueList({ rows }: { rows: KeyValueRow[] }) {
  return (
    <dl className="divide-y divide-gray-100">
      {rows.map((row) => (
        <div key={row.label} className="grid gap-0.5 px-4 py-2 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4">
          <dt className="text-xs text-gray-500 sm:py-0.5">{row.label}</dt>
          <dd className={`min-w-0 break-words text-gray-900 ${row.mono ? 'font-mono text-xs sm:py-0.5' : 'text-[13px]'}`}>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Chip({ children }: { children: ReactNode }) {
  return <span className="inline-flex items-center rounded-md border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-xs text-gray-700">{children}</span>;
}

export function ChipList({ items, empty = 'None' }: { items: ReactNode[]; empty?: string }) {
  if (items.length === 0) return <span className="text-gray-500">{empty}</span>;
  return <span className="flex flex-wrap gap-1.5">{items}</span>;
}

export function Muted({ children }: { children: ReactNode }) {
  return <span className="text-gray-500">{children}</span>;
}
