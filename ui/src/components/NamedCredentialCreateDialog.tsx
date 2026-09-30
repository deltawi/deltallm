import { useState } from 'react';
import {
  namedCredentials,
  type ManagedAssetAccessInput,
  type NamedCredential,
  type ProviderPreset,
} from '../lib/api';
import { useToast } from './ToastProvider';
import Modal from './Modal';
import NamedCredentialForm, {
  type NamedCredentialAudienceOption,
  type NamedCredentialPayload,
} from './NamedCredentialForm';

interface NamedCredentialCreateDialogProps {
  open: boolean;
  providerPresets: ProviderPreset[];
  initialProvider?: string;
  initialAccess?: ManagedAssetAccessInput;
  lockProvider?: boolean;
  teamOptions?: NamedCredentialAudienceOption[];
  organizationOptions?: NamedCredentialAudienceOption[];
  allowPublic?: boolean;
  audiencesLoading?: boolean;
  audiencesError?: string | null;
  onRetryAudiences?: () => void;
  onClose: () => void;
  onCreated?: (credential: NamedCredential) => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : 'Failed to create named credential.';
}

export default function NamedCredentialCreateDialog({
  open,
  providerPresets,
  initialProvider = '',
  initialAccess,
  lockProvider = false,
  teamOptions = [],
  organizationOptions = [],
  allowPublic = false,
  audiencesLoading = false,
  audiencesError = null,
  onRetryAudiences,
  onClose,
  onCreated,
}: NamedCredentialCreateDialogProps) {
  const { pushToast } = useToast();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    if (saving) return;
    setError(null);
    onClose();
  };

  const handleCreate = async (payload: NamedCredentialPayload) => {
    setSaving(true);
    setError(null);
    let credential: NamedCredential;
    try {
      credential = await namedCredentials.create(payload);
    } catch (createError: unknown) {
      setError(errorMessage(createError));
      setSaving(false);
      return;
    }
    setSaving(false);
    onCreated?.(credential);
    pushToast({
      tone: 'success',
      title: 'Credential created',
      message: `"${credential.name || payload.name}" is ready to use.`,
    });
    onClose();
  };

  return (
    <Modal open={open} onClose={close} title="Create Named Credential" wide>
      <NamedCredentialForm
        initialProvider={initialProvider}
        initialAccess={initialAccess}
        lockProvider={lockProvider}
        providerPresets={providerPresets}
        saving={saving}
        error={error}
        teamOptions={teamOptions}
        organizationOptions={organizationOptions}
        allowPublic={allowPublic}
        audiencesLoading={audiencesLoading}
        audiencesError={audiencesError}
        onRetryAudiences={onRetryAudiences}
        onSave={handleCreate}
        onCancel={close}
      />
    </Modal>
  );
}
