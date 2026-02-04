import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Building2, Trash2, Loader2, AlertCircle } from 'lucide-react';
import { useOrganizations, useCreateOrganization, useDeleteOrganization } from '@/hooks/useOrganizations';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import { BudgetProgress } from '@/components/BudgetProgress';
import type { Organization, CreateOrganizationRequest } from '@/types';

export function Organizations() {
  const navigate = useNavigate();
  const { data: organizations, isLoading, isFetching, isPending, isError, error, status, refetch } = useOrganizations();
  const createOrg = useCreateOrganization();
  const deleteOrg = useDeleteOrganization();

  console.log('[Organizations] Render state:', { isLoading, isFetching, isPending, isError, status, hasData: !!organizations });
  const [searchQuery, setSearchQuery] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [formData, setFormData] = useState<CreateOrganizationRequest>({
    name: '',
    slug: '',
    description: '',
  });

  const filteredOrganizations = organizations?.items.filter(
    (org) =>
      org.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      org.slug.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createOrg.mutateAsync(formData);
      setIsModalOpen(false);
      setFormData({ name: '', slug: '', description: '' });
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
      header: 'Organization',
      render: (org: Organization) => (
        <div className="flex items-center">
          <div className="w-10 h-10 rounded-lg bg-primary-100 flex items-center justify-center mr-3">
            <Building2 className="w-5 h-5 text-primary-600" />
          </div>
          <div>
            <p className="font-medium text-gray-900">{org.name}</p>
            <p className="text-sm text-gray-500">{org.slug}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'budget',
      header: 'Budget',
      render: (org: Organization) =>
        org.max_budget !== undefined ? (
          <BudgetProgress
            budget={{
              entity_type: 'organization',
              entity_id: org.id,
              entity_name: org.name,
              max_budget: org.max_budget,
              current_spend: org.spend,
              remaining_budget: org.max_budget - org.spend,
              budget_utilization_percent: (org.spend / org.max_budget) * 100,
              is_exceeded: org.spend >= org.max_budget,
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
      render: (org: Organization) => (
        <span className="text-sm text-gray-600">
          {org.member_count || 0} members
        </span>
      ),
    },
    {
      key: 'teams',
      header: 'Teams',
      render: (org: Organization) => (
        <span className="text-sm text-gray-600">
          {org.team_count || 0} teams
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (org: Organization) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            if (confirm(`Are you sure you want to delete "${org.name}"?`)) {
              deleteOrg.mutate(org.id);
            }
          }}
          className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
          title="Delete organization"
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
          <h1 className="text-2xl font-bold text-gray-900">Organizations</h1>
          <p className="text-gray-600 mt-1">
            Manage your organizations and their settings
          </p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium"
        >
          <Plus className="w-4 h-4 mr-2" />
          New Organization
        </button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search organizations..."
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
            <span className="text-red-700">Failed to load organizations. Please try again.</span>
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
          <span className="ml-2 text-gray-600">Loading organizations...</span>
        </div>
      ) : (
        /* Organizations Table */
        <DataTable
          columns={columns}
          data={filteredOrganizations || []}
          keyExtractor={(org) => org.id}
          onRowClick={(org) => navigate(`/organizations/${org.id}`)}
          emptyMessage="No organizations found. Create your first organization to get started."
        />
      )}

      {/* Create Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title="Create Organization"
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Organization Name
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
              placeholder="My Organization"
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
              placeholder="my-organization"
            />
            <p className="text-xs text-gray-500 mt-1">
              Used in URLs and API calls
            </p>
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
              disabled={createOrg.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {createOrg.isPending ? 'Creating...' : 'Create Organization'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
