import { ChevronDown, Search, X } from 'lucide-react';
import { type KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from 'react';
import type {
  AssetAccessRole,
  AssetSubjectType,
  ManagedAssetGrantInput,
} from '../lib/api';
import { managedAssets } from '../lib/api';
import type { ManagedAssetAudienceOption } from '../lib/useManagedAssetAudienceOptions';

interface ManagedAssetAccessFieldsProps {
  grants: ManagedAssetGrantInput[];
  teamOptions: ManagedAssetAudienceOption[];
  organizationOptions: ManagedAssetAudienceOption[];
  allowPublic: boolean;
  audiencesLoading?: boolean;
  audiencesError?: string | null;
  onRetryAudiences?: () => void;
  error?: string | null;
  disabled?: boolean;
  onChange: (grants: ManagedAssetGrantInput[]) => void;
}

type AccessTab = AssetSubjectType;

const tabs: Array<{ value: AccessTab; label: string }> = [
  { value: 'team', label: 'Teams' },
  { value: 'organization', label: 'Organizations' },
  { value: 'public', label: 'Public' },
];

function grantKey(grant: ManagedAssetGrantInput): string {
  return `${grant.subject_type}:${grant.subject_id || ''}`;
}

function accessLabel(grants: ManagedAssetGrantInput[]): string {
  if (grants.length === 0) return 'Private';
  if (grants.some((grant) => grant.subject_type === 'public')) return 'Public';
  const teamCount = grants.filter((grant) => grant.subject_type === 'team').length;
  const organizationCount = grants.length - teamCount;
  if (teamCount && !organizationCount) {
    return `Shared · ${teamCount} team${teamCount === 1 ? '' : 's'}`;
  }
  if (organizationCount && !teamCount) {
    return `Shared · ${organizationCount} organization${organizationCount === 1 ? '' : 's'}`;
  }
  return `Shared · ${grants.length} audiences`;
}

export default function ManagedAssetAccessFields({
  grants,
  teamOptions,
  organizationOptions,
  allowPublic,
  audiencesLoading = false,
  audiencesError = null,
  onRetryAudiences,
  error = null,
  disabled = false,
  onChange,
}: ManagedAssetAccessFieldsProps) {
  const fieldId = useId();
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState<AccessTab>('team');
  const [search, setSearch] = useState('');
  const [remoteOptions, setRemoteOptions] = useState<ManagedAssetAudienceOption[]>([]);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const remoteRequestId = useRef(0);

  const pinned = useMemo(
    () => grants.filter((grant) => grant.subject_type === activeTab),
    [activeTab, grants],
  );
  const baseOptions = activeTab === 'team' ? teamOptions : organizationOptions;
  const options = useMemo(() => {
    const byId = new Map<string, ManagedAssetAudienceOption>();
    for (const option of [...baseOptions, ...remoteOptions]) byId.set(option.id, option);
    return [...byId.values()];
  }, [baseOptions, remoteOptions]);
  const pinnedIds = useMemo(() => new Set(pinned.map((grant) => grant.subject_id)), [pinned]);
  const normalizedSearch = search.trim().toLowerCase();
  const matchingOptions = useMemo(
    () => options
      .filter((option) => !pinnedIds.has(option.id))
      .filter((option) => !normalizedSearch
        || option.label.toLowerCase().includes(normalizedSearch)
        || option.id.toLowerCase().includes(normalizedSearch))
      .slice(0, 3),
    [normalizedSearch, options, pinnedIds],
  );

  const replaceGrant = (target: ManagedAssetGrantInput, role: AssetAccessRole) => {
    onChange(grants.map((grant) => grantKey(grant) === grantKey(target)
      ? { ...grant, access_role: role }
      : grant));
  };

  const removeGrant = (target: ManagedAssetGrantInput) => {
    onChange(grants.filter((grant) => grantKey(grant) !== grantKey(target)));
  };

  const addGrant = (subjectType: AccessTab, subjectId: string | null) => {
    const nextGrant: ManagedAssetGrantInput = {
      subject_type: subjectType,
      subject_id: subjectId,
      access_role: 'reader',
    };
    if (grants.some((grant) => grantKey(grant) === grantKey(nextGrant))) return;
    onChange([...grants, nextGrant]);
  };

  const publicGrant = grants.find((grant) => grant.subject_type === 'public') || null;

  useEffect(() => {
    if (!expanded || activeTab === 'public') return undefined;
    const selectedIds = pinned
      .map((grant) => grant.subject_id)
      .filter((value): value is string => Boolean(value));
    const hasMissingPinned = selectedIds.some((id) => !baseOptions.some((option) => option.id === id));
    if (!search.trim() && !hasMissingPinned) return undefined;
    const requestId = ++remoteRequestId.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setRemoteLoading(true);
      setRemoteError(null);
      void managedAssets.audienceOptions(
        activeTab,
        { search: search.trim(), selectedIds, limit: 3 },
        controller.signal,
      ).then((result) => {
        if (remoteRequestId.current !== requestId) return;
        setRemoteOptions(result.data);
      }).catch((caught: unknown) => {
        if (!controller.signal.aborted && remoteRequestId.current === requestId) {
          setRemoteError(caught instanceof Error ? caught.message : 'Unable to search audiences.');
        }
      }).finally(() => {
        if (remoteRequestId.current === requestId) setRemoteLoading(false);
      });
    }, 200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [activeTab, baseOptions, expanded, pinned, search]);

  const activateTabFromKeyboard = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = tabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    remoteRequestId.current += 1;
    setActiveTab(tabs[nextIndex].value);
    setSearch('');
    setRemoteOptions([]);
    setRemoteError(null);
    setRemoteLoading(false);
    const tabButtons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    tabButtons?.[nextIndex]?.focus();
  };

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50"
        onClick={() => {
          if (expanded) {
            remoteRequestId.current += 1;
            setRemoteOptions([]);
            setRemoteError(null);
            setRemoteLoading(false);
          }
          setExpanded((value) => !value);
        }}
        aria-expanded={expanded}
      >
        <span>
          <span className="block text-sm font-semibold text-slate-900">Sharing &amp; Access</span>
          <span className="mt-0.5 block text-xs text-slate-500">
            You remain Owner and control who can use or edit this asset.
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${grants.length === 0 ? 'bg-slate-100 text-slate-700' : 'bg-blue-50 text-blue-700'}`}>
            {accessLabel(grants)}
          </span>
          <ChevronDown className={`h-4 w-4 text-slate-500 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </span>
      </button>

      {expanded ? (
        <div className="border-t border-slate-200 px-4 pb-4 pt-3">
          <div className="inline-flex rounded-lg border border-gray-300 bg-white p-0.5" role="tablist" aria-label="Sharing audience">
            {tabs.map((tab, index) => {
              const grantCount = grants.filter((grant) => grant.subject_type === tab.value).length;
              return (
                <button
                  key={tab.value}
                  id={`${fieldId}-${tab.value}-tab`}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === tab.value}
                  aria-controls={`${fieldId}-${tab.value}-panel`}
                  tabIndex={activeTab === tab.value ? 0 : -1}
                  onClick={() => {
                    remoteRequestId.current += 1;
                    setActiveTab(tab.value);
                    setSearch('');
                    setRemoteOptions([]);
                    setRemoteError(null);
                    setRemoteLoading(false);
                  }}
                  onKeyDown={(event) => activateTabFromKeyboard(event, index)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${activeTab === tab.value ? 'bg-brand-primary text-brand-on-primary shadow-sm' : 'text-gray-600 hover:text-gray-900'}`}
                >
                  {tab.label}
                  {grantCount > 0 ? <span className="ml-1.5 opacity-80">{grantCount}</span> : null}
                </button>
              );
            })}
          </div>

          {tabs.map((tab) => (
            <div
              key={tab.value}
              id={`${fieldId}-${tab.value}-panel`}
              role="tabpanel"
              aria-labelledby={`${fieldId}-${tab.value}-tab`}
              hidden={activeTab !== tab.value}
            >
            {activeTab === tab.value ? <>
            {activeTab === 'public' ? (
            <div className="mt-4 rounded-lg border border-slate-200 px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-900">Anyone on the platform</div>
                  <div className="mt-0.5 text-xs text-slate-500">Public access is always read-only.</div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={Boolean(publicGrant)}
                  disabled={disabled || !allowPublic}
                  onClick={() => publicGrant ? removeGrant(publicGrant) : addGrant('public', null)}
                  className={`relative h-6 w-11 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${publicGrant ? 'bg-brand-primary' : 'bg-slate-300'}`}
                >
                  <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${publicGrant ? 'translate-x-5' : 'translate-x-0'}`} />
                </button>
              </div>
              {!allowPublic ? <p className="mt-2 text-xs text-amber-700">Only a platform admin can enable Public access.</p> : null}
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              {pinned.length > 0 ? (
                <div className="space-y-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Has access</p>
                  {pinned.map((grant) => {
                    const option = options.find((item) => item.id === grant.subject_id);
                    return (
                      <div key={grantKey(grant)} className="flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2">
                        <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900">{option?.label || grant.subject_id}</span>
                        <select
                          aria-label={`Access for ${option?.label || grant.subject_id}`}
                          value={grant.access_role}
                          disabled={disabled}
                          onChange={(event) => replaceGrant(grant, event.target.value as AssetAccessRole)}
                          className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs"
                        >
                          <option value="reader">Read</option>
                          <option value="editor">Read / Write</option>
                        </select>
                        <button
                          type="button"
                          aria-label={`Remove ${option?.label || grant.subject_id}`}
                          onClick={() => removeGrant(grant)}
                          disabled={disabled}
                          className="rounded p-1 text-slate-500 hover:bg-white hover:text-red-600 disabled:opacity-50"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : null}

              <label htmlFor={`${fieldId}-search`} className="relative block">
                <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                <input
                  id={`${fieldId}-search`}
                  type="search"
                  value={search}
                  disabled={disabled || audiencesLoading || remoteLoading}
                  onChange={(event) => {
                    const value = event.target.value;
                    setSearch(value);
                    if (!value.trim()) {
                      remoteRequestId.current += 1;
                      setRemoteOptions([]);
                      setRemoteError(null);
                      setRemoteLoading(false);
                    }
                  }}
                  placeholder={`Search ${activeTab === 'team' ? 'teams' : 'organizations'}…`}
                  className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm disabled:bg-slate-100"
                />
              </label>

              <div className="space-y-1.5">
                {matchingOptions.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    disabled={disabled}
                    onClick={() => addGrant(activeTab, option.id)}
                    className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-left text-sm hover:border-blue-200 hover:bg-blue-50 disabled:opacity-50"
                  >
                    <span className="min-w-0 truncate text-slate-800">{option.label}</span>
                    <span className="ml-3 shrink-0 text-xs font-semibold text-brand-primary">Add · Read</span>
                  </button>
                ))}
                {!audiencesLoading && matchingOptions.length === 0 ? (
                  <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
                    {options.length === pinned.length ? `No more ${activeTab === 'team' ? 'teams' : 'organizations'} available.` : 'No matches found.'}
                  </p>
                ) : null}
                {audiencesLoading || remoteLoading ? <p className="px-1 text-xs text-slate-500">Loading choices…</p> : null}
              </div>
              <p className="text-[11px] text-slate-400">Up to three search results are shown. Refine the search to find another audience.</p>
            </div>
          )}

            {error ? <p className="mt-3 text-xs font-medium text-red-600">{error}</p> : null}
            {audiencesError ? (
              <div className="mt-3 flex items-center gap-2 text-xs text-red-600">
                <span>Team and organization choices could not be loaded.</span>
                {onRetryAudiences ? (
                  <button type="button" onClick={onRetryAudiences} className="font-semibold underline underline-offset-2">Try again</button>
                ) : null}
              </div>
            ) : null}
            {remoteError ? <p className="mt-3 text-xs text-red-600">{remoteError}</p> : null}
            </> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
