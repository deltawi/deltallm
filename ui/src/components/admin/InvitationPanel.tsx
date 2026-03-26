import type { Invitation, Pagination } from '../../lib/api';

interface InvitationPanelProps {
  items: Invitation[];
  loading: boolean;
  saving: boolean;
  error?: string | null;
  pagination: Pagination;
  onPageChange: (offset: number) => void;
  onResend: (invitationId: string) => void;
  onCancel: (invitationId: string) => void;
}

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
  items,
  loading,
  saving,
  error,
  pagination,
  onPageChange,
  onResend,
  onCancel,
}: InvitationPanelProps) {
  const currentPage = Math.floor(pagination.offset / pagination.limit) + 1;
  const totalPages = Math.max(1, Math.ceil((pagination.total || 0) / pagination.limit));
  const hasPrev = pagination.offset > 0;
  const hasNext = pagination.has_more;

  return (
    <>
      {error ? (
        <div className="mx-4 mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
        </div>
      ) : items.length === 0 ? (
        <div className="px-5 py-14 text-center text-sm text-gray-500">
          No invitations match the current filter.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Email</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Scope</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Expires</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-gray-400">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => {
                const isActive = item.status === 'pending' || item.status === 'sent';
                return (
                  <tr
                    key={item.invitation_id}
                    className={`border-b border-gray-100 ${index === items.length - 1 ? 'border-b-0' : ''}`}
                  >
                    <td className="px-4 py-3.5">
                      <div>
                        <p className="font-medium text-gray-900">{item.email}</p>
                        {item.inviter_email ? (
                          <p className="text-xs text-gray-400">Invited by {item.inviter_email}</p>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-gray-700">{describeScope(item)}</td>
                    <td className="px-4 py-3.5">
                      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[item.status]}`}>
                        {item.status}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-gray-600">{formatDate(item.expires_at)}</td>
                    <td className="px-4 py-3.5 text-right">
                      <div className="flex items-center justify-end gap-3">
                        <button
                          type="button"
                          onClick={() => onResend(item.invitation_id)}
                          disabled={!isActive || saving}
                          className="text-sm font-medium text-blue-600 hover:text-blue-800 disabled:cursor-not-allowed disabled:text-gray-300"
                        >
                          Resend
                        </button>
                        <button
                          type="button"
                          onClick={() => onCancel(item.invitation_id)}
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

      <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-4 py-3">
        <span className="text-xs text-gray-500">
          {loading
            ? 'Loading…'
            : `Showing ${Math.min(pagination.offset + 1, pagination.total || 0)}–${Math.min(pagination.offset + items.length, pagination.total || 0)} of ${pagination.total}`}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">
            Page {currentPage} of {totalPages}
          </span>
          <button
            onClick={() => onPageChange(Math.max(0, pagination.offset - pagination.limit))}
            disabled={!hasPrev || loading}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Previous
          </button>
          <button
            onClick={() => onPageChange(pagination.offset + pagination.limit)}
            disabled={!hasNext || loading}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Next
          </button>
        </div>
      </div>
    </>
  );
}
