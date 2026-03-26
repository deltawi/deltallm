import { useEffect, useState } from 'react';
import Modal from '../Modal';
import { rbac, type ProvisionPersonResponse } from '../../lib/api';
import { useToast } from '../ToastProvider';

type ProvisionMode = 'invite_email' | 'create_account';
type ScopeType = 'none' | 'organization' | 'team';

type OptionItem = {
  organization_id?: string;
  organization_name?: string | null;
  team_id?: string;
  team_alias?: string | null;
};

interface ProvisionPersonModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess: (result: ProvisionPersonResponse) => Promise<void> | void;
  orgList: OptionItem[];
  teamList: OptionItem[];
  initialOrganizationId?: string | null;
  initialTeamId?: string | null;
}

const PLATFORM_ROLES = [
  { value: 'org_user', label: 'Organization User' },
  { value: 'platform_admin', label: 'Platform Admin' },
] as const;

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

function defaultScopeType(initialOrganizationId?: string | null, initialTeamId?: string | null): ScopeType {
  if (initialTeamId) return 'team';
  if (initialOrganizationId) return 'organization';
  return 'none';
}

export default function ProvisionPersonModal({
  open,
  onClose,
  onSuccess,
  orgList,
  teamList,
  initialOrganizationId,
  initialTeamId,
}: ProvisionPersonModalProps) {
  const { pushToast } = useToast();
  const [email, setEmail] = useState('');
  const [mode, setMode] = useState<ProvisionMode>('invite_email');
  const [platformRole, setPlatformRole] = useState('org_user');
  const [scopeType, setScopeType] = useState<ScopeType>('none');
  const [organizationId, setOrganizationId] = useState('');
  const [organizationRole, setOrganizationRole] = useState('org_member');
  const [teamId, setTeamId] = useState('');
  const [teamRole, setTeamRole] = useState('team_viewer');
  const [password, setPassword] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setEmail('');
    setMode('invite_email');
    setPlatformRole('org_user');
    setScopeType(defaultScopeType(initialOrganizationId, initialTeamId));
    setOrganizationId(initialOrganizationId || orgList[0]?.organization_id || '');
    setOrganizationRole('org_member');
    setTeamId(initialTeamId || teamList[0]?.team_id || '');
    setTeamRole('team_viewer');
    setPassword('');
    setIsActive(true);
    setError('');
  }, [open, initialOrganizationId, initialTeamId, orgList, teamList]);

  useEffect(() => {
    if (mode === 'invite_email' && platformRole !== 'org_user') {
      setPlatformRole('org_user');
    }
  }, [mode, platformRole]);

  useEffect(() => {
    if (platformRole === 'platform_admin') {
      setScopeType('none');
      return;
    }
    if (scopeType === 'none' && (initialOrganizationId || initialTeamId) && mode === 'invite_email') {
      setScopeType(defaultScopeType(initialOrganizationId, initialTeamId));
    }
  }, [initialOrganizationId, initialTeamId, mode, platformRole, scopeType]);

  useEffect(() => {
    if (scopeType !== 'team') return;
    if (!teamList.some((item) => item.team_id === teamId)) {
      setTeamId(teamList[0]?.team_id || '');
    }
  }, [scopeType, teamId, teamList]);

  const submit = async () => {
    const normalizedEmail = email.trim();
    if (!normalizedEmail) {
      setError('Email is required');
      return;
    }
    if (mode === 'invite_email' && scopeType === 'none') {
      setError('Select organization or team access for the invitation');
      return;
    }
    if (mode === 'create_account' && !password.trim()) {
      setError('Password is required when creating an account manually');
      return;
    }
    if (scopeType === 'organization' && !organizationId) {
      setError('Select an organization');
      return;
    }
    if (scopeType === 'team' && !teamId) {
      setError('Select a team');
      return;
    }

    setSaving(true);
    setError('');
    try {
      const payload = {
        email: normalizedEmail,
        mode,
        platform_role: platformRole,
        password: mode === 'create_account' ? password.trim() : undefined,
        is_active: mode === 'create_account' ? isActive : undefined,
        organization_id: scopeType === 'organization' ? organizationId : undefined,
        organization_role: scopeType === 'organization' ? organizationRole : undefined,
        team_id: scopeType === 'team' ? teamId : undefined,
        team_role: scopeType === 'team' ? teamRole : undefined,
      };
      const result = await rbac.provisionPerson(payload);
      pushToast({
        tone: 'success',
        message: mode === 'invite_email' ? 'Invitation queued for delivery.' : 'Account created.',
      });
      onClose();
      void Promise.resolve(onSuccess(result)).catch((refreshError: any) => {
        pushToast({
          tone: 'info',
          message: refreshError?.message || 'Person provisioned, but the list could not be refreshed. Reload to confirm the latest state.',
        });
      });
    } catch (err: any) {
      setError(err?.message || 'Failed to provision access');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={() => { if (!saving) onClose(); }} title="Add Person">
      <div className="space-y-5">
        {error ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        ) : null}

        <section className="space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Identity</h3>
            <p className="mt-1 text-xs text-gray-500">Choose who should receive access.</p>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">Email</label>
            <input
              type="email"
              value={email}
              onChange={(event) => { setEmail(event.target.value); setError(''); }}
              placeholder="user@example.com"
              autoComplete="email"
              data-autofocus="true"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </section>

        <section className="space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Onboarding</h3>
            <p className="mt-1 text-xs text-gray-500">Choose whether to invite them by email or create the account directly.</p>
          </div>
          <div className="grid grid-cols-2 gap-2 rounded-xl bg-gray-100 p-1">
            <button
              type="button"
              onClick={() => { setMode('invite_email'); setError(''); }}
              className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                mode === 'invite_email' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Send invite email
            </button>
            <button
              type="button"
              onClick={() => { setMode('create_account'); setError(''); }}
              className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                mode === 'create_account' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Create account manually
            </button>
          </div>
        </section>

        <section className="space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Access</h3>
            <p className="mt-1 text-xs text-gray-500">Set the platform role and optional initial scope grant.</p>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">Platform role</label>
            <select
              value={platformRole}
              onChange={(event) => { setPlatformRole(event.target.value); setError(''); }}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {PLATFORM_ROLES.filter((item) => mode === 'create_account' || item.value !== 'platform_admin').map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>

          {platformRole !== 'platform_admin' ? (
            <>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-gray-700">Initial access</label>
                <select
                  value={scopeType}
                  onChange={(event) => { setScopeType(event.target.value as ScopeType); setError(''); }}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="none">No initial scope</option>
                  <option value="organization">Organization</option>
                  <option value="team">Team</option>
                </select>
              </div>

              {scopeType === 'organization' ? (
                <>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-gray-700">Organization</label>
                    <select
                      value={organizationId}
                      onChange={(event) => { setOrganizationId(event.target.value); setError(''); }}
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
                    <label className="mb-1.5 block text-sm font-medium text-gray-700">Organization role</label>
                    <select
                      value={organizationRole}
                      onChange={(event) => setOrganizationRole(event.target.value)}
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      {ORGANIZATION_ROLES.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </>
              ) : null}

              {scopeType === 'team' ? (
                <>
                  <div>
                    <label className="mb-1.5 block text-sm font-medium text-gray-700">Team</label>
                    <select
                      value={teamId}
                      onChange={(event) => { setTeamId(event.target.value); setError(''); }}
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
                    <label className="mb-1.5 block text-sm font-medium text-gray-700">Team role</label>
                    <select
                      value={teamRole}
                      onChange={(event) => setTeamRole(event.target.value)}
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      {TEAM_ROLES.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </>
              ) : null}
            </>
          ) : (
            <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
              Platform admins receive full control-plane access and do not need an initial organization or team grant.
            </div>
          )}
        </section>

        {mode === 'create_account' ? (
          <section className="space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-900">Account setup</h3>
              <p className="mt-1 text-xs text-gray-500">Manual creation sets the initial password immediately.</p>
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-gray-700">Password</label>
              <input
                type="password"
                value={password}
                onChange={(event) => { setPassword(event.target.value); setError(''); }}
                placeholder="At least 12 characters"
                autoComplete="new-password"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(event) => setIsActive(event.target.checked)}
                className="rounded border-gray-300"
              />
              Account active
            </label>
          </section>
        ) : null}

        <div className="flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={saving}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? 'Saving…' : mode === 'invite_email' ? 'Send Invitation' : 'Create Account'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
