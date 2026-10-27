import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Pencil } from 'lucide-react';
import type { ModelDeploymentDetail } from '../../../lib/api';
import type { ManagedAssetAudienceOption } from '../../../lib/useManagedAssetAudienceOptions';
import Button from '../../Button';
import ManagedAssetAccessPanel from '../../ManagedAssetAccessPanel';
import { SettingsRow } from '../../admin/shells';
import { DetailSection, KeyValueList, Muted } from './DetailSection';
import { formatDate } from './modelDetailFormat';

export interface ModelAudienceOptions {
  teamOptions: ManagedAssetAudienceOption[];
  organizationOptions: ManagedAssetAudienceOption[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

interface ModelSettingsTabProps {
  model: ModelDeploymentDetail;
  modeLabel: string;
  sharing: string | null;
  canEdit: boolean;
  canDelete: boolean;
  canRevokeCredential: boolean;
  isPlatformAdmin: boolean;
  audiences: ModelAudienceOptions;
  onEdit: () => void;
  onRevokeCredential: () => void;
  onDelete: () => void;
  onAccessSaved: () => void;
}

export default function ModelSettingsTab({
  model, modeLabel, sharing, canEdit, canDelete, canRevokeCredential, isPlatformAdmin, audiences,
  onEdit, onRevokeCredential, onDelete, onAccessSaved,
}: ModelSettingsTabProps) {
  const [showAccess, setShowAccess] = useState(false);
  const name = model.api_model_id || model.model_name;
  const access = model.access;
  const created = formatDate(model.created_at);
  const owner = access?.effective_role === 'owner' ? ' You are the owner.' : '';
  return (
    <div className="max-w-4xl space-y-3">
      <DetailSection
        title="General"
        description="Name and identifiers. The editor also changes the provider, limits and pricing."
        action={canEdit ? <Button size="sm" onClick={onEdit}><Pencil className="h-3.5 w-3.5" aria-hidden="true" />Edit model</Button> : undefined}
      >
        <KeyValueList rows={[
          { label: 'Display name', value: model.display_name || model.model_name },
          { label: 'Model name clients send', value: name, mono: true },
          { label: 'Deployment ID', value: model.deployment_id, mono: true },
          { label: 'Workload', value: modeLabel },
          {
            label: 'Created',
            value: created
              ? <>{created}{model.created_by_email ? <span className="text-gray-500"> by {model.created_by_email}</span> : null}</>
              : <Muted>Unknown</Muted>,
          },
          ...(formatDate(model.updated_at) ? [{ label: 'Last changed', value: formatDate(model.updated_at) }] : []),
        ]} />
      </DetailSection>

      {access?.governance_source === 'creator' ? (
        <SettingsRow
          title="Sharing"
          description={`${sharing ?? 'Private'}.${owner}`}
          action={access.capabilities.manage_access || isPlatformAdmin
            ? <Button size="sm" variant="outline" aria-expanded={showAccess} onClick={() => setShowAccess((open) => !open)}>{showAccess ? 'Close' : 'Manage access'}</Button>
            : undefined}
        >
          {showAccess ? (
            <div className="border-t border-gray-100 px-4 py-3">
              <ManagedAssetAccessPanel
                access={access}
                assetLabel="Model"
                teamOptions={audiences.teamOptions}
                organizationOptions={audiences.organizationOptions}
                allowPublic={isPlatformAdmin}
                audiencesLoading={audiences.loading}
                audiencesError={audiences.error}
                onRetryAudiences={audiences.refetch}
                onSaved={onAccessSaved}
              />
            </div>
          ) : null}
        </SettingsRow>
      ) : access?.governance_source === 'platform' ? (
        <SettingsRow
          title="Sharing"
          description="Tier managed. Organizations reach this model, and its prices and limits, through their tier."
          action={isPlatformAdmin
            ? <Link to="/tiers" className="control-sm inline-flex items-center rounded-md border border-gray-200 bg-white text-gray-700 hover:bg-gray-50">Open tiers</Link>
            : undefined}
        />
      ) : null}

      {canRevokeCredential ? (
        <SettingsRow
          title="Credential access"
          description={<>Stop this model from using <strong>{model.named_credential_name || 'its credential'}</strong>. Requests fail until an editor chooses a replacement.</>}
          action={<Button size="sm" variant="warning-outline" onClick={onRevokeCredential}>Revoke credential</Button>}
        />
      ) : null}

      {canDelete ? (
        <SettingsRow
          tone="danger"
          title="Delete model"
          description={<>Removes this deployment. Clients calling <code className="font-mono">{name}</code> get errors unless another deployment serves it.</>}
          action={<Button size="sm" variant="danger-outline" onClick={onDelete}>Delete model</Button>}
        />
      ) : null}
    </div>
  );
}
