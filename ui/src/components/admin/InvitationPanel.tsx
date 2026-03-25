import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { invitations, type Invitation } from '../../lib/api';
import Modal from '../Modal';
import { useToast } from '../ToastProvider';

type TargetType = 'organization' | 'team';

type OptionItem = {
  organization_id?: string;
  organization_name?: string | null;
  team_id?: string;
  team_alias?: string | null;
};

interface InvitationPanelProps {
  orgList: OptionItem[];
  teamList: OptionItem[];
  initialOrganizationId?: string | null;
  initialTeamId?: string | null;
}

const ORGANIZATION_ROLES = [
  { value: 'org_member', label: 'Member' },
  { value: 'org_owner', label: 'Owner' },
  { value: 'org_admin', label: 'Admin' },
  { value: 'org_billing', label: 'Billing' },
  { value: 'org_auditor', label: 'Auditor' },
] as const;

const TEAM_ROLES = [
  { value: 'team_admin', label: 'Admin' },
  { value: 'team_developer', label: 'Developer' },
  { value: 'team_viewer', label: 'Viewer' },
] as const;

const STATUS_STYLES: Record<Invitation['status'], string> = {
  pending: 'bg-amber-100 text-amber-800',
  sent: 'bg-blue-100 text-blue-800',
  accepted: 'bg-green-100 text-green-800',
  cancelled: 'bg-gray-100 text-gray-700',
  expired: 'bg-red-100 text-red-700',
};

function formatDate(value?: string | null): string {
  if (!value) return '—';
  return new Date(value).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function describeScope(invitation: Invitation): string {
  const organizationInvite = Array.isArray(invitation.metadata?.organization_invites)
    ? invitation.metadata?.organization_invites[0] as Record<string, unknown> | undefined
    : undefined;
  if (organizationInvite) {
    return `${organizationInvite.organization_name || organizationInvite.organization_id} (${organizationInvite.role || 'member'})`;
  }
  const teamInvite = Array.isArray(invitation.metadata?.team_invites)
    ? invitation.metadata?.team_invites[0] as Record<string, unknown> | undefined
    : undefined;
  if (teamInvite) {
    return `${teamInvite.team_alias || teamInvite.team_id} (${teamInvite.role || 'viewer'})`;
  }
  return invitation.invite_scope_type;
}

export default function InvitationPanel({
  orgList,
  teamList,
  initialOrganizationId,
  initialTeamId,
}: InvitationPanelProps) {
  const { pushToast } = useToast();
  const [items, setItems] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('active');
  const [email, setEmail] = useState('');
  const [targetType, setTargetType] = useState<TargetType>(initialTeamId ? 'team' : 'organization');
  const [organizationId, setOrganizationId] = useState(initialOrganizationId || '');
  const [teamId, setTeamId] = useState(initialTeamId || '');
  const [organizationRole, setOrganizationRole] = useState<string>('org_member');
  const [teamRole, setTeamRole] = useState<string>('team_viewer');

  const loadInvitations = async () => {
    setLoading(true);
    setError('');
    try {
      const normalizedStatus = statusFilter === 'active' ? undefined : statusFilter;
      const response = await invitations.list({
        status: normalizedStatus,
        search: search.trim() || undefined,
        limit: 20,
        offset: 0,
      });
      let nextItems = response.data || [];
      if (statusFilter === 'active') {
        nextItems = nextItems.filter((item) => item.status === 'pending' || item.status === 'sent');
      }
      setItems(nextItems);
    } catch (err: any) {
      setError(err?.message || 'Failed to load invitations');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadInvitations();
  }, [search, statusFilter]);

  const resetCreateForm = () => {
    setEmail('');
    setTargetType(initialTeamId ? 'team' : 'organization');
    setOrganizationId(initialOrganizationId || orgList[0]?.organization_id || '');
    setTeamId(initialTeamId || teamList[0]?.team_id || '');
    setOrganizationRole('org_member');
    setTeamRole('team_viewer');
    setError('');
  };

  const openCreateModal = () => {
    resetCreateForm();
    setShowCreateModal(true);
  };

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim()) {
      setError('Email is required');
      return;
    }
    if (targetType === 'organization' && !organizationId) {
      setError('Select an organization');
      return;
    }
    if (targetType === 'team' && !teamId) {
      setError('Select a team');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await invitations.create(
        targetType === 'organization'
          ? { email: email.trim(), organization_id: organizationId, organization_role: organizationRole }
          : { email: email.trim(), team_id: teamId, team_role: teamRole }
      );
      setShowCreateModal(false);
      pushToast({ tone: 'success', message: 'Invitation queued for delivery.' });
      await loadInvitations();
    } catch (err: any) {
      setError(err?.message || 'Failed to create invitation');
    } finally {
      setSaving(false);
    }
  };

  const handleResend = async (invitationId: string) => {
    setSaving(true);
    setError('');
    try {
      await invitations.resend(invitationId);
      pushToast({ tone: 'success', message: 'Invitation resent.' });
      await loadInvitations();
    } catch (err: any) {
      setError(err?.message || 'Failed to resend invitation');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = async (invitationId: string) => {
    if (!window.confirm('Cancel this invitation?')) return;
    setSaving(true);
    setError('');
    try {
      await invitations.cancel(invitationId);
      pushToast({ tone: 'success', message: 'Invitation cancelled.' });
      await loadInvitations();
    } catch (err: any) {
      setError(err?.message || 'Failed to cancel invitation');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-col gap-3 border-b border-gray-200 px-5 py-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Pending Invitations</h3>
          <p className="mt-1 text-sm text-gray-500">Invite people by email before they activate their account.</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            type="text"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search invitations..."
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500 sm:w-56"
          />
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="active">Active</option>
            <option value="sent">Sent</option>
            <option value="pending">Pending</option>
            <option value="accepted">Accepted</option>
            <option value="cancelled">Cancelled</option>
            <option value="expired">Expired</option>
          </select>
          <button
            type="button"
            onClick={openCreateModal}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
          >
            Invite by Email
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-5 mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
        </div>
      ) : items.length === 0 ? (
        <div className="px-5 py-10 text-center text-sm text-gray-500">
          No invitations match the current filter.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Email</th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Scope</th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Status</th>
                <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Expires</th>
                <th className="px-5 py-3 text-right text-xs font-semibold text-gray-500">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => {
                const isActive = item.status === 'pending' || item.status === 'sent';
                return (
                  <tr
                    key={item.invitation_id}
                    className={index < items.length - 1 ? 'border-b border-gray-100' : undefined}
                  >
                    <td className="px-5 py-3.5">
                      <div>
                        <p className="font-medium text-gray-900">{item.email}</p>
                        {item.inviter_email && <p className="text-xs text-gray-400">Invited by {item.inviter_email}</p>}
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-gray-700">{describeScope(item)}</td>
                    <td className="px-5 py-3.5">
                      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[item.status]}`}>
                        {item.status}
                      </span>
                    </td>
                    <td className="px-5 py-3.5 text-gray-600">{formatDate(item.expires_at)}</td>
                    <td className="px-5 py-3.5 text-right">
                      <div className="flex items-center justify-end gap-3">
                        <button
                          type="button"
                          onClick={() => handleResend(item.invitation_id)}
                          disabled={!isActive || saving}
                          className="text-sm font-medium text-blue-600 hover:text-blue-800 disabled:cursor-not-allowed disabled:text-gray-300"
                        >
                          Resend
                        </button>
                        <button
                          type="button"
                          onClick={() => handleCancel(item.invitation_id)}
                          disabled={!isActive || saving}
                          className="text-sm font-medium text-red-600 hover:text-red-800 disabled:cursor-not-allowed disabled:text-gray-300"
                        >
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Modal open={showCreateModal} onClose={() => setShowCreateModal(false)} title="Invite User by Email">
        <form onSubmit={handleCreate} className="space-y-4">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">Email</label>
            <input
              type="email"
              value={email}
              onChange={(event) => { setEmail(event.target.value); setError(''); }}
              placeholder="user@example.com"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
              autoComplete="email"
              data-autofocus="true"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">Scope</label>
            <select
              value={targetType}
              onChange={(event) => {
                const nextType = event.target.value as TargetType;
                setTargetType(nextType);
                setError('');
              }}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="organization">Organization</option>
              <option value="team">Team</option>
            </select>
          </div>
          {targetType === 'organization' ? (
            <>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-gray-700">Organization</label>
                <select
                  value={organizationId}
                  onChange={(event) => setOrganizationId(event.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {orgList.map((item) => (
                    <option key={item.organization_id} value={item.organization_id}>
                      {item.organization_name || item.organization_id}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-gray-700">Role</label>
                <select
                  value={organizationRole}
                  onChange={(event) => setOrganizationRole(event.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {ORGANIZATION_ROLES.map((role) => (
                    <option key={role.value} value={role.value}>
                      {role.label}
                    </option>
                  ))}
                </select>
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-gray-700">Team</label>
                <select
                  value={teamId}
                  onChange={(event) => setTeamId(event.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {teamList.map((item) => (
                    <option key={item.team_id} value={item.team_id}>
                      {item.team_alias || item.team_id}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-gray-700">Role</label>
                <select
                  value={teamRole}
                  onChange={(event) => setTeamRole(event.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {TEAM_ROLES.map((role) => (
                    <option key={role.value} value={role.value}>
                      {role.label}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}
          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => setShowCreateModal(false)}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? 'Sending…' : 'Send Invitation'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
