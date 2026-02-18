import clsx from 'clsx';

interface StatusBadgeProps {
  status: 'healthy' | 'unhealthy' | 'active' | 'expired' | 'blocked' | string;
  label?: string;
}

const colorMap: Record<string, string> = {
  healthy: 'bg-green-100 text-green-700',
  active: 'bg-green-100 text-green-700',
  unhealthy: 'bg-red-100 text-red-700',
  expired: 'bg-gray-100 text-gray-600',
  blocked: 'bg-red-100 text-red-700',
  enabled: 'bg-green-100 text-green-700',
  disabled: 'bg-gray-100 text-gray-600',
  warning: 'bg-yellow-100 text-yellow-700',
};

export default function StatusBadge({ status, label }: StatusBadgeProps) {
  const color = colorMap[status] || 'bg-gray-100 text-gray-600';
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium', color)}>
      {label || status}
    </span>
  );
}
