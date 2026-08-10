import type { ReactNode } from 'react';

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: ReactNode;
  trend?: string;
}

export default function StatCard({ title, value, subtitle, icon, trend }: StatCardProps) {
  const valueText = String(value);
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm sm:p-5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-gray-500 sm:text-sm">{title}</p>
          <p className="mt-1 whitespace-nowrap text-base font-bold tabular-nums text-gray-900 min-[380px]:text-lg sm:text-2xl" title={valueText}>{value}</p>
          {subtitle && <p className="text-xs text-gray-400 mt-1">{subtitle}</p>}
          {trend && <p className="text-xs text-green-600 mt-1">{trend}</p>}
        </div>
        {icon && <div className="hidden shrink-0 rounded-lg bg-blue-50 p-2 text-blue-600 sm:block">{icon}</div>}
      </div>
    </div>
  );
}
