import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, LogIn, RotateCcw, Save } from 'lucide-react';
import Button from '../../Button';
import ConfirmDialog from '../../ConfirmDialog';
import { PanelError, Skeleton } from '../../usage/ReportBits';
import { useToast } from '../../ToastProvider';
import { SettingsField, SettingsSection } from '../SettingsSection';
import ChipListInput from './ChipListInput';
import SwitchRow from './SwitchRow';
import { useApi } from '../../../lib/hooks';
import { ssoSignIn } from '../../../lib/api/ssoSignIn';
import type { SignInSettings, SsoSignInRules } from '../../../lib/api/ssoSignIn';
import { errorMessage } from '../../../lib/errorMessage';
import {
  addEmailDomains,
  buildSignInPayload,
  DEFAULT_SIGN_IN_SETTINGS,
  sameSignInSettings,
  validateSignInSettings,
} from '../../../lib/ssoSignIn';

const NEW_ACCOUNT_OPTIONS: Array<{ value: boolean; title: string; description: string }> = [
  {
    value: true,
    title: 'Create an account at the first SSO sign-in',
    description: 'Anyone that the identity provider lets in gets an account. Without a team, they cannot use models.',
  },
  {
    value: false,
    title: 'Only people who already have an account',
    description: 'Add people on the People page first. Emails in the admin email list and people with a mapped group value also get an account.',
  },
];

export default function SsoSignInPanel() {
  const { pushToast } = useToast();
  const { data, loading, error, refetch } = useApi((signal) => ssoSignIn.get(signal), []);
  const [saved, setSaved] = useState<SignInSettings>(DEFAULT_SIGN_IN_SETTINGS);
  const [draft, setDraft] = useState<SignInSettings>(DEFAULT_SIGN_IN_SETTINGS);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmPasswordOff, setConfirmPasswordOff] = useState(false);

  useEffect(() => {
    if (!data) return;
    const loaded = buildSignInPayload(data);
    setSaved(loaded);
    setDraft(loaded);
  }, [data]);

  const dirty = !sameSignInSettings(draft, saved);
  const problem = useMemo(() => (data && dirty ? validateSignInSettings(draft, data) : null), [data, draft, dirty]);
  const updateRules = (patch: Partial<SsoSignInRules>) => {
    setDraft((current) => ({ ...current, sso_sign_in: { ...current.sso_sign_in, ...patch } }));
    setSaveError(null);
  };

  const save = async () => {
    if (problem) return;
    setSaving(true);
    setSaveError(null);
    try {
      const response = await ssoSignIn.update(buildSignInPayload(draft));
      const next = buildSignInPayload(response);
      setSaved(next);
      setDraft(next);
      pushToast({ tone: 'success', message: 'Sign-in settings saved.' });
    } catch (err) {
      setSaveError(errorMessage(err, 'Could not save the sign-in settings.'));
    } finally {
      setSaving(false);
    }
  };

  if (loading && !data) {
    return (
      <div className="space-y-3 rounded-xl border border-gray-200 bg-white p-5 shadow-sm" aria-busy="true" aria-label="Loading sign-in settings">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-3 w-72" />
        <Skeleton className="h-9 w-full" />
      </div>
    );
  }
  if (error && !data) return <PanelError message={errorMessage(error, 'Could not load sign-in settings.')} onRetry={refetch} />;

  const rules = draft.sso_sign_in;
  const newAccountsLocked = Boolean(data?.self_registration_enabled);
  return (
    <SettingsSection title="Sign-in" description="Choose how people sign in to the Admin UI. The master key always works." icon={LogIn}>
      <div className="space-y-5">
        <SwitchRow
          title="Allow email and password sign-in"
          description="When off, people sign in only with SSO. Password reset is off, invitations do not ask for a password, and sessions that started with a password end at once."
          checked={draft.password_login_enabled}
          onChange={(value) => {
            if (value) {
              setDraft((current) => ({ ...current, password_login_enabled: true }));
              setSaveError(null);
            } else {
              setConfirmPasswordOff(true);
            }
          }}
          highlight={!draft.password_login_enabled}
        />

        <div className="border-t border-gray-100 px-3 pt-4">
          <fieldset className="space-y-2">
            <legend className="mb-2 text-sm font-medium text-gray-800">New people from SSO</legend>
            {NEW_ACCOUNT_OPTIONS.map((option) => (
              <label
                key={String(option.value)}
                className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 ${rules.allow_new_accounts === option.value ? 'border-brand-primary bg-brand-primary-soft' : 'border-gray-200'} ${newAccountsLocked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
              >
                <input
                  type="radio"
                  name="sso-new-accounts"
                  className="mt-0.5"
                  checked={rules.allow_new_accounts === option.value}
                  disabled={newAccountsLocked}
                  onChange={() => updateRules({ allow_new_accounts: option.value })}
                />
                <span>
                  <span className="block text-sm text-gray-900">{option.title}</span>
                  <span className="block text-xs text-gray-500">{option.description}</span>
                </span>
              </label>
            ))}
            {newAccountsLocked && (
              <p className="text-xs text-gray-500">Self-registration is on in config.yaml, so SSO creates sandbox accounts for allowed domains.</p>
            )}
          </fieldset>
        </div>

        <div className="border-t border-gray-100 px-3 pt-4">
          <SettingsField
            label="Allowed email domains"
            htmlFor="sso-allowed-domains"
            hint="Leave empty to allow every domain. When set, every SSO sign-in, admins too, needs a verified email in one of these domains. Subdomains are not included."
          >
            <ChipListInput
              id="sso-allowed-domains"
              values={rules.allowed_email_domains}
              onChange={(allowed_email_domains) => updateRules({ allowed_email_domains })}
              add={addEmailDomains}
              placeholder="example.com"
            />
          </SettingsField>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-3 border-t border-gray-100 pt-4">
          {(problem || saveError) && (
            <p className="mr-auto flex items-center gap-1.5 text-xs text-red-600" role="alert">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {saveError || problem}
            </p>
          )}
          {!problem && !saveError && dirty && <p className="mr-auto text-xs text-gray-500">You have unsaved changes.</p>}
          <Button variant="ghost" size="sm" onClick={() => { setDraft(saved); setSaveError(null); }} disabled={!dirty || saving}>
            <RotateCcw className="h-4 w-4" />Discard
          </Button>
          <Button size="sm" onClick={() => { void save(); }} loading={saving} disabled={!dirty || Boolean(problem)}>
            <Save className="h-4 w-4" />Save sign-in settings
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmPasswordOff}
        title="Turn off password sign-in?"
        description="After you save, people can sign in only with SSO or the master key. Everyone who signed in with a password is signed out. Password reset links stop working. Make sure that SSO works for the people who need access."
        confirmLabel="Turn off password sign-in"
        destructive
        onConfirm={() => {
          setDraft((current) => ({ ...current, password_login_enabled: false }));
          setSaveError(null);
          setConfirmPasswordOff(false);
        }}
        onClose={() => setConfirmPasswordOff(false)}
      />
    </SettingsSection>
  );
}
