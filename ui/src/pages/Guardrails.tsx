import { useState } from 'react';
import { guardrails, organizations, teams, keys, type GuardrailRecord } from '../lib/api';
import { useApi } from '../lib/hooks';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import ScopedGuardrailEditor from '../components/ScopedGuardrailEditor';
import GuardrailFormModal from '../components/guardrails/GuardrailFormModal';
import { Plus, Pencil, Trash2, Shield, Building2, Users, Key } from 'lucide-react';
import { ContentCard, IndexShell } from '../components/admin/shells';
import type { GuardrailConfigInput } from '../lib/guardrails';

type ScopeTarget = { scope: 'organization' | 'team' | 'key'; id: string; label: string } | null;

function toGuardrailConfigInput(item: GuardrailRecord): GuardrailConfigInput {
  return {
    guardrail_name: item.guardrail_name,
    deltallm_params: item.deltallm_params,
  };
}

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
  const [scopeTarget, setScopeTarget] = useState<ScopeTarget>(null);

  const items = data || [];
  const presidioCapability = catalog?.capabilities.presidio;

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

  const columns = [
    {
      key: 'guardrail_name',
      header: 'Name',
      render: (row: GuardrailRecord) => (
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-blue-500" />
          <span className="font-medium text-slate-900">{row.guardrail_name}</span>
        </div>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      render: (row: GuardrailRecord) => (
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-900">{row.type}</div>
          {row.is_custom && row.class_path ? (
            <div className="truncate text-xs text-slate-500">{row.class_path}</div>
          ) : null}
        </div>
      ),
    },
    {
      key: 'mode',
      header: 'Mode',
      render: (row: GuardrailRecord) => (
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
          {row.mode === 'pre_call' ? 'Pre-call' : 'Post-call'}
        </span>
      ),
    },
    {
      key: 'default_action',
      header: 'Action',
      render: (row: GuardrailRecord) => (
        <span className="text-xs text-slate-700">{row.default_action === 'block' ? 'Block' : 'Log only'}</span>
      ),
    },
    {
      key: 'threshold',
      header: 'Threshold',
      render: (row: GuardrailRecord) => row.threshold.toFixed(1),
    },
    {
      key: 'enabled',
      header: 'Default',
      render: (row: GuardrailRecord) => (
        <StatusBadge status={row.editor.default_on ? 'enabled' : 'disabled'} />
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (row: GuardrailRecord) => (
        <div className="flex gap-1">
          <button
            onClick={() => {
              setEditItem(row);
              setShowForm(true);
            }}
            className="rounded-lg p-1.5 hover:bg-slate-100"
            aria-label={`Edit ${row.guardrail_name}`}
          >
            <Pencil className="h-4 w-4 text-slate-500" />
          </button>
          <button
            onClick={() => handleDelete(row.guardrail_name)}
            className="rounded-lg p-1.5 hover:bg-red-50"
            aria-label={`Delete ${row.guardrail_name}`}
          >
            <Trash2 className="h-4 w-4 text-red-500" />
          </button>
        </div>
      ),
    },
  ];

  const loadError = error instanceof Error ? error.message : catalogError instanceof Error ? catalogError.message : null;

  return (
    <IndexShell
      title="Guardrails"
      count={items.length || null}
      description="Configure reusable safety guardrails and assign them to organizations, teams, or API keys."
      action={(
        <button
          onClick={() => {
            setEditItem(null);
            setShowForm(true);
          }}
          disabled={catalogLoading || !catalog}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
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

      {presidioCapability?.engine_mode === 'full' ? (
        <div className="mb-6 rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-800 shadow-sm">
          Full Presidio engine is installed. Presidio guardrails can use the complete built-in entity set.
        </div>
      ) : presidioCapability ? (
        <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-800 shadow-sm">
          Presidio packages are not installed. Guardrails will run in limited regex fallback mode with these entities:
          {' '}
          {presidioCapability.fallback_supported_entities.join(', ')}.
        </div>
      ) : null}

      <ContentCard>
        <DataTable columns={columns} data={items} loading={loading || catalogLoading} emptyMessage="No guardrails configured" />
      </ContentCard>

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
        <h2 className="mb-1 text-lg font-semibold text-slate-900">Scoped Assignments</h2>
        <p className="mb-4 text-sm text-slate-500">Assign guardrails at the organization, team, or API-key level. Scoped assignments resolve hierarchically: Global → Organization → Team → API Key.</p>

        <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
          <Card>
            <div className="p-4">
              <div className="mb-3 flex items-center gap-2">
                <Building2 className="h-4 w-4 text-indigo-600" />
                <h3 className="text-sm font-medium text-slate-900">Organizations</h3>
              </div>
              {orgList.length === 0 ? (
                <p className="text-xs text-slate-400">No organizations</p>
              ) : (
                <div className="max-h-48 space-y-1 overflow-y-auto">
                  {orgList.map((org: any) => (
                    <button
                      key={org.organization_id}
                      onClick={() =>
                        setScopeTarget({
                          scope: 'organization',
                          id: org.organization_id,
                          label: org.organization_alias || org.organization_id,
                        })
                      }
                      className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                        scopeTarget?.scope === 'organization' && scopeTarget?.id === org.organization_id
                          ? 'bg-indigo-50 font-medium text-indigo-700'
                          : 'text-slate-700 hover:bg-indigo-50'
                      }`}
                    >
                      {org.organization_alias || org.organization_id}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <Card>
            <div className="p-4">
              <div className="mb-3 flex items-center gap-2">
                <Users className="h-4 w-4 text-emerald-600" />
                <h3 className="text-sm font-medium text-slate-900">Teams</h3>
              </div>
              {teamList.length === 0 ? (
                <p className="text-xs text-slate-400">No teams</p>
              ) : (
                <div className="max-h-48 space-y-1 overflow-y-auto">
                  {teamList.map((team: any) => (
                    <button
                      key={team.team_id}
                      onClick={() =>
                        setScopeTarget({
                          scope: 'team',
                          id: team.team_id,
                          label: team.team_alias || team.team_id,
                        })
                      }
                      className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                        scopeTarget?.scope === 'team' && scopeTarget?.id === team.team_id
                          ? 'bg-emerald-50 font-medium text-emerald-700'
                          : 'text-slate-700 hover:bg-emerald-50'
                      }`}
                    >
                      {team.team_alias || team.team_id}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <Card>
            <div className="p-4">
              <div className="mb-3 flex items-center gap-2">
                <Key className="h-4 w-4 text-amber-600" />
                <h3 className="text-sm font-medium text-slate-900">API Keys</h3>
              </div>
              {keyList.length === 0 ? (
                <p className="text-xs text-slate-400">No API keys</p>
              ) : (
                <div className="max-h-48 space-y-1 overflow-y-auto">
                  {keyList.map((keyItem: any) => (
                    <button
                      key={keyItem.token}
                      onClick={() =>
                        setScopeTarget({
                          scope: 'key',
                          id: keyItem.token,
                          label: keyItem.key_name || keyItem.key_alias || keyItem.token?.slice(0, 12),
                        })
                      }
                      className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                        scopeTarget?.scope === 'key' && scopeTarget?.id === keyItem.token
                          ? 'bg-amber-50 font-medium text-amber-700'
                          : 'text-slate-700 hover:bg-amber-50'
                      }`}
                    >
                      {keyItem.key_name || keyItem.key_alias || keyItem.token?.slice(0, 12)}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>
        </div>

        {scopeTarget ? (
          <ScopedGuardrailEditor
            key={`${scopeTarget.scope}-${scopeTarget.id}`}
            scope={scopeTarget.scope}
            entityId={scopeTarget.id}
            entityLabel={scopeTarget.label}
            onClose={() => setScopeTarget(null)}
          />
        ) : null}
      </div>
    </IndexShell>
  );
}
