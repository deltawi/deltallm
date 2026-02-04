import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Users, Trash2, Loader2, AlertCircle } from 'lucide-react';
import { useTeams, useCreateTeam, useDeleteTeam } from '@/hooks/useTeams';
import { useOrganizations } from '@/hooks/useOrganizations';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import { BudgetProgress } from '@/components/BudgetProgress';
import type { Team, CreateTeamRequest } from '@/types';

export function Teams() {
  const navigate = useNavigate();
  const { data: teams, isLoading, error, refetch } = useTeams();
  const { data: organizations } = useOrganizations();
  const createTeam = useCreateTeam();
  const deleteTeam = useDeleteTeam();

  const [searchQuery, setSearchQuery] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [formData, setFormData] = useState<CreateTeamRequest>({
    name: '',
    slug: '',
    org_id: '',
    description: '',
  });

  const filteredTeams = teams?.items.filter(
    (team) =>
      team.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      team.slug.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createTeam.mutateAsync(formData);
      setIsModalOpen(false);
      setFormData({ name: '', slug: '', org_id: '', description: '' });
    } catch (error) {
      // Error handled by mutation
    }
  };

  const generateSlug = (name: string) => {
    return name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '');
  };

  const columns = [
    {
      key: 'name',
      header: 'Team',
      render: (team: Team) => (
        <div className="flex items-center">
          <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center mr-3">
            <Users className="w-5 h-5 text-green-600" />
          </div>
          <div>
            <p className="font-medium text-gray-900">{team.name}</p>
            <p className="text-sm text-gray-500">{team.slug}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'organization',
      header: 'Organization',
      render: (team: Team) => (
        <span className="text-sm text-gray-600">
          {team.organization?.name || 'Unknown'}
        </span>
      ),
    },
    {
      key: 'budget',
      header: 'Budget',
      render: (team: Team) =>
        team.max_budget !== undefined ? (
          <BudgetProgress
            budget={{
              entity_type: 'team',
              entity_id: team.id,
              entity_name: team.name,
              max_budget: team.max_budget,
              current_spend: team.spend,
              remaining_budget: team.max_budget - team.spend,
              budget_utilization_percent: (team.spend / team.max_budget) * 100,
              is_exceeded: team.spend >= team.max_budget,
            }}
            size="sm"
          />
        ) : (
          <span className="text-gray-500 text-sm">No budget set</span>
        ),
    },
    {
      key: 'members',
      header: 'Members',
      render: (team: Team) => (
        <span className="text-sm text-gray-600">
          {team.member_count || 0} members
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (team: Team) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            if (confirm(`Are you sure you want to delete "${team.name}"?`)) {
              deleteTeam.mutate(team.id);
            }
          }}
          className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
          title="Delete team"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      ),
    },
  ];

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Teams</h1>
          <p className="text-gray-600 mt-1">
            Manage teams within your organizations
          </p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium"
        >
          <Plus className="w-4 h-4 mr-2" />
          New Team
        </button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search teams..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center justify-between">
          <div className="flex items-center">
            <AlertCircle className="w-5 h-5 text-red-600 mr-2" />
            <span className="text-red-700">Failed to load teams. Please try again.</span>
          </div>
          <button
            onClick={() => refetch()}
            className="px-3 py-1 text-sm bg-red-100 text-red-700 rounded hover:bg-red-200 transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading State */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-primary-600 animate-spin" />
          <span className="ml-2 text-gray-600">Loading teams...</span>
        </div>
      ) : (
        /* Teams Table */
        <DataTable
          columns={columns}
          data={filteredTeams || []}
          keyExtractor={(team) => team.id}
          onRowClick={(team) => navigate(`/teams/${team.id}`)}
          emptyMessage="No teams found. Create your first team to get started."
        />
      )}

      {/* Create Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title="Create Team"
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Organization
            </label>
            <select
              required
              value={formData.org_id}
              onChange={(e) => setFormData({ ...formData, org_id: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            >
              <option value="">Select organization...</option>
              {organizations?.items.map((org) => (
                <option key={org.id} value={org.id}>
                  {org.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Team Name
            </label>
            <input
              type="text"
              required
              value={formData.name}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  name: e.target.value,
                  slug: generateSlug(e.target.value),
                })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              placeholder="Engineering"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Slug
            </label>
            <input
              type="text"
              required
              value={formData.slug}
              onChange={(e) =>
                setFormData({ ...formData, slug: e.target.value })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              placeholder="engineering"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Description
            </label>
            <textarea
              value={formData.description}
              onChange={(e) =>
                setFormData({ ...formData, description: e.target.value })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              rows={3}
              placeholder="Optional description"
            />
          </div>

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={() => setIsModalOpen(false)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createTeam.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {createTeam.isPending ? 'Creating...' : 'Create Team'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
