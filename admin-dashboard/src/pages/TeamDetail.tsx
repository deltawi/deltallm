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
  AlertCircle,
} from 'lucide-react';
import { useTeam, useUpdateTeam, useTeamMembers, useAddTeamMember, useRemoveTeamMember, useUpdateTeamMemberRole } from '@/hooks/useTeams';
import { useTeamBudget, useSetTeamBudget } from '@/hooks/useBudget';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import { BudgetProgress } from '@/components/BudgetProgress';
import type { TeamMember } from '@/types';

const TEAM_ROLES = [
  { value: 'admin', label: 'Admin' },
  { value: 'member', label: 'Member' },
];

export function TeamDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const teamId = id!;
  
  // Wrap in try-catch to detect render errors
  try {
    return <TeamDetailContent teamId={teamId} navigate={navigate} />;
  } catch (error) {
    console.error('[TeamDetail] Render error:', error);
    return (
      <div className="p-8">
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
          <h2 className="text-red-700 font-bold mb-2">Render Error</h2>
          <p className="text-red-600">{(error as Error).message}</p>
          <button 
            onClick={() => navigate('/teams')}
            className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
          >
            Back to Teams
          </button>
        </div>
      </div>
    );
  }
}

function TeamDetailContent({ teamId, navigate }: { teamId: string; navigate: ReturnType<typeof useNavigate> }) {
  const { data: team, isLoading: teamLoading, error: teamError } = useTeam(teamId);
  const { data: budget, isLoading: budgetLoading } = useTeamBudget(teamId);
  const { data: membersData, isLoading: membersLoading } = useTeamMembers(teamId);
  const members = membersData?.items || [];

  // Debug logging
  console.log('[TeamDetail] Render - teamId:', teamId, 'teamLoading:', teamLoading, 'team:', team ? 'present' : 'undefined', 'error:', teamError);

  const updateTeam = useUpdateTeam(teamId);
  const setBudget = useSetTeamBudget(teamId);
  const addMember = useAddTeamMember(teamId);
  const removeMember = useRemoveTeamMember(teamId);
  const updateMemberRole = useUpdateTeamMemberRole(teamId);

  const [activeTab, setActiveTab] = useState<'overview' | 'members' | 'budget'>('overview');
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isBudgetModalOpen, setIsBudgetModalOpen] = useState(false);
  const [isAddMemberModalOpen, setIsAddMemberModalOpen] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', description: '' });
  const [budgetValue, setBudgetValue] = useState('');
  const [newMember, setNewMember] = useState({ user_id: '', role: 'member' });

  if (teamLoading || budgetLoading || membersLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    );
  }

  if (teamError) {
    return (
      <div className="p-8">
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-center">
          <AlertCircle className="w-5 h-5 text-red-600 mr-2" />
          <div>
            <p className="text-red-700 font-medium">Failed to load team</p>
            <p className="text-red-600 text-sm">{(teamError as Error).message}</p>
          </div>
        </div>
      </div>
    );
  }

  if (!team) {
    return (
      <div className="p-8">
        <p className="text-gray-600">Team not found</p>
      </div>
    );
  }

  console.log('[TeamDetail] Rendering team:', team.name, 'spend:', team.spend, 'created_at:', team.created_at);

  // Safe accessor for potentially undefined values
  // Note: API returns spend as string "0.0000", need to parse it
  const safeSpend = typeof team.spend === 'string' ? parseFloat(team.spend) : (team.spend ?? 0);
  const safeCreatedAt = team.created_at ? new Date(team.created_at).toLocaleDateString() : 'N/A';
  
  console.log('[TeamDetail] safeSpend:', safeSpend, 'safeCreatedAt:', safeCreatedAt);

  const memberColumns = [
    {
      key: 'user',
      header: 'User',
      render: (member: TeamMember) => (
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
      render: (member: TeamMember) => (
        <select
          value={member.role}
          onChange={(e) => updateMemberRole.mutate({ userId: member.user_id, role: e.target.value })}
          className="px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
        >
          {TEAM_ROLES.map((role) => (
            <option key={role.value} value={role.value}>
              {role.label}
            </option>
          ))}
        </select>
      ),
    },
    {
      key: 'joined',
      header: 'Joined',
      render: (member: TeamMember) => (
        <span className="text-sm text-gray-500">
          {new Date(member.joined_at).toLocaleDateString()}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (member: TeamMember) => (
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

  const handleUpdate = async () => {
    await updateTeam.mutateAsync(editForm);
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

  // Wrap render in try-catch to detect any JSX errors
  try {
    return (
      <div className="p-8">
        {/* Back button */}
        <button
        onClick={() => navigate('/teams')}
        className="flex items-center text-gray-600 hover:text-gray-900 mb-4"
      >
        <ArrowLeft className="w-4 h-4 mr-1" />
        Back to Teams
      </button>

      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{team.name}</h1>
          <p className="text-gray-600 mt-1">{team.description}</p>
          <p className="text-sm text-gray-500 mt-1">/{team.slug}</p>
        </div>
        <div className="flex space-x-2">
          <button
            onClick={() => {
              setEditForm({
                name: team.name,
                description: team.description || '',
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
                {team.max_budget !== undefined ? 'Update' : 'Set Budget'}
              </button>
            </div>
            {budget ? (
              <BudgetProgress budget={budget} size="lg" />
            ) : (
              <p className="text-gray-500">No budget set</p>
            )}
          </div>

          {/* Info Card */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Team Information</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-gray-500">Organization</p>
                <p className="font-medium text-gray-900">{team.organization?.name || 'N/A'}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Members</p>
                <p className="font-medium text-gray-900">{members.length}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Created</p>
                <p className="font-medium text-gray-900">{safeCreatedAt}</p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Current Spend</p>
                <p className="font-medium text-gray-900">${safeSpend.toFixed(2)}</p>
              </div>
            </div>
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
          {/* Team Budget */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-900">Team Budget</h3>
              <button
                onClick={() => setIsBudgetModalOpen(true)}
                className="text-sm text-primary-600 hover:text-primary-700 font-medium"
              >
                {team.max_budget !== undefined ? 'Update' : 'Set Budget'}
              </button>
            </div>
            {budget ? (
              <BudgetProgress budget={budget} size="lg" />
            ) : (
              <p className="text-gray-500">No budget set</p>
            )}
          </div>
        </div>
      )}

      {/* Edit Modal */}
      <Modal
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        title="Edit Team"
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
              disabled={updateTeam.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
            >
              {updateTeam.isPending ? 'Saving...' : 'Save Changes'}
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
        title="Add Team Member"
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
              {TEAM_ROLES.map((role) => (
                <option key={role.value} value={role.value}>
                  {role.label}
                </option>
              ))}
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
  } catch (renderError) {
    console.error('[TeamDetail] JSX Render Error:', renderError);
    return (
      <div className="p-8">
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
          <h2 className="text-red-700 font-bold mb-2">Render Error</h2>
          <p className="text-red-600">{(renderError as Error).message}</p>
          <pre className="mt-2 text-xs text-red-500 bg-red-100 p-2 rounded">{(renderError as Error).stack}</pre>
        </div>
      </div>
    );
  }
}
