import type { BudgetStatus } from '@/types';

interface BudgetProgressProps {
  budget: BudgetStatus;
  size?: 'sm' | 'md' | 'lg';
}

export function BudgetProgress({ budget, size = 'md' }: BudgetProgressProps) {
  const utilization = budget.budget_utilization_percent || 0;
  const isWarning = utilization >= 75 && utilization < 90;
  const isDanger = utilization >= 90 || budget.is_exceeded;
  
  const heightClass = size === 'sm' ? 'h-2' : size === 'lg' ? 'h-4' : 'h-3';
  
  const getBarColor = () => {
    if (isDanger) return 'bg-red-500';
    if (isWarning) return 'bg-yellow-500';
    return 'bg-green-500';
  };

  const formatCurrency = (value?: number) => {
    if (value === undefined) return '$0.00';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
    }).format(value);
  };

  return (
    <div className="space-y-2">
      <div className="flex justify-between text-sm">
        <span className="text-gray-600">
          {formatCurrency(budget.current_spend)} spent
        </span>
        <span className="text-gray-600">
          {budget.max_budget ? formatCurrency(budget.max_budget) : 'Unlimited'}
        </span>
      </div>
      
      <div className={`w-full ${heightClass} bg-gray-200 rounded-full overflow-hidden`}>
        <div
          className={`${heightClass} ${getBarColor()} rounded-full transition-all duration-300`}
          style={{ width: `${Math.min(utilization, 100)}%` }}
        />
      </div>
      
      <div className="flex justify-between text-xs text-gray-500">
        <span>
          {budget.remaining_budget !== undefined
            ? `${formatCurrency(budget.remaining_budget)} remaining`
            : 'No limit set'}
        </span>
        {budget.max_budget && (
          <span className={isDanger ? 'text-red-600 font-medium' : ''}>
            {utilization.toFixed(1)}% used
          </span>
        )}
      </div>
    </div>
  );
}
