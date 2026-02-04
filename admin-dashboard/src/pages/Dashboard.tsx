import { useNavigate } from 'react-router-dom';
import {
  Building2,
  Users,
  CreditCard,
  TrendingUp,
  ArrowRight,
  Loader2,
} from 'lucide-react';
import { useOrganizations } from '@/hooks/useOrganizations';
import { useTeams } from '@/hooks/useTeams';
import { useSpendSummary } from '@/hooks/useBudget';
import { BudgetProgress } from '@/components/BudgetProgress';

export function Dashboard() {
  const navigate = useNavigate();
  const { data: organizations, isLoading: orgsLoading } = useOrganizations();
  const { data: teams, isLoading: teamsLoading } = useTeams();
  const { data: summary, isLoading: summaryLoading } = useSpendSummary({ days: 30 });

  const isLoading = orgsLoading || teamsLoading || summaryLoading;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    );
  }

  const stats = [
    {
      label: 'Organizations',
      value: organizations?.items.length || 0,
      icon: Building2,
      color: 'bg-blue-500',
      link: '/organizations',
    },
    {
      label: 'Teams',
      value: teams?.items.length || 0,
      icon: Users,
      color: 'bg-green-500',
      link: '/teams',
    },
    {
      label: '30-Day Spend',
      value: summary ? `$${summary.total_spend.toFixed(2)}` : '$0.00',
      icon: CreditCard,
      color: 'bg-purple-500',
      link: '/budget',
    },
    {
      label: 'Total Requests',
      value: summary?.total_requests || 0,
      icon: TrendingUp,
      color: 'bg-orange-500',
      link: '/budget',
    },
  ];

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-600 mt-1">
          Overview of your DeltaLLM usage and organizations
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <div
              key={stat.label}
              onClick={() => navigate(stat.link)}
              className="bg-white rounded-xl p-6 border border-gray-200 cursor-pointer hover:shadow-md transition-shadow"
            >
              <div className="flex items-center justify-between mb-4">
                <div className={`p-3 rounded-lg ${stat.color} bg-opacity-10`}>
                  <Icon className={`w-6 h-6 ${stat.color.replace('bg-', 'text-')}`} />
                </div>
                <ArrowRight className="w-5 h-5 text-gray-400" />
              </div>
              <p className="text-2xl font-bold text-gray-900">{stat.value}</p>
              <p className="text-sm text-gray-600">{stat.label}</p>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Organizations */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">Organizations</h2>
            <button
              onClick={() => navigate('/organizations')}
              className="text-sm text-primary-600 hover:text-primary-700 font-medium"
            >
              View all
            </button>
          </div>
          <div className="divide-y divide-gray-200">
            {organizations?.items.slice(0, 5).map((org) => (
              <div
                key={org.id}
                onClick={() => navigate(`/organizations/${org.id}`)}
                className="px-6 py-4 hover:bg-gray-50 cursor-pointer transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-medium text-gray-900">{org.name}</h3>
                  <span className="text-sm text-gray-500">
                    {org.member_count || 0} members
                  </span>
                </div>
                {org.max_budget !== undefined && (
                  <BudgetProgress
                    budget={{
                      entity_type: 'organization',
                      entity_id: org.id,
                      entity_name: org.name,
                      max_budget: org.max_budget,
                      current_spend: org.spend,
                      remaining_budget: org.max_budget !== undefined
                        ? org.max_budget - org.spend
                        : undefined,
                      budget_utilization_percent: org.max_budget
                        ? (org.spend / org.max_budget) * 100
                        : undefined,
                      is_exceeded: org.max_budget !== undefined
                        ? org.spend >= org.max_budget
                        : false,
                    }}
                    size="sm"
                  />
                )}
              </div>
            ))}
            {organizations?.items.length === 0 && (
              <div className="px-6 py-8 text-center text-gray-500">
                No organizations yet. Create your first one to get started.
              </div>
            )}
          </div>
        </div>

        {/* Top Models */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Top Models (30 days)</h2>
          </div>
          <div className="divide-y divide-gray-200">
            {summary?.top_models?.map((model) => (
              <div
                key={model.model}
                className="px-6 py-4 flex items-center justify-between"
              >
                <div>
                  <p className="font-medium text-gray-900">{model.model}</p>
                  <p className="text-sm text-gray-500">{model.requests} requests</p>
                </div>
                <p className="font-medium text-gray-900">
                  ${model.spend.toFixed(2)}
                </p>
              </div>
            ))}
            {(!summary?.top_models || summary.top_models.length === 0) && (
              <div className="px-6 py-8 text-center text-gray-500">
                No usage data yet.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
