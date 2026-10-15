import { useEffect, useState } from 'react';
import { Building2, Search, Users } from 'lucide-react';
import Button from '../../Button';
import Modal from '../../Modal';
import { SettingsField } from '../SettingsSection';
import { organizations, teams } from '../../../lib/api';
import type { SsoGroupMapping, SsoGroupTarget, SsoOrgRole, SsoTeamRole } from '../../../lib/api/ssoGroupSync';
import { errorMessage } from '../../../lib/errorMessage';
import { ORG_ROLE_OPTIONS, TEAM_ROLE_OPTIONS } from '../../../lib/ssoGroupSync';

type TargetKind = 'team' | 'organization';

const INPUT_CLASS = 'w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm transition-all focus:border-transparent focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary';

function useTargetSearch(open: boolean, kind: TargetKind, search: string) {
  const [results, setResults] = useState<SsoGroupTarget[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const query = { search: search.trim() || undefined, limit: 20 };
        if (kind === 'team') {
          const page = await teams.list(query, controller.signal);
          setResults(page.data.map((team) => ({
            id: team.team_id,
            kind: 'team',
            name: team.team_alias ?? null,
            organization_id: team.organization_id ?? null,
            organization_name: team.organization_name ?? null,
            status: 'ok',
          })));
        } else {
          const page = await organizations.list(query, controller.signal);
          setResults(page.data.map((org) => ({
            id: org.organization_id,
            kind: 'organization',
            name: org.organization_name ?? null,
            status: org.lifecycle_state === 'active' ? 'ok' : 'inactive_organization',
          })));
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(errorMessage(err, 'Could not load the list.'));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 250);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, kind, search]);

  return { results, loading, error };
}

type DialogProps = {
  open: boolean;
  onClose: () => void;
  onAdd: (mapping: SsoGroupMapping, target: SsoGroupTarget) => void;
  validate: (mapping: SsoGroupMapping) => string | null;
};

/** Mounted only while open, so each opening starts with an empty form. */
export default function AddSsoMappingDialog(props: DialogProps) {
  return props.open ? <AddSsoMappingForm {...props} /> : null;
}

function AddSsoMappingForm({
  open,
  onClose,
  onAdd,
  validate,
}: DialogProps) {
  const [value, setValue] = useState('');
  const [kind, setKind] = useState<TargetKind>('team');
  const [search, setSearch] = useState('');
  const [target, setTarget] = useState<SsoGroupTarget | null>(null);
  const [teamRole, setTeamRole] = useState<SsoTeamRole>('team_developer');
  const [orgRole, setOrgRole] = useState<SsoOrgRole>('org_member');
  const [formError, setFormError] = useState<string | null>(null);
  const { results, loading, error } = useTargetSearch(open, kind, search);

  const switchKind = (next: TargetKind) => {
    setKind(next);
    setTarget(null);
    setSearch('');
  };

  const submit = () => {
    if (!target) {
      setFormError(`Pick a ${kind === 'team' ? 'team' : 'organization'}.`);
      return;
    }
    const mapping: SsoGroupMapping = kind === 'team'
      ? { value: value.trim(), team_id: target.id, team_role: teamRole }
      : { value: value.trim(), organization_id: target.id, org_role: orgRole };
    const problem = validate(mapping);
    if (problem) {
      setFormError(problem);
      return;
    }
    onAdd(mapping, target);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add mapping"
      description="People whose token has this claim value get this access at each SSO sign-in."
      footer={(
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={submit} disabled={!value.trim() || !target}>Add mapping</Button>
        </div>
      )}
    >
      <div className="space-y-4">
        <SettingsField label="Claim value" htmlFor="sso-mapping-value" hint="The exact value the identity provider sends, for example team-ml.">
          <input
            id="sso-mapping-value"
            value={value}
            onChange={(event) => { setValue(event.target.value); setFormError(null); }}
            className={`${INPUT_CLASS} font-mono`}
            autoComplete="off"
            maxLength={256}
          />
        </SettingsField>

        <div>
          <p className="mb-1 text-sm font-medium text-gray-700">Gives access to</p>
          <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5" role="group" aria-label="Target type">
            {([['team', 'Team', Users], ['organization', 'Organization', Building2]] as const).map(([id, label, Icon]) => (
              <button
                key={id}
                type="button"
                aria-pressed={kind === id}
                onClick={() => switchKind(id)}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium ${kind === id ? 'bg-white text-brand-secondary-ink shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={kind === 'team' ? 'Search teams' : 'Search organizations'}
              aria-label={kind === 'team' ? 'Search teams' : 'Search organizations'}
              className={`${INPUT_CLASS} pl-9`}
            />
          </div>
          <div className="mt-2 max-h-56 overflow-y-auto rounded-lg border border-gray-100" role="listbox" aria-label="Results">
            {loading && results.length === 0 && <p className="px-3 py-3 text-xs text-gray-400">Loading…</p>}
            {error && <p className="px-3 py-3 text-xs text-red-600">{error}</p>}
            {!loading && !error && results.length === 0 && (
              <p className="px-3 py-3 text-xs text-gray-400">No {kind === 'team' ? 'teams' : 'organizations'} found.</p>
            )}
            {results.map((item) => {
              const selected = target?.id === item.id;
              const disabled = item.status !== 'ok';
              return (
                <button
                  key={item.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  disabled={disabled}
                  onClick={() => { setTarget(item); setFormError(null); }}
                  className={`flex w-full items-center justify-between gap-3 border-b border-gray-50 px-3 py-2 text-left text-sm last:border-b-0 disabled:cursor-not-allowed disabled:opacity-50 ${selected ? 'bg-brand-secondary-soft text-brand-secondary-ink' : 'hover:bg-gray-50'}`}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{item.name || item.id}</span>
                    <span className="block truncate font-mono text-[11px] text-gray-400">
                      {item.id}{item.organization_name ? ` · ${item.organization_name}` : ''}
                    </span>
                  </span>
                  {disabled && <span className="shrink-0 text-[11px] text-gray-500">Not active</span>}
                </button>
              );
            })}
          </div>
        </div>

        <SettingsField label="Role" htmlFor="sso-mapping-role" hint="When several values give the same team or organization, the highest role wins.">
          {kind === 'team' ? (
            <select id="sso-mapping-role" value={teamRole} onChange={(event) => setTeamRole(event.target.value as SsoTeamRole)} className={INPUT_CLASS}>
              {TEAM_ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          ) : (
            <select id="sso-mapping-role" value={orgRole} onChange={(event) => setOrgRole(event.target.value as SsoOrgRole)} className={INPUT_CLASS}>
              {ORG_ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          )}
        </SettingsField>

        {formError && <p className="text-xs text-red-600" role="alert">{formError}</p>}
      </div>
    </Modal>
  );
}
