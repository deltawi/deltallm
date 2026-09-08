import ConfirmDialog from '../ConfirmDialog';
import type { OrganizationDeletionPlan } from '../../lib/organizationDeletion';

type RequestDialogProps = {
  open: boolean;
  organizationName: string;
  plan: OrganizationDeletionPlan | null;
  confirmationName: string;
  acknowledged: boolean;
  working: boolean;
  onConfirmationNameChange: (value: string) => void;
  onAcknowledgedChange: (value: boolean) => void;
  onConfirm: () => void;
  onClose: () => void;
};

type ExpediteDialogProps = {
  open: boolean;
  organizationName: string;
  confirmationName: string;
  acknowledged: boolean;
  working: boolean;
  onConfirmationNameChange: (value: string) => void;
  onAcknowledgedChange: (value: boolean) => void;
  onConfirm: () => void;
  onClose: () => void;
};

type RestoreDialogProps = {
  open: boolean;
  working: boolean;
  onConfirm: () => void;
  onClose: () => void;
};

function ImpactSummary({ plan }: { plan: OrganizationDeletionPlan }) {
  const counts = plan.counts;
  const removalRows = [
    ['Teams', counts.teams],
    ['API keys', counts.api_keys],
    ['Service accounts', counts.service_accounts],
    ['Memberships', counts.organization_memberships + counts.team_memberships],
    ['Pending invitations', counts.pending_invitations],
    ['Pending approvals', counts.pending_mcp_approvals],
    ['Policy and asset bindings', counts.scope_bindings],
    ['Owned MCP servers', counts.owned_mcp_servers],
    ['Owned prompt templates', counts.owned_prompt_templates],
    ['Owned route groups', counts.owned_route_groups],
    ['Prompt render logs', counts.prompt_render_logs],
    ['Conflicting sensitive records', counts.conflicting_sensitive_records],
    ['Unattributed sensitive records', counts.unattributed_sensitive_records],
    ['Batch records missing ownership', counts.unresolved_batch_ownership_records],
  ] as const;
  return (
    <div className="grid grid-cols-2 gap-2 rounded-lg border border-red-100 bg-red-50/60 p-3">
      {removalRows.map(([label, value]) => (
        <div key={label} className="flex items-center justify-between gap-2 text-xs">
          <span className="text-gray-600">{label}</span>
          <span className="font-semibold text-gray-900">{value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

export function OrganizationDeletionRequestDialog({
  open,
  organizationName,
  plan,
  confirmationName,
  acknowledged,
  working,
  onConfirmationNameChange,
  onAcknowledgedChange,
  onConfirm,
  onClose,
}: RequestDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      title="Delete organization"
      description={`Type “${organizationName}” and acknowledge cancellation to schedule deletion.`}
      confirmLabel="Schedule deletion"
      destructive
      confirming={working}
      confirmDisabled={!plan?.can_request || confirmationName !== organizationName || !acknowledged}
      onConfirm={onConfirm}
      onClose={onClose}
    >
      {plan && <ImpactSummary plan={plan} />}
      <div>
        <label htmlFor="organization-delete-confirmation" className="mb-1 block text-xs font-medium text-gray-700">Organization name</label>
        <input id="organization-delete-confirmation" value={confirmationName} onChange={(event) => onConfirmationNameChange(event.target.value)} autoComplete="off" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-red-500 focus:outline-none focus:ring-2 focus:ring-red-200" />
      </div>
      <label className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
        <input type="checkbox" checked={acknowledged} onChange={(event) => onAcknowledgedChange(event.target.checked)} className="mt-0.5" />
        <span>I understand active batches will be cancelled and restored organizations do not restart cancelled work.</span>
      </label>
      {plan && (
        <p className="text-xs text-gray-500">
          Spend ({plan.counts.retained_spend_events.toLocaleString()}), audit ({plan.counts.retained_audit_events.toLocaleString()}), terminal batch ({plan.counts.retained_batch_jobs.toLocaleString()}), and batch file ({plan.counts.retained_batch_files.toLocaleString()}) records remain under their existing retention periods.
        </p>
      )}
    </ConfirmDialog>
  );
}

export function OrganizationDeletionExpediteDialog({
  open,
  organizationName,
  confirmationName,
  acknowledged,
  working,
  onConfirmationNameChange,
  onAcknowledgedChange,
  onConfirm,
  onClose,
}: ExpediteDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      title="Delete permanently now"
      description={`Type “${organizationName}” to waive the recovery window.`}
      confirmLabel="Waive recovery window"
      destructive
      confirming={working}
      confirmDisabled={confirmationName !== organizationName || !acknowledged}
      onConfirm={onConfirm}
      onClose={onClose}
    >
      <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800">
        Restore will close immediately. Cleanup begins only after active batches stop, and ownership and final-inventory safety checks still apply.
      </p>
      <div>
        <label htmlFor="organization-expedite-confirmation" className="mb-1 block text-xs font-medium text-gray-700">Organization name</label>
        <input id="organization-expedite-confirmation" value={confirmationName} onChange={(event) => onConfirmationNameChange(event.target.value)} autoComplete="off" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-red-500 focus:outline-none focus:ring-2 focus:ring-red-200" />
      </div>
      <label className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-900">
        <input type="checkbox" checked={acknowledged} onChange={(event) => onAcknowledgedChange(event.target.checked)} className="mt-0.5" />
        <span>I understand this permanently waives the remaining recovery period and cannot be undone.</span>
      </label>
    </ConfirmDialog>
  );
}

export function OrganizationDeletionRestoreDialog({
  open,
  working,
  onConfirm,
  onClose,
}: RestoreDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      title="Restore organization"
      description="Restore access before irreversible cleanup begins? Cancelled work will remain cancelled."
      confirmLabel="Restore organization"
      confirming={working}
      onConfirm={onConfirm}
      onClose={onClose}
    />
  );
}
