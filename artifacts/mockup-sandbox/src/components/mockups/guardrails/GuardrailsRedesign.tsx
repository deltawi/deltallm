import { useState } from 'react';
import {
  Shield, ShieldCheck, ShieldAlert, ShieldOff,
  Plus, Pencil, Trash2, ChevronDown, ChevronRight,
  Building2, Users, Key, X, Eye, Ban,
  Fingerprint, MessageSquareWarning, ScanSearch, Code2,
  ToggleLeft, ToggleRight, Settings2, Layers,
  ArrowDownUp, CheckCircle2, AlertTriangle, Info
} from 'lucide-react';

type GuardrailType = 'pii' | 'prompt_injection' | 'toxicity' | 'custom';
type GuardrailMode = 'pre_call' | 'post_call';
type GuardrailAction = 'block' | 'log';

interface Guardrail {
  name: string;
  type: GuardrailType;
  label: string;
  description: string;
  mode: GuardrailMode;
  action: GuardrailAction;
  threshold: number;
  enabled: boolean;
  entities?: string[];
}

const GUARDRAILS: Guardrail[] = [
  {
    name: 'presidio-pii',
    type: 'pii',
    label: 'PII Detection',
    description: 'Detects personally identifiable information using Presidio NLP engine',
    mode: 'pre_call',
    action: 'block',
    threshold: 0.7,
    enabled: true,
    entities: ['EMAIL_ADDRESS', 'PHONE_NUMBER', 'CREDIT_CARD', 'US_SSN', 'IP_ADDRESS'],
  },
  {
    name: 'lakera-injection',
    type: 'prompt_injection',
    label: 'Prompt Injection',
    description: 'Lakera Guard API detects prompt injection and jailbreak attempts',
    mode: 'pre_call',
    action: 'block',
    threshold: 0.8,
    enabled: true,
  },
  {
    name: 'toxicity-filter',
    type: 'toxicity',
    label: 'Toxicity Filter',
    description: 'Screens responses for harmful, offensive, or inappropriate content',
    mode: 'post_call',
    action: 'log',
    threshold: 0.6,
    enabled: false,
  },
  {
    name: 'custom-compliance',
    type: 'custom',
    label: 'Compliance Check',
    description: 'Custom guardrail enforcing domain-specific regulatory compliance rules',
    mode: 'pre_call',
    action: 'block',
    threshold: 0.5,
    enabled: true,
  },
];

const typeConfig: Record<GuardrailType, { icon: typeof Shield; color: string; bg: string; border: string; badge: string }> = {
  pii: { icon: Fingerprint, color: 'text-violet-600', bg: 'bg-violet-50', border: 'border-violet-200', badge: 'bg-violet-100 text-violet-700' },
  prompt_injection: { icon: ShieldAlert, color: 'text-rose-600', bg: 'bg-rose-50', border: 'border-rose-200', badge: 'bg-rose-100 text-rose-700' },
  toxicity: { icon: MessageSquareWarning, color: 'text-amber-600', bg: 'bg-amber-50', border: 'border-amber-200', badge: 'bg-amber-100 text-amber-700' },
  custom: { icon: Code2, color: 'text-blue-600', bg: 'bg-blue-50', border: 'border-blue-200', badge: 'bg-blue-100 text-blue-700' },
};

const typeLabel: Record<GuardrailType, string> = {
  pii: 'PII Detection',
  prompt_injection: 'Prompt Injection',
  toxicity: 'Toxicity',
  custom: 'Custom',
};

function GuardrailCard({ guardrail, onToggle }: { guardrail: Guardrail; onToggle: () => void }) {
  const config = typeConfig[guardrail.type];
  const Icon = config.icon;

  return (
    <div className={`group relative rounded-xl border bg-white transition-all hover:shadow-md ${guardrail.enabled ? 'border-gray-200' : 'border-gray-100 opacity-70'}`}>
      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3.5">
            <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${config.bg}`}>
              <Icon className={`h-5 w-5 ${config.color}`} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-gray-900">{guardrail.label}</h3>
                <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${config.badge}`}>
                  {typeLabel[guardrail.type]}
                </span>
              </div>
              <p className="mt-1 text-xs text-gray-500 leading-relaxed">{guardrail.description}</p>
            </div>
          </div>

          <button
            onClick={onToggle}
            className="shrink-0 mt-0.5"
            title={guardrail.enabled ? 'Disable guardrail' : 'Enable guardrail'}
          >
            {guardrail.enabled ? (
              <ToggleRight className="h-7 w-7 text-violet-600" />
            ) : (
              <ToggleLeft className="h-7 w-7 text-gray-300" />
            )}
          </button>
        </div>

        <div className="mt-4 flex items-center gap-5">
          <div className="flex items-center gap-1.5">
            <ArrowDownUp className="h-3.5 w-3.5 text-gray-400" />
            <span className="text-xs text-gray-600">
              {guardrail.mode === 'pre_call' ? 'Pre-call' : 'Post-call'}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            {guardrail.action === 'block' ? (
              <Ban className="h-3.5 w-3.5 text-red-500" />
            ) : (
              <Eye className="h-3.5 w-3.5 text-blue-500" />
            )}
            <span className="text-xs text-gray-600">
              {guardrail.action === 'block' ? 'Block' : 'Log only'}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <ScanSearch className="h-3.5 w-3.5 text-gray-400" />
            <span className="text-xs text-gray-600">
              Threshold: {guardrail.threshold}
            </span>
          </div>
          {guardrail.entities && (
            <div className="flex items-center gap-1.5">
              <Layers className="h-3.5 w-3.5 text-gray-400" />
              <span className="text-xs text-gray-600">
                {guardrail.entities.length} entities
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-gray-100 px-5 py-2.5">
        <div className="flex items-center gap-1">
          {guardrail.entities?.slice(0, 3).map((entity) => (
            <span key={entity} className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
              {entity}
            </span>
          ))}
          {guardrail.entities && guardrail.entities.length > 3 && (
            <span className="text-[10px] text-gray-400">+{guardrail.entities.length - 3} more</span>
          )}
        </div>
        <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <button className="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
            <Settings2 className="h-3.5 w-3.5" />
          </button>
          <button className="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button className="rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-500">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

type ScopeKind = 'organization' | 'team' | 'key';
interface ScopeEntity { id: string; label: string; hasOverride: boolean }

const scopeEntities: Record<ScopeKind, ScopeEntity[]> = {
  organization: [
    { id: 'org-1', label: 'Acme Corp', hasOverride: true },
    { id: 'org-2', label: 'Globex Inc', hasOverride: false },
    { id: 'org-3', label: 'Initech Labs', hasOverride: true },
  ],
  team: [
    { id: 'team-1', label: 'ML Engineering', hasOverride: true },
    { id: 'team-2', label: 'Product Analytics', hasOverride: false },
    { id: 'team-3', label: 'Customer Support', hasOverride: false },
    { id: 'team-4', label: 'Research', hasOverride: true },
  ],
  key: [
    { id: 'key-1', label: 'prod-api-v2', hasOverride: false },
    { id: 'key-2', label: 'staging-internal', hasOverride: true },
    { id: 'key-3', label: 'partner-acme', hasOverride: false },
  ],
};

const scopeIcons: Record<ScopeKind, typeof Building2> = {
  organization: Building2,
  team: Users,
  key: Key,
};
const scopeColors: Record<ScopeKind, { active: string; text: string; bg: string; dot: string }> = {
  organization: { active: 'bg-indigo-50 border-indigo-200 text-indigo-700', text: 'text-indigo-600', bg: 'bg-indigo-50', dot: 'bg-indigo-500' },
  team: { active: 'bg-emerald-50 border-emerald-200 text-emerald-700', text: 'text-emerald-600', bg: 'bg-emerald-50', dot: 'bg-emerald-500' },
  key: { active: 'bg-amber-50 border-amber-200 text-amber-700', text: 'text-amber-600', bg: 'bg-amber-50', dot: 'bg-amber-500' },
};

function ScopedAssignmentsPanel() {
  const [activeScope, setActiveScope] = useState<ScopeKind>('organization');
  const [selectedEntity, setSelectedEntity] = useState<ScopeEntity | null>(scopeEntities.organization[0]);
  const entities = scopeEntities[activeScope];
  const colors = scopeColors[activeScope];
  const ScopeIcon = scopeIcons[activeScope];

  return (
    <div className="mt-8">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
            <Shield className="h-4.5 w-4.5 text-gray-500" />
            Scoped Assignments
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            Override guardrail settings per organization, team, or API key. Resolution: Global → Org → Team → Key.
          </p>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className="flex border-b border-gray-200">
          {(['organization', 'team', 'key'] as ScopeKind[]).map((scope) => {
            const Icon = scopeIcons[scope];
            const isActive = activeScope === scope;
            const overrideCount = scopeEntities[scope].filter((e) => e.hasOverride).length;
            return (
              <button
                key={scope}
                onClick={() => {
                  setActiveScope(scope);
                  setSelectedEntity(scopeEntities[scope][0] || null);
                }}
                className={`flex items-center gap-2 px-5 py-3 text-sm font-medium transition-colors border-b-2 ${
                  isActive
                    ? `${scopeColors[scope].text} border-current`
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <Icon className="h-4 w-4" />
                {scope === 'organization' ? 'Organizations' : scope === 'team' ? 'Teams' : 'API Keys'}
                {overrideCount > 0 && (
                  <span className={`ml-1 inline-flex h-4.5 min-w-[1.125rem] items-center justify-center rounded-full px-1.5 text-[10px] font-semibold ${isActive ? scopeColors[scope].active : 'bg-gray-100 text-gray-500'}`}>
                    {overrideCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div className="flex divide-x divide-gray-200" style={{ minHeight: 320 }}>
          <div className="w-64 shrink-0 bg-gray-50/50 p-3">
            <div className="space-y-0.5">
              {entities.map((entity) => (
                <button
                  key={entity.id}
                  onClick={() => setSelectedEntity(entity)}
                  className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                    selectedEntity?.id === entity.id
                      ? `${colors.active} border font-medium`
                      : 'text-gray-700 hover:bg-gray-100 border border-transparent'
                  }`}
                >
                  <span className="truncate">{entity.label}</span>
                  {entity.hasOverride && (
                    <span className={`ml-2 h-1.5 w-1.5 shrink-0 rounded-full ${colors.dot}`} />
                  )}
                </button>
              ))}
            </div>
          </div>

          <div className="flex-1 p-5">
            {selectedEntity ? (
              <div className="space-y-5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <ScopeIcon className={`h-4.5 w-4.5 ${colors.text}`} />
                    <h3 className="text-sm font-semibold text-gray-900">{selectedEntity.label}</h3>
                    {selectedEntity.hasOverride ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-orange-50 px-2 py-0.5 text-[10px] font-medium text-orange-700 border border-orange-200">
                        Override active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-500 border border-gray-200">
                        Inheriting
                      </span>
                    )}
                  </div>
                  {selectedEntity.hasOverride && (
                    <button className="text-xs text-red-500 hover:text-red-600 hover:underline">
                      Clear override
                    </button>
                  )}
                </div>

                <div>
                  <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-gray-500">Resolution Mode</label>
                  <div className="flex gap-2">
                    <label className={`flex cursor-pointer items-center gap-2 rounded-lg border px-4 py-2.5 text-sm transition-colors ${selectedEntity.hasOverride ? 'border-gray-200 text-gray-500' : 'border-blue-300 bg-blue-50 text-blue-700 font-medium'}`}>
                      <input type="radio" name="mode" className="sr-only" defaultChecked={!selectedEntity.hasOverride} />
                      Inherit
                    </label>
                    <label className={`flex cursor-pointer items-center gap-2 rounded-lg border px-4 py-2.5 text-sm transition-colors ${selectedEntity.hasOverride ? 'border-orange-300 bg-orange-50 text-orange-700 font-medium' : 'border-gray-200 text-gray-500'}`}>
                      <input type="radio" name="mode" className="sr-only" defaultChecked={selectedEntity.hasOverride} />
                      Override
                    </label>
                  </div>
                </div>

                {selectedEntity.hasOverride && (
                  <>
                    <div>
                      <div className="mb-2 flex items-center justify-between">
                        <label className="text-xs font-medium uppercase tracking-wide text-gray-500">Active Guardrails</label>
                        <button className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700">
                          <Plus className="h-3 w-3" /> Add
                        </button>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        <span className="inline-flex items-center gap-1.5 rounded-full border border-green-200 bg-green-50 px-2.5 py-1 text-xs font-medium text-green-700">
                          <CheckCircle2 className="h-3 w-3" /> presidio-pii
                          <button className="ml-0.5 hover:text-green-900"><X className="h-3 w-3" /></button>
                        </span>
                        <span className="inline-flex items-center gap-1.5 rounded-full border border-green-200 bg-green-50 px-2.5 py-1 text-xs font-medium text-green-700">
                          <CheckCircle2 className="h-3 w-3" /> lakera-injection
                          <button className="ml-0.5 hover:text-green-900"><X className="h-3 w-3" /></button>
                        </span>
                      </div>
                    </div>

                    <div>
                      <div className="mb-2 flex items-center justify-between">
                        <label className="text-xs font-medium uppercase tracking-wide text-gray-500">Excluded Guardrails</label>
                        <button className="flex items-center gap-1 text-xs text-red-600 hover:text-red-700">
                          <Plus className="h-3 w-3" /> Add
                        </button>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        <span className="inline-flex items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-2.5 py-1 text-xs font-medium text-red-700">
                          <ShieldOff className="h-3 w-3" /> toxicity-filter
                          <button className="ml-0.5 hover:text-red-900"><X className="h-3 w-3" /></button>
                        </span>
                      </div>
                    </div>
                  </>
                )}

                <div className="flex justify-end border-t border-gray-100 pt-4">
                  <button className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 transition-colors">
                    Save Configuration
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-gray-400">
                Select an entity to configure guardrails
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function GuardrailsRedesign() {
  const [guardrailList, setGuardrailList] = useState(GUARDRAILS);

  const toggleGuardrail = (name: string) => {
    setGuardrailList((prev) =>
      prev.map((g) => (g.name === name ? { ...g, enabled: !g.enabled } : g))
    );
  };

  const activeCount = guardrailList.filter((g) => g.enabled).length;
  const preCallCount = guardrailList.filter((g) => g.mode === 'pre_call').length;
  const postCallCount = guardrailList.filter((g) => g.mode === 'post_call').length;

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="mb-1 flex items-center gap-1.5 text-xs text-gray-400">
              <span>Platform</span>
              <ChevronRight className="h-3 w-3" />
              <span className="font-medium text-gray-600">Guardrails</span>
            </div>
            <h1 className="flex items-center gap-2 text-xl font-bold text-gray-900">
              <Shield className="h-5 w-5 text-violet-600" />
              Guardrails
              <span className="ml-1 inline-flex h-5 min-w-[1.5rem] items-center justify-center rounded-full bg-gray-100 px-1.5 text-xs font-semibold text-gray-600">
                {guardrailList.length}
              </span>
            </h1>
            <p className="mt-1 text-sm text-gray-500">
              Configure safety guardrails to filter, block, or audit requests and responses across your gateway.
            </p>
          </div>
          <button className="inline-flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-violet-700">
            <Plus className="h-4 w-4" /> Add Guardrail
          </button>
        </div>
      </div>

      <div className="px-6 py-5">
        <div className="mb-6 grid grid-cols-4 gap-4">
          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gray-100">
                <Shield className="h-4.5 w-4.5 text-gray-600" />
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900">{guardrailList.length}</p>
                <p className="text-xs text-gray-500">Total Guardrails</p>
              </div>
            </div>
          </div>
          <div className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-green-50">
                <ShieldCheck className="h-4.5 w-4.5 text-green-600" />
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
                <ArrowDownUp className="h-4.5 w-4.5 text-blue-600" />
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
                <Eye className="h-4.5 w-4.5 text-amber-600" />
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900">{postCallCount}</p>
                <p className="text-xs text-gray-500">Post-call</p>
              </div>
            </div>
          </div>
        </div>

        <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 flex items-start gap-3">
          <CheckCircle2 className="h-4.5 w-4.5 text-emerald-600 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-medium text-emerald-800">Full Presidio engine installed</p>
            <p className="text-xs text-emerald-700 mt-0.5">PII guardrails can use the complete built-in entity set including NLP-powered recognition.</p>
          </div>
        </div>

        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500">Global Guardrails</h2>
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <Info className="h-3.5 w-3.5" />
            <span>Hover cards for actions</span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          {guardrailList.map((g) => (
            <GuardrailCard key={g.name} guardrail={g} onToggle={() => toggleGuardrail(g.name)} />
          ))}
        </div>

        <ScopedAssignmentsPanel />
      </div>
    </div>
  );
}
