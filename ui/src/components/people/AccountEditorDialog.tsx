import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, KeyRound, LockKeyhole, LogOut, UserCog } from 'lucide-react';
import { rbac, type RBACAccount } from '../../lib/api';
import { accountPayload, initialAccountForm, personInputClass, PLATFORM_ROLES, validateAccountForm } from '../../lib/peopleForm';
import Button from '../Button';
import Modal from '../Modal';
import { useToast } from '../ToastProvider';
import AccountStatusControl from './AccountStatusControl';
import PlatformRoleField from './PlatformRoleField';

interface Props { account: RBACAccount; onClose: () => void; onSuccess: () => Promise<void> | void }

export default function AccountEditorDialog({ account, onClose, onSuccess }: Props) {
  const id = useId();
  const { pushToast } = useToast();
  const [form, setForm] = useState(() => initialAccountForm(account));
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [error, setError] = useState('');
  const [passwordError, setPasswordError] = useState(false);
  const [saving, setSaving] = useState(false);
  const pending = useRef(false);
  const mounted = useRef(true);
  const passwordRef = useRef<HTMLInputElement>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const close = () => { if (!pending.current) onClose(); };
  const clearError = () => { setError(''); setPasswordError(false); };
  const submit = async () => {
    if (pending.current) return;
    const issue = validateAccountForm(form);
    if (issue) {
      setError(issue.message); setPasswordError(true); setPasswordOpen(true);
      window.requestAnimationFrame(() => passwordRef.current?.focus());
      return;
    }
    pending.current = true; setSaving(true); clearError();
    try {
      await rbac.accounts.upsert(accountPayload(account, form));
      if (!mounted.current) return;
      setForm((current) => ({ ...current, password: '' }));
      pushToast({ tone: 'success', message: 'Account updated.' });
      onClose();
      void Promise.resolve().then(onSuccess).catch(() => pushToast({ tone: 'info', message: 'The account was updated, but the list could not be refreshed. Reload to see the latest state.' }));
    } catch (err: unknown) {
      if (mounted.current) setError(err instanceof Error ? err.message : 'Failed to save the account.');
    } finally {
      pending.current = false;
      if (mounted.current) setSaving(false);
    }
  };
  return <Modal open focused onClose={close} title="Edit account" icon={<UserCog className="h-5 w-5" />} description="Manage platform access and sign-in." footer={
    <div className="space-y-3">
      {error && <div id={`${id}-error`} role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="min-w-0 text-xs leading-5 text-gray-500"><span className="block max-w-56 truncate font-medium text-gray-700">{account.email}</span>{form.active ? 'Active' : 'Disabled'} · {PLATFORM_ROLES.find((role) => role.value === form.role)?.label || form.role}</p>
        <div className="ml-auto flex gap-2"><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button form={`${id}-form`} type="submit" loading={saving}>{saving ? 'Saving…' : 'Save changes'}</Button></div>
      </div>
    </div>
  }>
    <form id={`${id}-form`} noValidate onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <fieldset disabled={saving} className="min-w-0 space-y-5" aria-describedby={error ? `${id}-error` : undefined}>
        <div className="space-y-1.5">
          <label className="block space-y-1.5 text-sm font-medium text-gray-700">Email<input type="email" value={account.email} disabled aria-describedby={`${id}-email-locked`} className={personInputClass} /></label>
          <p id={`${id}-email-locked`} className="flex items-center gap-1.5 text-xs text-gray-500"><LockKeyhole aria-hidden="true" className="h-3.5 w-3.5" />Email cannot be changed.</p>
        </div>
        <div className="space-y-4 border-t border-gray-200 pt-5">
          <PlatformRoleField id={`${id}-role`} role={form.role} onChange={(role) => { setForm({ ...form, role }); clearError(); }} />
          <AccountStatusControl active={form.active} onChange={(active) => { setForm({ ...form, active }); clearError(); }} />
        </div>
        <details open={passwordOpen} onToggle={(event) => setPasswordOpen(event.currentTarget.open)} className="group border-t border-gray-200 pt-4">
          <summary className="flex cursor-pointer list-none items-center gap-2 rounded text-sm font-medium text-gray-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary [&::-webkit-details-marker]:hidden"><KeyRound aria-hidden="true" className="h-4 w-4" />Change password<ChevronDown aria-hidden="true" className="ml-auto h-4 w-4 group-open:rotate-180" /></summary>
          <div className="mt-4 space-y-3">
            <label className="block space-y-1.5 text-sm font-medium text-gray-700">New password<input ref={passwordRef} type="password" value={form.password} onChange={(event) => { setForm({ ...form, password: event.target.value }); clearError(); }} autoComplete="new-password" placeholder="Leave blank to keep current" aria-invalid={passwordError || undefined} aria-describedby={passwordError ? `${id}-error` : `${id}-password-hint`} className={personInputClass} /></label>
            <p id={`${id}-password-hint`} className="text-xs leading-5 text-gray-500">Use at least 12 characters. Leave blank to keep the current password.</p>
            <p className="flex items-start gap-2 rounded-lg bg-gray-50 p-3 text-xs leading-5 text-gray-500"><LogOut aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />A new password signs this account out of all active sessions.</p>
          </div>
        </details>
      </fieldset>
    </form>
  </Modal>;
}
