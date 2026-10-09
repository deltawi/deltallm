import { Building2 } from 'lucide-react';
import { ORGANIZATION_ROLES, personInputClass, TEAM_ROLES, changeProvisionRole, type OrganizationOption, type PersonFormIssue, type PersonScope, type ProvisionForm, type TeamOption } from '../../lib/peopleForm';
import PlatformRoleField from './PlatformRoleField';
import PersonScopeSelect, { type ScopeSelectionState } from './PersonScopeSelect';

interface Props {
  id: string; form: ProvisionForm; onChange: (form: ProvisionForm) => void;
  organizations: OrganizationOption[]; teams: TeamOption[]; saving: boolean;
  issue: PersonFormIssue | null; onSelectionStatus: (status: ScopeSelectionState) => void;
  selection: ScopeSelectionState | null;
}

export default function PersonAccessFields({ id, form, onChange, organizations, teams, saving, issue, onSelectionStatus, selection }: Props) {
  const selectedId = form.scope === 'organization' ? form.organizationId : form.teamId;
  const matchingSelection = selection?.kind === form.scope && selection.value === selectedId ? selection : null;
  const team = teams.find((item) => item.team_id === form.teamId);
  const organizationName = matchingSelection?.selected?.organizationName || team?.organization_name || organizations.find((item) => item.organization_id === team?.organization_id)?.organization_name;
  const roles = form.scope === 'organization' ? ORGANIZATION_ROLES : TEAM_ROLES;
  const targetField = form.scope === 'organization' ? 'organizationId' : 'teamId';
  return <div className="space-y-5">
    <PlatformRoleField id={`${id}-role`} role={form.platformRole} invitation={form.mode === 'invite_email'} onChange={(role) => onChange(changeProvisionRole(form, role))} />
    {form.platformRole === 'platform_admin' ? <p className="text-xs leading-5 text-gray-500">An initial organization or team membership is not required.</p> : <div className="space-y-4 border-t border-gray-200 pt-5">
      <div className="space-y-1.5">
        <label htmlFor={`${id}-scope`} className="block text-sm font-medium text-gray-700">Initial access</label>
        <select id={`${id}-scope`} value={form.scope} data-person-field="scope" aria-invalid={issue?.field === 'scope' || undefined} aria-describedby={issue?.field === 'scope' ? `${id}-error` : `${id}-scope-hint`} onChange={(event) => onChange({ ...form, scope: event.target.value as PersonScope, organizationId: form.organizationId || organizations[0]?.organization_id || '', teamId: form.teamId || teams[0]?.team_id || '' })} className={personInputClass}>
          <option value="none">No initial scope</option><option value="organization">Organization</option><option value="team">Team</option>
        </select>
        <p id={`${id}-scope-hint`} className="text-xs leading-5 text-gray-500">{form.mode === 'invite_email' ? 'Required for an email invitation.' : 'Optional. Add one organization or team membership.'}</p>
      </div>
      {form.scope !== 'none' && <>
        <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
          <PersonScopeSelect key={form.scope} kind={form.scope} value={selectedId} onChange={(value) => onChange({ ...form, [targetField]: value })} onStatusChange={onSelectionStatus} disabled={saving} errorId={issue?.field === targetField ? `${id}-error` : undefined} />
          <div className="space-y-1.5">
            <label htmlFor={`${id}-membership-role`} className="block text-sm font-medium text-gray-700">{form.scope === 'organization' ? 'Organization role' : 'Team role'}</label>
            <select id={`${id}-membership-role`} value={form.scope === 'organization' ? form.organizationRole : form.teamRole} onChange={(event) => onChange({ ...form, [form.scope === 'organization' ? 'organizationRole' : 'teamRole']: event.target.value })} className={personInputClass}>
              {roles.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}
            </select>
          </div>
        </div>
        {form.scope === 'team' && organizationName && <p className="flex items-center gap-2 rounded-lg bg-gray-50 p-3 text-xs leading-5 text-gray-500"><Building2 aria-hidden="true" className="h-4 w-4 shrink-0" />Organization: {organizationName}</p>}
      </>}
    </div>}
  </div>;
}
