import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import ManagedAssetAccessFields from '../ManagedAssetAccessFields';
import Modal from '../Modal';
import { useToast } from '../ToastProvider';
import MCPServerForm from './MCPServerForm';
import {
  buildMCPServerPayload,
  EMPTY_MCP_SERVER_FORM,
  type MCPServerFormValues,
} from '../../lib/mcpServerForm';
import { mcpServers, managedAssetAccessInput, type ManagedAssetGrantInput } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { isPlatformAdminSession } from '../../lib/authorization';
import { errorMessage } from '../../lib/errorMessage';
import { useManagedAssetAudienceOptions } from '../../lib/useManagedAssetAudienceOptions';

type MCPServerCreateModalProps = {
  open: boolean;
  onClose: () => void;
};

export default function MCPServerCreateModal({ open, onClose }: MCPServerCreateModalProps) {
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const { pushToast } = useToast();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const { teamOptions, organizationOptions, loading: audiencesLoading, error: audiencesError, refetch: refetchAudiences } = useManagedAssetAudienceOptions(
    session,
    isPlatformAdmin,
  );
  const memberships = session?.organization_memberships;
  const ownerScopeOptions = useMemo(
    () => (memberships || [])
      .map((membership) => String(membership.organization_id || ''))
      .filter(Boolean)
      .map((organizationId) => ({ value: organizationId, label: organizationId })),
    [memberships],
  );

  const [accessError, setAccessError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<MCPServerFormValues>({ ...EMPTY_MCP_SERVER_FORM });
  const [grants, setGrants] = useState<ManagedAssetGrantInput[]>([]);

  const reset = () => {
    setForm({ ...EMPTY_MCP_SERVER_FORM });
    setGrants([]);
    setAccessError(null);
  };

  const handleCreate = async () => {
    if (creating) return;
    setAccessError(null);
    setCreating(true);
    try {
      const created = await mcpServers.create({
        ...buildMCPServerPayload(form),
        access: managedAssetAccessInput(grants),
      });
      onClose();
      reset();
      pushToast({
        tone: 'success',
        title: 'MCP server created',
        message: `Server "${created.server_key}" is ready for capability refresh.`,
      });
      navigate(`/mcp-servers/${created.mcp_server_id}`);
    } catch (error) {
      pushToast({ tone: 'error', title: 'Create failed', message: errorMessage(error, 'Failed to create MCP server.') });
    } finally {
      setCreating(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Register MCP Server" wide>
      <div className="space-y-5">
        <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
          <div className="font-semibold">What happens next</div>
          <div className="mt-1 text-blue-800">
            This registers the server record only. On the detail page you can run health checks, refresh tool discovery,
            and add bindings or tool policies.
          </div>
        </div>
        <MCPServerForm
          value={form}
          onChange={setForm}
          ownerScopeOptions={ownerScopeOptions}
          showOwnerScope={isPlatformAdmin}
        />
        <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
          <ManagedAssetAccessFields
            grants={grants}
            teamOptions={teamOptions}
            organizationOptions={organizationOptions}
            allowPublic={isPlatformAdmin}
            audiencesLoading={audiencesLoading}
            audiencesError={audiencesError}
            onRetryAudiences={refetchAudiences}
            error={accessError}
            onChange={(nextGrants) => { setGrants(nextGrants); setAccessError(null); }}
          />
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={() => {
              onClose();
              reset();
            }}
            className="control-md rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => { void handleCreate(); }}
            disabled={creating}
            className="control-md rounded-lg bg-brand-primary font-medium text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
          >
            {creating ? 'Creating…' : 'Create Server'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
