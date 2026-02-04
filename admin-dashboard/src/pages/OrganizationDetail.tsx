import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Users,
  CreditCard,
  Settings,
  Edit,
  Plus,
  Loader2,
  X,
} from 'lucide-react';
import { useOrganization, useUpdateOrganization, useOrgMembers, useAddOrgMember, useRemoveOrgMember, useUpdateOrgMemberRole } from '@/hooks/useOrganizations';
import { useOrgBudgetFull, useSetOrgBudget } from '@/hooks/useBudget';
import { useTeams } from '@/hooks/useTeams';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import { BudgetProgress } from '@/components/BudgetProgress';
import type { OrgMember, Team, BudgetStatus } from '@/types';

export function OrganizationDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const orgId = id!;

  const { data: organization, isLoading: orgLoading } = useOrganization(orgId);
  const { data: budget, isLoading: budgetLoading } = useOrgBudgetFull(orgId);
  const { data: membersData, isLoading: membersLoading } = useOrgMembers(orgId);
  const members = membersData?.items || [];
  const { data: teamsData } = useTeams(orgId);
  const teams = teamsData?.items || [];

  const updateOrg = useUpdateOrganization(orgId);
  const setBudget = useSetOrgBudget(orgId);
  const addMember = useAddOrgMember(orgId);
  const removeMember = useRemoveOrgMember(orgId);
  const updateMemberRole = useUpdateOrgMemberRole(orgId);

  const [activeTab, setActiveTab] = useState<'overview' | 'members' | 'budget'>('overview');
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isBudgetModalOpen, setIsBudgetModalOpen] = useState(false);
  const [isAddMemberModalOpen, setIsAddMemberModalOpen] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', description: '' });
  const [budgetValue, setBudgetValue] = useState('');
  const [newMember, setNewMember] = useState({ user_id: '', role: 'member' });

  if (orgLoading || budgetLoading || membersLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    );
  }

  if (!organization) {
    return (
      <div className="p-8">
        <p className="text-gray-600">Organization not found</p>
      </div>
    );
  }

  const memberColumns = [
    {
      key: 'user',
      header: 'User',
      render: (member: OrgMember) => (
        <div className="flex items-center">
          <div className="w-10 h-10 rounded-full bg-gray-200 flex items-center justify-center">
            <span className="text-gray-600 font-medium">
              {member.user.first_name?.[0] || member.user.email[0].toUpperCase()}
            </span>
          </div>
          <div className="ml-3">
            <p className="font-medium text-gray-900">
              {member.user.first_name} {member.user.last_name}
            </p>
            <p className="text-sm text-gray-500">{member.user.email}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'role',
      header: 'Role',
      render: (member: OrgMember) => (
        <select
          value={member.role}
          onChange={(e) => updateMemberRole.mutate({ userId: member.user_id, role: e.target.value })}
          className="px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
        >
          <option value="owner">Owner</option>
          <option value="admin">Admin</option>
          <option value="member">Member</option>
          <option value="viewer">Viewer</option>
        </select>
      ),
    },
    {
      key: 'joined',
      header: 'Joined',
      render: (member: OrgMember) => (
        <span className="text-sm text-gray-500">
          {new Date(member.joined_at).toLocaleDateString()}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (member: OrgMember) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            if (confirm('Are you sure you want to remove this member?')) {
              removeMember.mutate(member.user_id);
            }
          }}
          className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
          title="Remove member"
        >
          <X className="w-4 h-4" />
        </button>
      ),
    },
  ];

  const teamColumns = [
    {
      key: 'name',
      header: 'Team',
      render: (team: Team) => (
        <div>
          <p className="font-medium text-gray-900">{team.name}</p>
          <p className="text-sm text-gray-500">{team.slug}</p>
        </div>
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
  ];

  const handleUpdate = async () => {
    await updateOrg.mutateAsync(editForm);
    setIsEditModalOpen(false);
  };

  const handleSetBudget = async () => {
    const value = parseFloat(budgetValue);
    if (!isNaN(value) && value > 0) {
      await setBudget.mutateAsync(value);
      setIsBudgetModalOpen(false);
      setBudgetValue('');
    }
  };

  const handleAddMember = async () => {
    if (newMember.user_id) {
      await addMember.mutateAsync({
        user_id: newMember.user_id,
        role: newMember.role,
      });
      setIsAddMemberModalOpen(false);
      setNewMember({ user_id: '', role: 'member' });
    }
  };

  return (
    <div className="p-8">
      {/* Back button */}
      <button
        onClick={() => navigate('/organizations')}
        className="flex items-center text-gray-600 hover:text-gray-900 mb-4"
      >
        <ArrowLeft className="w-4 h-4 mr-1" />
        Back to Organizations
      </button>

      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{organization.name}</h1>
          <p className="text-gray-600 mt-1">{organization.description}</p>
          <p className="text-sm text-gray-500 mt-1">/{organization.slug}</p>
        </div>
        <div className="flex space-x-2">
          <button
            onClick={() => {
              setEditForm({
                name: organization.name,
                description: organization.description || '',
              });
              setIsEditModalOpen(true);
            }}
            className="flex items-center px-4 py-2 text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            <Edit className="w-4 h-4 mr-2" />
            Edit
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="flex space-x-8">
          {[
            { key: 'overview', label: 'Overview', icon: Settings },
            { key: 'members', label: 'Members', icon: Users },
            { key: 'budget', label: 'Budget', icon: CreditCard },
          ].map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key as any)}
                className={`flex items-center pb-4 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab.key
                    ? 'border-primary-500 text-primary-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <Icon className="w-4 h-4 mr-2" />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab Content */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Budget Card */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-900">Budget</h3>
              <button
                onClick={() => setIsBudgetModalOpen(true)}
                className="text-sm text-primary-600 hover:text-primary-700 font-medium"
              >
                {organization.max_budget !== undefined ? 'Update' : 'Set Budget'}
              </button>
            </div>
            {budget?.org_budget && (
              <BudgetProgress budget={budget.org_budget} size="lg" />
            )}
          </div>

          {/* Teams Section */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-900">Teams</h3>
              <button
                onClick={() => navigate(`/teams/create?org_id=${orgId}`)}
                className="flex items-center px-3 py-1.5 text-sm text-primary-600 hover:bg-primary-50 rounded-lg"
              >
                <Plus className="w-4 h-4 mr-1" />
                Add Team
              </button>
            </div>
            <DataTable
              columns={teamColumns}
              data={teams}
              keyExtractor={(team) => team.id}
              onRowClick={(team) => navigate(`/teams/${team.id}`)}
            />
          </div>
        </div>
      )}

      {activeTab === 'members' && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-900">
              Members ({members.length})
            </h3>
            <button
              onClick={() => setIsAddMemberModalOpen(true)}
              className="flex items-center px-3 py-1.5 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700"
            >
              <Plus className="w-4 h-4 mr-1" />
              Add Member
            </button>
          </div>
          <DataTable
            columns={memberColumns}
            data={members}
            keyExtractor={(member) => member.id}
          />
        </div>
      )}

      {activeTab === 'budget' && (
        <div className="space-y-6">
          {/* Org Budget */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              Organization Budget
            </h3>
            {budget?.org_budget && <BudgetProgress budget={budget.org_budget} size="lg" />}
          </div>

          {/* Team Budgets */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Team Budgets</h3>
            <div className="space-y-4">
              {budget?.team_budgets.map((teamBudget: BudgetStatus) => (
                <div key={teamBudget.entity_id} className="border-t border-gray-100 pt-4">
                  <p className="font-medium text-gray-900 mb-2">{teamBudget.entity_name}</p>
                  <BudgetProgress budget={teamBudget} />
                </div>
              ))}
              {(!budget?.team_budgets || budget.team_budgets.length === 0) && (
                <p className="text-gray-500 text-center py-4">No teams with budgets</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      <Modal
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        title="Edit Organization"
      >
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Name
            </label>
            <input
              type="text"
              value={editForm.name}
              onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Description
            </label>
            <textarea
              value={editForm.description}
              onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              rows={3}
            />
          </div>
          <div className="flex justify-end space-x-3 pt-4">
            <button
              onClick={() => setIsEditModalOpen(false)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg"
            >
              Cancel
            </button>
            <button
              onClick={handleUpdate}
              disabled={updateOrg.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
            >
              {updateOrg.isPending ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      </Modal>

      {/* Budget Modal */}
      <Modal
        isOpen={isBudgetModalOpen}
        onClose={() => setIsBudgetModalOpen(false)}
        title="Set Budget Limit"
      >
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Maximum Budget (USD)
            </label>
            <input
              type="number"
              min="0"
              step="0.01"
              value={budgetValue}
              onChange={(e) => setBudgetValue(e.target.value)}
              placeholder="1000.00"
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
          </div>
          <div className="flex justify-end space-x-3 pt-4">
            <button
              onClick={() => setIsBudgetModalOpen(false)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg"
            >
              Cancel
            </button>
            <button
              onClick={handleSetBudget}
              disabled={setBudget.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
            >
              {setBudget.isPending ? 'Setting...' : 'Set Budget'}
            </button>
          </div>
        </div>
      </Modal>

      {/* Add Member Modal */}
      <Modal
        isOpen={isAddMemberModalOpen}
        onClose={() => setIsAddMemberModalOpen(false)}
        title="Add Organization Member"
      >
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              User ID
            </label>
            <input
              type="text"
              value={newMember.user_id}
              onChange={(e) => setNewMember({ ...newMember, user_id: e.target.value })}
              placeholder="Enter user ID"
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
            <p className="text-xs text-gray-500 mt-1">
              Enter the user ID of the person you want to add
            </p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Role
            </label>
            <select
              value={newMember.role}
              onChange={(e) => setNewMember({ ...newMember, role: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            >
              <option value="owner">Owner</option>
              <option value="admin">Admin</option>
              <option value="member">Member</option>
              <option value="viewer">Viewer</option>
            </select>
          </div>
          <div className="flex justify-end space-x-3 pt-4">
            <button
              onClick={() => setIsAddMemberModalOpen(false)}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg"
            >
              Cancel
            </button>
            <button
              onClick={handleAddMember}
              disabled={addMember.isPending || !newMember.user_id}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
            >
              {addMember.isPending ? 'Adding...' : 'Add Member'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
