import { useEffect, useId, useRef, useState } from 'react';
import { Mail, ShieldCheck, UserRound, UserRoundPlus } from 'lucide-react';
import Modal from '../Modal';
import { rbac, type ProvisionPersonResponse } from '../../lib/api';
import { changeProvisionMethod, initialProvisionForm, ORGANIZATION_ROLES, personInputClass, provisionPayload, TEAM_ROLES, validateProvisionForm, type OrganizationOption, type PersonFormIssue, type PersonTab, type ProvisionForm, type TeamOption } from '../../lib/peopleForm';
import { useToast } from '../ToastProvider';
import Button from '../Button';
import { IconTabs } from './shells';
import AccountStatusControl from '../people/AccountStatusControl';
import PersonAccessFields from '../people/PersonAccessFields';
import type { ScopeSelectionState } from '../people/PersonScopeSelect';

interface Props {
  open: boolean; onClose: () => void;
  onSuccess: (result: ProvisionPersonResponse) => Promise<void> | void;
  orgList: OrganizationOption[]; teamList: TeamOption[];
  initialOrganizationId?: string | null; initialTeamId?: string | null;
}

function ProvisionPersonEditor({ onClose, onSuccess, orgList, teamList, initialOrganizationId, initialTeamId }: Omit<Props, 'open'>) {
  const { pushToast } = useToast();
  const id = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  const [form, setForm] = useState(() => initialProvisionForm({ initialOrganizationId, initialTeamId }, orgList, teamList));
  const [tab, setTab] = useState<PersonTab>('details');
  const [selection, setSelection] = useState<ScopeSelectionState | null>(null);
  const [saving, setSaving] = useState(false);
  const [issue, setIssue] = useState<PersonFormIssue | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const close = () => { if (!pending.current) onClose(); };
  const change = (next: ProvisionForm) => { setForm(next); setIssue(null); setError(''); };
  const items = [{ id: 'details' as const, label: 'Details', icon: UserRound }, { id: 'access' as const, label: 'Access', icon: ShieldCheck }];
  const selectedId = form.scope === 'organization' ? form.organizationId : form.teamId;
  const currentSelection = selection?.kind === form.scope && selection.value === selectedId ? selection : null;
  const fallbackName = form.scope === 'organization' ? orgList.find((item) => item.organization_id === selectedId)?.organization_name : teamList.find((item) => item.team_id === selectedId)?.team_alias;
  const summaryName = form.platformRole === 'platform_admin' ? 'Platform Admin' : form.scope !== 'none' ? currentSelection?.selected?.label || fallbackName || selectedId || 'Choose initial access' : form.mode === 'invite_email' ? 'Initial access required' : 'No initial scope';
  const roles = form.scope === 'organization' ? ORGANIZATION_ROLES : TEAM_ROLES;
  const summaryDetail = form.platformRole === 'platform_admin' ? form.active ? 'Account active' : 'Account disabled' : form.scope !== 'none' ? `${form.scope === 'organization' ? 'Organization' : 'Team'} · ${roles.find((role) => role.value === (form.scope === 'organization' ? form.organizationRole : form.teamRole))?.label || ''}` : form.mode === 'invite_email' ? 'Choose an organization or team' : 'Organization User';
  const reportIssue = (next: PersonFormIssue) => {
    setIssue(next); setError(next.message); setTab(next.tab);
    window.requestAnimationFrame(() => formRef.current?.querySelector<HTMLElement>(`[data-person-field="${next.field}"]`)?.focus());
  };
  const submit = async () => {
    if (pending.current) return;
    const nextIssue = validateProvisionForm(form);
    if (nextIssue) { reportIssue(nextIssue); return; }
    if (form.scope !== 'none') {
      const field = form.scope === 'organization' ? 'organizationId' : 'teamId';
      if (!currentSelection || currentSelection.loading) { reportIssue({ tab: 'access', field, message: 'Wait for initial access to finish loading.' }); return; }
      if (currentSelection.error || !currentSelection.selected) { reportIssue({ tab: 'access', field, message: currentSelection.error || 'Select available initial access.' }); return; }
    }
    pending.current = true; setSaving(true); setError(''); setIssue(null);
    try {
      const result = await rbac.provisionPerson(provisionPayload(form));
      if (!mounted.current) return;
      setForm((current) => ({ ...current, password: '' }));
      pushToast({ tone: 'success', message: form.mode === 'invite_email' ? 'Invitation queued for delivery.' : 'Account created.' });
      onClose();
      void Promise.resolve().then(() => onSuccess(result)).catch(() => pushToast({ tone: 'info', message: 'Access was saved, but the list could not be refreshed. Reload to see the latest state.' }));
    } catch (err: unknown) {
      if (mounted.current) setError(err instanceof Error ? err.message : 'Failed to provision access.');
    } finally {
      pending.current = false;
      if (mounted.current) setSaving(false);
    }
  };
  return <Modal open focused onClose={close} title="Add person" icon={<UserRoundPlus className="h-5 w-5" />} description="Set up sign-in and initial access." navigation={<IconTabs id={id} label="New person settings" items={items} active={tab} onChange={setTab} />} footer={
    <div className="space-y-3">
      {error && <div id={`${id}-error`} role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="min-w-0 text-xs leading-5 text-gray-500"><span className="block max-w-56 truncate font-medium text-gray-700">{summaryName}</span>{summaryDetail}</p>
        <div className="ml-auto flex gap-2"><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button form={`${id}-form`} type="submit" loading={saving}>{saving ? 'Saving…' : form.mode === 'invite_email' ? 'Send invitation' : 'Create account'}</Button></div>
      </div>
    </div>
  }>
    <form id={`${id}-form`} ref={formRef} noValidate onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      {items.map((item) => <div key={item.id} role="tabpanel" id={`${id}-panel-${item.id}`} aria-labelledby={`${id}-tab-${item.id}`} hidden={tab !== item.id}>
        <fieldset disabled={saving} className="min-w-0 space-y-5" aria-describedby={error ? `${id}-error` : undefined}>
          {item.id === 'details' ? <>
            <label className="block space-y-1.5 text-sm font-medium text-gray-700">Email
              <input value={form.email} type="email" onChange={(event) => change({ ...form, email: event.target.value })} placeholder="person@example.com" autoComplete="email" data-autofocus="true" data-person-field="email" aria-invalid={issue?.field === 'email' || undefined} aria-describedby={issue?.field === 'email' ? `${id}-error` : undefined} className={personInputClass} />
            </label>
            <fieldset className="min-w-0"><legend className="mb-2 text-sm font-medium text-gray-700">How should they get access?</legend>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {(['invite_email', 'create_account'] as const).map((mode) => <label key={mode} className={`flex cursor-pointer items-start gap-2 rounded-lg border p-3 ${form.mode === mode ? 'border-brand-primary bg-brand-primary-soft' : 'border-gray-200'}`}>
                  <input type="radio" name={`${id}-method`} value={mode} checked={form.mode === mode} onChange={() => change(changeProvisionMethod(form, mode, { initialOrganizationId, initialTeamId }))} className="mt-1 shrink-0 accent-brand-primary" />
                  <span><span className="block text-sm font-medium text-gray-900">{mode === 'invite_email' ? 'Email invitation' : 'Create manually'}</span><span className="block text-xs leading-5 text-gray-500">{mode === 'invite_email' ? 'Send a link to accept access.' : 'Set the initial password now.'}</span></span>
                </label>)}
              </div>
            </fieldset>
            {form.mode === 'invite_email' ? <p className="flex items-start gap-2 rounded-lg bg-gray-50 p-3 text-xs leading-5 text-gray-500"><Mail aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />The invitation includes the organization or team access selected in the Access tab.</p> : <div className="space-y-4 border-t border-gray-200 pt-5">
              <label className="block space-y-1.5 text-sm font-medium text-gray-700">Password
                <input type="password" value={form.password} onChange={(event) => change({ ...form, password: event.target.value })} autoComplete="new-password" placeholder="At least 12 characters" data-person-field="password" aria-invalid={issue?.field === 'password' || undefined} aria-describedby={issue?.field === 'password' ? `${id}-error` : `${id}-password-hint`} className={personInputClass} />
              </label>
              <p id={`${id}-password-hint`} className="text-xs leading-5 text-gray-500">Required for manual account creation.</p>
              <AccountStatusControl active={form.active} onChange={(active) => change({ ...form, active })} />
            </div>}
          </> : <PersonAccessFields id={id} form={form} onChange={change} organizations={orgList} teams={teamList} saving={saving} issue={issue} onSelectionStatus={setSelection} selection={selection} />}
        </fieldset>
      </div>)}
    </form>
  </Modal>;
}

export default function ProvisionPersonModal({ open, ...props }: Props) {
  return open ? <ProvisionPersonEditor key={JSON.stringify([props.initialOrganizationId, props.initialTeamId])} {...props} /> : null;
}
