import type { RBACAccount, rbac } from './api';

export type ProvisionMode = 'invite_email' | 'create_account';
export type PersonScope = 'none' | 'organization' | 'team';
export type PersonTab = 'details' | 'access';
export type OrganizationOption = { organization_id: string; organization_name?: string | null };
export type TeamOption = { team_id: string; team_alias?: string | null; organization_id?: string | null; organization_name?: string | null };
export const PLATFORM_ROLES = [{ value: 'org_user', label: 'Organization User' }, { value: 'platform_admin', label: 'Platform Admin' }];
export const ORGANIZATION_ROLES = [{ value: 'org_member', label: 'Member' }, { value: 'org_owner', label: 'Owner' }, { value: 'org_admin', label: 'Admin' }, { value: 'org_billing', label: 'Billing' }, { value: 'org_auditor', label: 'Auditor' }];
export const TEAM_ROLES = [{ value: 'team_admin', label: 'Admin' }, { value: 'team_developer', label: 'Developer' }, { value: 'team_viewer', label: 'Viewer' }];
export const personInputClass = 'block w-full min-w-0 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-gray-100 disabled:text-gray-600';
export type ProvisionForm = {
  email: string; mode: ProvisionMode; platformRole: string; scope: PersonScope;
  organizationId: string; organizationRole: string; teamId: string; teamRole: string;
  password: string; active: boolean;
};
export type AccountForm = { role: string; password: string; active: boolean };
export type PersonFormIssue = { tab: PersonTab; field: string; message: string };
export type ProvisionPrefill = { initialOrganizationId?: string | null; initialTeamId?: string | null };

export function initialProvisionForm(prefill: ProvisionPrefill, organizations: OrganizationOption[] = [], teams: TeamOption[] = []): ProvisionForm {
  return {
    email: '', mode: 'invite_email', platformRole: 'org_user', password: '', active: true,
    scope: prefill.initialTeamId ? 'team' : prefill.initialOrganizationId ? 'organization' : 'none',
    organizationId: prefill.initialOrganizationId || organizations[0]?.organization_id || '',
    organizationRole: 'org_member', teamId: prefill.initialTeamId || teams[0]?.team_id || '', teamRole: 'team_viewer',
  };
}

export function changeProvisionMethod(form: ProvisionForm, mode: ProvisionMode, prefill: ProvisionPrefill): ProvisionForm {
  if (mode === 'create_account') return { ...form, mode };
  return {
    ...form, mode, platformRole: 'org_user', password: '',
    scope: form.scope !== 'none' ? form.scope : prefill.initialTeamId ? 'team' : prefill.initialOrganizationId ? 'organization' : 'none',
  };
}

export function changeProvisionRole(form: ProvisionForm, platformRole: string): ProvisionForm {
  return { ...form, platformRole, scope: platformRole === 'platform_admin' ? 'none' : form.scope };
}

export function validateProvisionForm(form: ProvisionForm): PersonFormIssue | null {
  if (!form.email.trim()) return { tab: 'details', field: 'email', message: 'Enter an email address.' };
  if (!/^[^\s@]+@[^\s@]+$/.test(form.email.trim())) return { tab: 'details', field: 'email', message: 'Enter a valid email address.' };
  if (form.mode === 'invite_email' && form.scope === 'none') return { tab: 'access', field: 'scope', message: 'Choose an organization or team for this invitation.' };
  if (form.mode === 'create_account' && form.password.trim().length < 12) return { tab: 'details', field: 'password', message: 'Use a password with at least 12 characters.' };
  if (form.scope === 'organization' && !form.organizationId) return { tab: 'access', field: 'organizationId', message: 'Select an organization.' };
  if (form.scope === 'team' && !form.teamId) return { tab: 'access', field: 'teamId', message: 'Select a team.' };
  return null;
}

export function provisionPayload(form: ProvisionForm): Parameters<typeof rbac.provisionPerson>[0] {
  const platformRole = form.mode === 'invite_email' ? 'org_user' : form.platformRole;
  return {
    email: form.email.trim(), mode: form.mode, platform_role: platformRole,
    ...(form.mode === 'create_account' ? { password: form.password.trim(), is_active: form.active } : {}),
    ...(platformRole !== 'platform_admin' && form.scope === 'organization' ? { organization_id: form.organizationId, organization_role: form.organizationRole } : {}),
    ...(platformRole !== 'platform_admin' && form.scope === 'team' ? { team_id: form.teamId, team_role: form.teamRole } : {}),
  };
}

export function initialAccountForm(account: RBACAccount): AccountForm {
  return { role: account.role, password: '', active: account.is_active };
}

export function validateAccountForm(form: AccountForm): PersonFormIssue | null {
  if (form.password.trim() && form.password.trim().length < 12) return { tab: 'details', field: 'password', message: 'Use a password with at least 12 characters.' };
  return null;
}

export function accountPayload(account: RBACAccount, form: AccountForm): { email: string; role: string; is_active: boolean; password?: string } {
  return { email: account.email.trim(), role: form.role, is_active: form.active, ...(form.password.trim() ? { password: form.password.trim() } : {}) };
}
