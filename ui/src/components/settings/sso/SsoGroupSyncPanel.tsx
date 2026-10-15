import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, FlaskConical, KeyRound, ListTree, Plus, RotateCcw, Save, ShieldCheck, X } from 'lucide-react';
import Button from '../../Button';
import ConfirmDialog from '../../ConfirmDialog';
import ToggleSwitch from '../../ToggleSwitch';
import { PanelError, Skeleton } from '../../usage/ReportBits';
import { useToast } from '../../ToastProvider';
import { SettingsField, SettingsSection } from '../SettingsSection';
import AddSsoMappingDialog from './AddSsoMappingDialog';
import SsoMappingTable from './SsoMappingTable';
import SsoPreviewPanel from './SsoPreviewPanel';
import { useApi } from '../../../lib/hooks';
import { ssoGroupSync } from '../../../lib/api/ssoGroupSync';
import type { SsoGroupMapping, SsoGroupSyncSettings, SsoGroupTarget } from '../../../lib/api/ssoGroupSync';
import { errorMessage } from '../../../lib/errorMessage';
import {
  addUniqueValues,
  buildSsoGroupSyncPayload,
  DEFAULT_SSO_GROUP_SYNC,
  sameSsoGroupSync,
  targetIndex,
  validateSsoGroupSync,
} from '../../../lib/ssoGroupSync';

const INPUT_CLASS = 'w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm transition-all focus:border-transparent focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary';

function SwitchRow({ title, description, checked, onChange, warning = false }: {
  title: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  warning?: boolean;
}) {
  return (
    <div className={`flex items-start justify-between gap-4 rounded-lg px-3 py-3 ${warning && checked ? 'border border-amber-200 bg-amber-50/60' : ''}`}>
      <div>
        <p className="text-sm font-medium text-gray-800">{title}</p>
        <p className="mt-0.5 text-xs text-gray-500">{description}</p>
      </div>
      <ToggleSwitch checked={checked} onCheckedChange={onChange} aria-label={title} />
    </div>
  );
}

function AdminValuesInput({ values, onChange }: { values: string[]; onChange: (values: string[]) => void }) {
  const [draft, setDraft] = useState('');
  const commit = () => {
    if (!draft.trim()) return;
    onChange(addUniqueValues(values, draft));
    setDraft('');
  };
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1.5 focus-within:bg-white focus-within:ring-2 focus-within:ring-brand-primary">
        {values.map((value) => (
          <span key={value} className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-0.5 font-mono text-xs text-gray-800 ring-1 ring-gray-200">
            {value}
            <button type="button" onClick={() => onChange(values.filter((item) => item !== value))} aria-label={`Remove ${value}`} className="text-gray-400 hover:text-gray-700">
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          id="sso-admin-values"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ',') {
              event.preventDefault();
              commit();
            } else if (event.key === 'Backspace' && !draft && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          onBlur={commit}
          placeholder={values.length ? '' : 'admin'}
          className="min-w-[8rem] flex-1 bg-transparent px-1 py-0.5 font-mono text-xs focus:outline-none"
        />
      </div>
    </div>
  );
}

function PanelSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Loading single sign-on settings">
      {[0, 1, 2].map((item) => (
        <div key={item} className="space-y-3 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3 w-72" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ))}
    </div>
  );
}

export default function SsoGroupSyncPanel() {
  const { pushToast } = useToast();
  const { data, loading, error, refetch } = useApi((signal) => ssoGroupSync.get(signal), []);
  const [saved, setSaved] = useState<SsoGroupSyncSettings>(DEFAULT_SSO_GROUP_SYNC);
  const [draft, setDraft] = useState<SsoGroupSyncSettings>(DEFAULT_SSO_GROUP_SYNC);
  const [targets, setTargets] = useState<Map<string, SsoGroupTarget>>(new Map());
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [confirmRemoval, setConfirmRemoval] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  useEffect(() => {
    if (!data) return;
    setSaved(data.settings);
    setDraft(data.settings);
    setTargets(targetIndex(data.targets));
  }, [data]);

  const dirty = !sameSsoGroupSync(draft, saved);
  const problem = useMemo(() => validateSsoGroupSync(draft), [draft]);
  const update = (patch: Partial<SsoGroupSyncSettings>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setSaveError(null);
  };

  const save = async () => {
    if (problem) return;
    setSaving(true);
    setSaveError(null);
    try {
      const response = await ssoGroupSync.update(buildSsoGroupSyncPayload(draft));
      setSaved(response.settings);
      setDraft(response.settings);
      setTargets(targetIndex(response.targets));
      pushToast({ tone: 'success', message: 'Single sign-on settings saved.' });
    } catch (err) {
      setSaveError(errorMessage(err, 'Could not save the settings.'));
    } finally {
      setSaving(false);
    }
  };

  if (loading && !data) return <PanelSkeleton />;
  if (error && !data) return <PanelError message={errorMessage(error, 'Could not load single sign-on settings.')} onRetry={refetch} />;

  const provider = data?.provider;
  return (
    <>
      <SettingsSection title="Identity provider" description="Set in the config file. Read only here." icon={KeyRound}>
        <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-gray-500">Status</dt>
            <dd className={`mt-0.5 font-medium ${provider?.enabled ? 'text-emerald-700' : 'text-gray-500'}`}>{provider?.enabled ? 'Connected' : 'SSO is off'}</dd>
          </div>
          <div>
            <dt className="text-xs text-gray-500">Provider</dt>
            <dd className="mt-0.5 font-medium text-gray-900">{provider?.provider ?? 'oidc'}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs text-gray-500">Authorize URL</dt>
            <dd className="mt-0.5 truncate font-mono text-xs text-gray-700">{provider?.authorize_url || 'Not set'}</dd>
          </div>
        </dl>
      </SettingsSection>

      <SettingsSection title="Group sync" description="Give access from a claim in the SSO token. Runs at each SSO sign-in." icon={ShieldCheck}>
        <div className="space-y-5">
          <SwitchRow
            title="Turn on group sync"
            description="When off, SSO sign-in does not read the claim and does not change access."
            checked={draft.enabled}
            onChange={(enabled) => update({ enabled })}
          />
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <SettingsField label="Claim name" htmlFor="sso-claim" hint="Read from the ID token, then userinfo. Use dots for nested claims, for example realm_access.roles.">
              <input id="sso-claim" value={draft.claim} onChange={(event) => update({ claim: event.target.value })} className={`${INPUT_CLASS} font-mono`} maxLength={128} autoComplete="off" />
            </SettingsField>
            <SettingsField label="Values that grant platform admin" htmlFor="sso-admin-values" hint="Press Enter to add a value. The config file admin email list still works.">
              <AdminValuesInput values={draft.admin_values} onChange={(admin_values) => update({ admin_values })} />
            </SettingsField>
          </div>
          <div className="space-y-1 border-t border-gray-100 pt-3">
            <SwitchRow
              title="Require a mapped value to sign in"
              description="People whose token has no mapped value cannot sign in with SSO. When off, they sign in with the default access."
              checked={draft.require_mapped_value}
              onChange={(require_mapped_value) => update({ require_mapped_value })}
            />
            <SwitchRow
              title="Remove access the identity provider no longer grants"
              description="For mapped teams and organizations, and for platform admin. Removing a membership also deletes the person's API keys there. The last platform admin is never removed."
              checked={draft.remove_unlisted}
              onChange={(value) => (value ? setConfirmRemoval(true) : update({ remove_unlisted: false }))}
              warning
            />
          </div>
        </div>
      </SettingsSection>

      <SettingsSection title="Mappings" description="Each claim value gives a team or organization role. When several values give the same target, the highest role wins." icon={ListTree}>
        <div className="space-y-4">
          <div className="flex flex-wrap justify-end gap-2">
            <Button size="sm" variant="outline" onClick={() => setPreviewOpen(true)}><FlaskConical className="h-4 w-4" />Test a sign-in</Button>
            <Button size="sm" onClick={() => setAddOpen(true)}><Plus className="h-4 w-4" />Add mapping</Button>
          </div>
          <SsoMappingTable
            mappings={draft.mappings}
            targets={targets}
            onChange={(index, mapping) => update({ mappings: draft.mappings.map((item, i) => (i === index ? mapping : item)) })}
            onRemove={(index) => update({ mappings: draft.mappings.filter((_, i) => i !== index) })}
          />
        </div>
      </SettingsSection>

      {previewOpen && <SsoPreviewPanel settings={draft} onClose={() => setPreviewOpen(false)} />}

      <div className="sticky bottom-4 z-10 flex flex-wrap items-center justify-end gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
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
          <Save className="h-4 w-4" />Save changes
        </Button>
      </div>

      <AddSsoMappingDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        validate={(mapping: SsoGroupMapping) => validateSsoGroupSync({ ...draft, mappings: [...draft.mappings, mapping] })}
        onAdd={(mapping, target) => {
          update({ mappings: [...draft.mappings, mapping] });
          setTargets((current) => new Map(current).set(`${target.kind}:${target.id}`, target));
        }}
      />
      <ConfirmDialog
        open={confirmRemoval}
        title="Remove access the identity provider no longer grants?"
        description="At each SSO sign-in, DeltaLLM removes mapped team and organization memberships and platform admin that the token no longer gives. It also deletes the person's API keys in removed teams and organizations. Manual memberships in unmapped teams stay. The change applies when you save."
        confirmLabel="Turn on removal"
        destructive
        onConfirm={() => { update({ remove_unlisted: true }); setConfirmRemoval(false); }}
        onClose={() => setConfirmRemoval(false)}
      />
    </>
  );
}
