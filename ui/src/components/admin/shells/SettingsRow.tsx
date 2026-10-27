import type { ReactNode } from 'react';

// One setting on a detail page's Settings tab: title, one-line description, an action, optional body below.
export default function SettingsRow({ title, description, action, tone = 'default', children }: {
  title: string;
  description: ReactNode;
  action?: ReactNode;
  tone?: 'default' | 'danger';
  children?: ReactNode;
}) {
  return (
    <section className={`rounded-xl border bg-white shadow-sm ${tone === 'danger' ? 'border-red-200' : 'border-gray-200'}`}>
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0 flex-1 basis-64">
          <h3 className={`text-sm font-semibold ${tone === 'danger' ? 'text-red-700' : 'text-gray-900'}`}>{title}</h3>
          <p className="mt-0.5 text-xs text-gray-500">{description}</p>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
