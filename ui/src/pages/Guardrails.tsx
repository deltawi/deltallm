import { useState, useCallback } from 'react';
import { guardrails, organizations, teams, keys, type GuardrailRecord } from '../lib/api';
import { useApi } from '../lib/hooks';
import ScopedGuardrailEditor from '../components/ScopedGuardrailEditor';
import GuardrailFormModal from '../components/guardrails/GuardrailFormModal';
import {
  Plus, Pencil, Trash2, ChevronDown, ChevronRight,
  Building2, Users, Key, Eye, Ban,
  Shield, ShieldCheck, ShieldAlert, ShieldOff,
  Fingerprint, MessageSquareWarning, Code2,
  ToggleLeft, ToggleRight,
  ArrowDownUp, CheckCircle2,
} from 'lucide-react';
import { IndexShell } from '../components/admin/shells';
import type { GuardrailConfigInput } from '../lib/guardrails';

type ScopeKind = 'organization' | 'team' | 'key';
type ScopeTarget = { scope: ScopeKind; id: string; label: string } | null;

function toGuardrailConfigInput(item: GuardrailRecord): GuardrailConfigInput {
  return {
    guardrail_name: item.guardrail_name,
    deltallm_params: item.deltallm_params,
  };
}

const typeStyles: Record<string, { icon: typeof Shield; color: string; bg: string; badge: string }> = {
  pii: { icon: Fingerprint, color: 'text-violet-600', bg: 'bg-violet-50', badge: 'bg-violet-100 text-violet-700' },
  prompt_injection: { icon: ShieldAlert, color: 'text-rose-600', bg: 'bg-rose-50', badge: 'bg-rose-100 text-rose-700' },
  toxicity: { icon: MessageSquareWarning, color: 'text-amber-600', bg: 'bg-amber-50', badge: 'bg-amber-100 text-amber-700' },
  custom: { icon: Code2, color: 'text-blue-600', bg: 'bg-blue-50', badge: 'bg-blue-100 text-blue-700' },
};

const typeLabels: Record<string, string> = {
  pii: 'PII Detection',
  prompt_injection: 'Prompt Injection',
  toxicity: 'Toxicity',
  custom: 'Custom',
};

function getTypeKey(row: GuardrailRecord): string {
  if (row.preset_id === 'presidio_pii' || row.type === 'pii') return 'pii';
  if (row.preset_id === 'lakera_prompt_injection' || row.type === 'prompt_injection') return 'prompt_injection';
  if (row.type === 'toxicity') return 'toxicity';
  if (row.is_custom) return 'custom';
  return row.type || 'custom';
}

function getStyle(row: GuardrailRecord) {
  const key = getTypeKey(row);
  return typeStyles[key] || typeStyles.custom;
}

function getTypeLabel(row: GuardrailRecord) {
  const key = getTypeKey(row);
  return typeLabels[key] || row.type || 'Custom';
}

const scopeIcons: Record<ScopeKind, typeof Building2> = {
  organization: Building2,
  team: Users,
  key: Key,
};

const scopeColors: Record<ScopeKind, { active: string; text: string; dot: string }> = {
  organization: { active: 'bg-indigo-50 border-indigo-200 text-indigo-700', text: 'text-indigo-600', dot: 'bg-indigo-500' },
  team: { active: 'bg-emerald-50 border-emerald-200 text-emerald-700', text: 'text-emerald-600', dot: 'bg-emerald-500' },
  key: { active: 'bg-amber-50 border-amber-200 text-amber-700', text: 'text-amber-600', dot: 'bg-amber-500' },
};

export default function Guardrails() {
  const { data, error, loading, refetch } = useApi(() => guardrails.list(), []);
  const { data: catalog, error: catalogError, loading: catalogLoading } = useApi(() => guardrails.catalog(), []);
  const { data: orgResult } = useApi(() => organizations.list({ limit: 500 }), []);
  const orgList = orgResult?.data || [];
  const { data: teamResult } = useApi(() => teams.list({ limit: 500 }), []);
  const teamList = teamResult?.data || [];
  const { data: keyResult } = useApi(() => keys.list({ limit: 500 }), []);
  const keyList = keyResult?.data || [];

  const [showForm, setShowForm] = useState(false);
  const [editItem, setEditItem] = useState<GuardrailRecord | null>(null);
  const [activeScope, setActiveScope] = useState<ScopeKind>('organization');
  const [scopeTarget, setScopeTarget] = useState<ScopeTarget>(null);

  const items = data || [];
  const presidioCapability = catalog?.capabilities.presidio;

  const activeCount = items.filter((g) => g.editor.default_on).length;
  const preCallCount = items.filter((g) => g.mode === 'pre_call').length;
  const postCallCount = items.filter((g) => g.mode === 'post_call').length;

  const saveAll = async (updated: GuardrailConfigInput[]) => {
    await guardrails.update({ guardrails: updated });
    refetch();
  };

  const handleSave = async (payload: GuardrailConfigInput) => {
    const current = items.map(toGuardrailConfigInput);
    const updated = editItem
      ? current.map((item) => (item.guardrail_name === editItem.guardrail_name ? payload : item))
      : [...current, payload];
    await saveAll(updated);
    setShowForm(false);
    setEditItem(null);
  };

  const handleDelete = async (name: string) => {
    if (!confirm('Delete this guardrail?')) return;
    const updated = items
      .filter((item) => item.guardrail_name !== name)
      .map(toGuardrailConfigInput);
    await saveAll(updated);
  };

  const scopeEntities = (scope: ScopeKind) => {
    if (scope === 'organization') return orgList.map((o: any) => ({ id: o.organization_id, label: o.organization_alias || o.organization_id }));
    if (scope === 'team') return teamList.map((t: any) => ({ id: t.team_id, label: t.team_alias || t.team_id }));
    return keyList.map((k: any) => ({ id: k.token, label: k.key_name || k.key_alias || k.token?.slice(0, 12) }));
  };

  const currentEntities = scopeEntities(activeScope);
  const ScopeIcon = scopeIcons[activeScope];
  const colors = scopeColors[activeScope];

  const loadError = error instanceof Error ? error.message : catalogError instanceof Error ? catalogError.message : null;

  return (
    <IndexShell
      title="Guardrails"
      titleIcon={Shield}
      count={items.length || null}
      description="Configure safety guardrails to filter, block, or audit requests and responses across your gateway."
      action={(
        <button
          onClick={() => {
            setEditItem(null);
            setShowForm(true);
          }}
          disabled={catalogLoading || !catalog}
          className="inline-flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Plus className="h-4 w-4" /> Add Guardrail
        </button>
      )}
    >
      {loadError ? (
        <div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-5 py-4 text-sm text-red-700 shadow-sm">
          {loadError}
        </div>
      ) : null}

      <div className="mb-6 grid grid-cols-4 gap-4">
        <div className="rounded-xl border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gray-100">
              <Shield className="h-[18px] w-[18px] text-gray-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{items.length}</p>
              <p className="text-xs text-gray-500">Total Guardrails</p>
            </div>
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-green-50">
              <ShieldCheck className="h-[18px] w-[18px] text-green-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{activeCount}</p>
              <p className="text-xs text-gray-500">Active</p>
            </div>
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50">
              <ArrowDownUp className="h-[18px] w-[18px] text-blue-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{preCallCount}</p>
              <p className="text-xs text-gray-500">Pre-call</p>
            </div>
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-50">
              <Eye className="h-[18px] w-[18px] text-amber-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{postCallCount}</p>
              <p className="text-xs text-gray-500">Post-call</p>
            </div>
          </div>
        </div>
      </div>

      {presidioCapability?.engine_mode === 'full' ? (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
          <CheckCircle2 className="mt-0.5 h-[18px] w-[18px] shrink-0 text-emerald-600" />
          <div>
            <p className="text-sm font-medium text-emerald-800">Full Presidio engine installed</p>
            <p className="mt-0.5 text-xs text-emerald-700">PII guardrails can use the complete built-in entity set including NLP-powered recognition.</p>
          </div>
        </div>
      ) : presidioCapability ? (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
          <ShieldAlert className="mt-0.5 h-[18px] w-[18px] shrink-0 text-amber-600" />
          <div>
            <p className="text-sm font-medium text-amber-800">Presidio running in limited fallback mode</p>
            <p className="mt-0.5 text-xs text-amber-700">
              Only regex-backed entities available: {presidioCapability.fallback_supported_entities.join(', ')}.
            </p>
          </div>
        </div>
      ) : null}

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50/60">
              <th className="px-5 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Type</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Mode</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Action</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">Threshold</th>
              <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wide text-gray-500">Default</th>
              <th className="w-24 px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading || catalogLoading ? (
              <tr>
                <td colSpan={7} className="px-5 py-12 text-center text-sm text-gray-400">Loading guardrails…</td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-5 py-12 text-center text-sm text-gray-400">No guardrails configured</td>
              </tr>
            ) : items.map((row) => {
              const style = getStyle(row);
              const Icon = style.icon;
              return (
                <tr key={row.guardrail_name} className={`group transition-colors hover:bg-gray-50/50 ${!row.editor.default_on ? 'opacity-60' : ''}`}>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${style.bg}`}>
                        <Icon className={`h-4 w-4 ${style.color}`} />
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-900">{row.guardrail_name}</p>
                        {row.is_custom && row.class_path ? (
                          <p className="truncate text-xs text-gray-400">{row.class_path}</p>
                        ) : null}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${style.badge}`}>
                      {getTypeLabel(row)}
                    </span>
                  </td>
                  <td className="px-4 py-3.5">
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs text-gray-700">
                      {row.mode === 'pre_call' ? 'Pre-call' : 'Post-call'}
                    </span>
                  </td>
                  <td className="px-4 py-3.5">
                    {row.default_action === 'block' ? (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600">
                        <Ban className="h-3.5 w-3.5" /> Block
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-blue-600">
                        <Eye className="h-3.5 w-3.5" /> Log
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3.5">
                    <span className="text-sm tabular-nums text-gray-700">{row.threshold.toFixed(1)}</span>
                  </td>
                  <td className="px-4 py-3.5 text-center">
                    {row.editor.default_on ? (
                      <ToggleRight className="mx-auto h-6 w-6 text-violet-600" />
                    ) : (
                      <ToggleLeft className="mx-auto h-6 w-6 text-gray-300" />
                    )}
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="flex items-center justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                      <button
                        onClick={() => {
                          setEditItem(row);
                          setShowForm(true);
                        }}
                        className="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                        aria-label={`Edit ${row.guardrail_name}`}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        onClick={() => handleDelete(row.guardrail_name)}
                        className="rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-500"
                        aria-label={`Delete ${row.guardrail_name}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <GuardrailFormModal
        open={showForm}
        item={editItem}
        catalog={catalog}
        onClose={() => {
          setShowForm(false);
          setEditItem(null);
        }}
        onSave={handleSave}
      />

      <div className="mt-8">
        <div className="mb-4">
          <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
            <Shield className="h-[18px] w-[18px] text-gray-500" />
            Scoped Assignments
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            Override guardrail settings per organization, team, or API key. Resolution: Global → Org → Team → Key.
          </p>
        </div>

        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
          <div className="flex border-b border-gray-200">
            {(['organization', 'team', 'key'] as ScopeKind[]).map((scope) => {
              const SIcon = scopeIcons[scope];
              const isActive = activeScope === scope;
              return (
                <button
                  key={scope}
                  onClick={() => {
                    setActiveScope(scope);
                    setScopeTarget(null);
                  }}
                  className={`flex items-center gap-2 px-5 py-3 text-sm font-medium transition-colors border-b-2 ${
                    isActive
                      ? `${scopeColors[scope].text} border-current`
                      : 'border-transparent text-gray-500 hover:text-gray-700'
                  }`}
                >
                  <SIcon className="h-4 w-4" />
                  {scope === 'organization' ? 'Organizations' : scope === 'team' ? 'Teams' : 'API Keys'}
                  {scopeEntities(scope).length > 0 && (
                    <span className={`ml-1 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1.5 text-[10px] font-semibold ${isActive ? scopeColors[scope].active : 'bg-gray-100 text-gray-500'}`}>
                      {scopeEntities(scope).length}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex divide-x divide-gray-200" style={{ minHeight: 320 }}>
            <div className="w-64 shrink-0 bg-gray-50/50 p-3">
              {currentEntities.length === 0 ? (
                <p className="px-3 py-4 text-xs text-gray-400">
                  No {activeScope === 'organization' ? 'organizations' : activeScope === 'team' ? 'teams' : 'API keys'} found
                </p>
              ) : (
                <div className="space-y-0.5">
                  {currentEntities.map((entity: any) => (
                    <button
                      key={entity.id}
                      onClick={() =>
                        setScopeTarget({
                          scope: activeScope,
                          id: entity.id,
                          label: entity.label,
                        })
                      }
                      className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                        scopeTarget?.id === entity.id && scopeTarget?.scope === activeScope
                          ? `${colors.active} border font-medium`
                          : 'border border-transparent text-gray-700 hover:bg-gray-100'
                      }`}
                    >
                      <span className="truncate">{entity.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="flex-1 p-5">
              {scopeTarget ? (
                <ScopedGuardrailEditor
                  key={`${scopeTarget.scope}-${scopeTarget.id}`}
                  scope={scopeTarget.scope}
                  entityId={scopeTarget.id}
                  entityLabel={scopeTarget.label}
                  onClose={() => setScopeTarget(null)}
                />
              ) : (
                <div className="flex h-full items-center justify-center text-sm text-gray-400">
                  Select an entity to configure guardrails
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </IndexShell>
  );
}
