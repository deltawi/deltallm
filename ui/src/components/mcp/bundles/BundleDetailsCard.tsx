import { useEffect } from 'react';
import { PanelCard } from '../../admin/shells';
import { organizations, teams, type MCPBundleOwnerScopeType } from '../../../lib/api';
import { useApi } from '../../../lib/hooks';
import { ownerScopeLabel, type BundleDraft } from '../../../lib/mcpBundles';

const FIELD = 'control-field-md w-full rounded-lg border border-gray-200 bg-white px-3 transition focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-brand-primary/30 disabled:bg-gray-50 disabled:text-gray-500';
const LABEL = 'mb-1 block text-xs font-medium text-gray-600';

const OWNER_TYPES: ReadonlyArray<{ id: MCPBundleOwnerScopeType; label: string }> = [
  { id: 'global', label: 'Global' },
  { id: 'organization', label: 'Organization' },
  { id: 'team', label: 'Team' },
];

type BundleDetailsCardProps = {
  draft: BundleDraft;
  isEdit: boolean;
  readOnly: boolean;
  canChooseGlobal: boolean;
  onChange: (patch: Partial<BundleDraft>) => void;
  onKeyEdited: () => void;
};

function OwnerPicker({ draft, onChange }: { draft: BundleDraft; onChange: (patch: Partial<BundleDraft>) => void }) {
  const type = draft.owner_scope_type;
  // Load the list for the selected owner type only.
  const orgs = useApi(
    (signal) => (type === 'organization' ? organizations.list({ limit: 200 }, signal) : Promise.resolve(null)),
    [type],
  );
  const teamList = useApi(
    (signal) => (type === 'team' ? teams.list({ limit: 200 }, signal) : Promise.resolve(null)),
    [type],
  );
  const options = type === 'organization'
    ? (orgs.data?.data ?? []).map((item) => ({ id: item.organization_id, label: item.organization_name || item.organization_id }))
    : (teamList.data?.data ?? []).map((item) => ({ id: item.team_id, label: item.team_alias || item.team_id }));
  const loading = type === 'organization' ? orgs.loading : teamList.loading;
  const onlyOption = options.length === 1 ? options[0].id : null;
  // A scoped admin often has one organization or team. Select it for them.
  useEffect(() => {
    if (onlyOption && !draft.owner_scope_id) onChange({ owner_scope_id: onlyOption });
  }, [onlyOption, draft.owner_scope_id, onChange]);
  if (type === 'global') return null;
  return (
    <div>
      <label htmlFor="bundle-owner-id" className={LABEL}>{type === 'organization' ? 'Organization' : 'Team'}</label>
      <select
        id="bundle-owner-id"
        className={FIELD}
        value={draft.owner_scope_id}
        disabled={loading && options.length === 0}
        onChange={(event) => onChange({ owner_scope_id: event.target.value })}
      >
        <option value="">{loading ? 'Loading…' : `Select a ${type}`}</option>
        {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </div>
  );
}

export default function BundleDetailsCard({ draft, isEdit, readOnly, canChooseGlobal, onChange, onKeyEdited }: BundleDetailsCardProps) {
  const ownerTypes = OWNER_TYPES.filter((item) => item.id !== 'global' || canChooseGlobal);
  return (
    <PanelCard>
      <h2 className="text-sm font-semibold text-gray-900">Details</h2>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="bundle-name" className={LABEL}>Name</label>
          <input
            id="bundle-name"
            className={FIELD}
            value={draft.name}
            disabled={readOnly}
            maxLength={200}
            placeholder="Engineering assistant"
            onChange={(event) => onChange({ name: event.target.value })}
          />
        </div>
        <div>
          <label htmlFor="bundle-key" className={LABEL}>Key</label>
          <input
            id="bundle-key"
            className={`${FIELD} font-mono`}
            value={draft.bundle_key}
            disabled={isEdit || readOnly}
            maxLength={63}
            placeholder="eng-assist"
            aria-describedby="bundle-key-help"
            onChange={(event) => {
              onKeyEdited();
              onChange({ bundle_key: event.target.value.toLowerCase() });
            }}
          />
          <p id="bundle-key-help" className="mt-1 text-[11px] text-gray-400">
            {isEdit ? 'You cannot change the key after you create the bundle.' : 'Use a-z, 0-9, "-" and "_". The key must not be the key of an MCP server.'}
          </p>
        </div>
        <div className="sm:col-span-2">
          <label htmlFor="bundle-description" className={LABEL}>Description</label>
          <input
            id="bundle-description"
            className={FIELD}
            value={draft.description}
            disabled={readOnly}
            maxLength={2000}
            placeholder="Optional"
            onChange={(event) => onChange({ description: event.target.value })}
          />
        </div>
        <div className="sm:col-span-2">
          <span className={LABEL} id="bundle-owner-label">Owner scope</span>
          {isEdit ? (
            <p className="text-sm text-gray-700">
              {ownerScopeLabel(draft.owner_scope_type, draft.owner_scope_id)}
              <span className="ml-2 text-[11px] text-gray-400">You cannot change the owner after you create the bundle.</span>
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <div role="radiogroup" aria-labelledby="bundle-owner-label" className="flex w-fit items-center gap-1 rounded-lg border border-gray-200 bg-white p-0.5">
                {ownerTypes.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="radio"
                    aria-checked={draft.owner_scope_type === item.id}
                    onClick={() => onChange({ owner_scope_type: item.id, owner_scope_id: '' })}
                    className={`control-segment rounded-md font-medium transition-colors ${
                      draft.owner_scope_type === item.id ? 'bg-brand-primary text-brand-on-primary shadow-sm' : 'text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <OwnerPicker draft={draft} onChange={onChange} />
            </div>
          )}
          <p className="mt-2 text-[11px] text-gray-400">
            The owner scope controls who can call the bundle. A key outside this scope gets "not found".
          </p>
        </div>
      </div>
    </PanelCard>
  );
}
