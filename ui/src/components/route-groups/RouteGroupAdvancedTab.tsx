import { useState } from 'react';
import {
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Clock,
  Code2,
  GitBranch,
  ListChecks,
  RotateCcw,
  Tag,
  Trash2,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import PolicyGuidedEditor from '../PolicyGuidedEditor';
import { ROUTE_GROUP_STRATEGY_OPTIONS, type PolicyAction, type PolicyGuidedValues } from '../../lib/routeGroups';
import type { PromptBinding, PromptTemplate, RoutePolicy } from '../../lib/api';

/* ─── AccordionCard shell ─────────────────────────────────────────────────── */

interface AccordionCardProps {
  id: string;
  open: boolean;
  onToggle: () => void;
  icon: React.ElementType;
  iconBg: string;
  iconColor: string;
  title: string;
  subtitle: string;
  badge?: React.ReactNode;
  borderAccent: string;
  children: React.ReactNode;
}

function AccordionCard({
  open,
  onToggle,
  icon: Icon,
  iconBg,
  iconColor,
  title,
  subtitle,
  badge,
  borderAccent,
  children,
}: AccordionCardProps) {
  return (
    <div className={`rounded-xl bg-white ring-1 ring-slate-200 shadow-sm overflow-hidden transition-shadow ${open ? 'shadow-md' : ''}`}>
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-slate-50/60 transition-colors group"
        aria-expanded={open}
      >
        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${iconBg}`}>
          <Icon className={`h-4 w-4 ${iconColor}`} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="text-sm font-semibold text-slate-900">{title}</span>
            {badge}
          </div>
          <p className="text-xs text-slate-500 mt-0.5 truncate">{subtitle}</p>
        </div>
        <ChevronDown className={`h-4 w-4 text-slate-400 shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className={`border-t-2 ${borderAccent}`}>
          {children}
        </div>
      )}
    </div>
  );
}

/* ─── Prop types ──────────────────────────────────────────────────────────── */

interface BindingFormValues {
  template_key: string;
  label: string;
  priority: string;
  enabled: boolean;
}

interface RouteGroupAdvancedTabProps {
  /* Prompt Binding */
  bindings: PromptBinding[];
  templates: PromptTemplate[];
  bindingForm: BindingFormValues;
  loadingTemplates: boolean;
  savingBinding: boolean;
  deletingBinding: string | null;
  onBindingFormChange: (next: BindingFormValues) => void;
  onSaveBinding: () => void;
  onDeleteBinding: (binding: PromptBinding) => void;

  /* Routing Policy */
  guidedPolicy: PolicyGuidedValues;
  memberIds: string[];
  guidedPreview: string;
  policyText: string;
  policyMessage: string | null;
  policyError: string | null;
  isPolicyBusy: boolean;
  policyAction: PolicyAction;
  showAdvancedJson: boolean;
  hasMembers: boolean;
  onToggleAdvancedJson: () => void;
  onGuidedPolicyChange: (next: PolicyGuidedValues) => void;
  onPolicyTextChange: (value: string) => void;
  onValidate: () => void;
  onSaveDraft: () => void;
  onPublish: () => void;

  /* Policy History */
  policies: RoutePolicy[];
  canRollbackVersions: RoutePolicy[];
  selectedRollbackVersion: number | null;
  loadingPolicies: boolean;
  hasPoliciesError: boolean;
  onRollbackVersionChange: (next: number | null) => void;
  onRollback: () => void;
}

/* ─── Component ───────────────────────────────────────────────────────────── */

const STATUS_DOT: Record<string, string> = {
  published: 'bg-emerald-500 border-emerald-100',
  draft: 'bg-blue-400 border-blue-100',
  archived: 'bg-slate-300 border-slate-100',
};
const STATUS_BADGE: Record<string, string> = {
  published: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  draft: 'bg-blue-50 text-blue-700 border-blue-200',
  archived: 'bg-slate-100 text-slate-500 border-slate-200',
};

export default function RouteGroupAdvancedTab({
  bindings,
  templates,
  bindingForm,
  loadingTemplates,
  savingBinding,
  deletingBinding,
  onBindingFormChange,
  onSaveBinding,
  onDeleteBinding,
  guidedPolicy,
  memberIds,
  guidedPreview,
  policyText,
  policyMessage,
  policyError,
  isPolicyBusy,
  policyAction,
  showAdvancedJson,
  hasMembers,
  onToggleAdvancedJson,
  onGuidedPolicyChange,
  onPolicyTextChange,
  onValidate,
  onSaveDraft,
  onPublish,
  policies,
  canRollbackVersions,
  selectedRollbackVersion,
  loadingPolicies,
  hasPoliciesError,
  onRollbackVersionChange,
  onRollback,
}: RouteGroupAdvancedTabProps) {
  const [openSections, setOpenSections] = useState<Set<string>>(new Set(['prompt-binding']));
  const toggle = (id: string) =>
    setOpenSections((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const publishedPolicy = policies.find((p) => p.status === 'published') ?? null;
  const activeStrategy = guidedPolicy?.strategy ?? 'simple-shuffle';
  const STRATEGY_LABELS: Record<string, string> = {
    'simple-shuffle': 'Shuffle',
    weighted: 'Weighted',
    'least-busy': 'Least Busy',
    'latency-based-routing': 'Latency',
    'cost-based-routing': 'Cost',
    'usage-based-routing': 'Usage',
    'tag-based-routing': 'Tag',
    'priority-based-routing': 'Priority',
    'rate-limit-aware': 'Rate Limit',
  };
  const strategyLabel = STRATEGY_LABELS[activeStrategy] ?? activeStrategy;

  return (
    <div className="space-y-3 py-2">

      {/* ── 1. Prompt Binding ── */}
      <AccordionCard
        id="prompt-binding"
        open={openSections.has('prompt-binding')}
        onToggle={() => toggle('prompt-binding')}
        icon={BookOpen}
        iconBg="bg-violet-100"
        iconColor="text-violet-600"
        title="Prompt Binding"
        subtitle="Gateway resolves the bound prompt automatically for every request in this group."
        borderAccent="border-violet-200"
        badge={
          bindings.length > 0 ? (
            <span className="inline-flex items-center rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700 ring-1 ring-inset ring-violet-600/20">
              {bindings.length} active
            </span>
          ) : (
            <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
              none
            </span>
          )
        }
      >
        <div className="px-5 py-5 space-y-5">
          {/* Active bindings */}
          {bindings.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {bindings.map((b) => (
                <div
                  key={b.prompt_binding_id}
                  className="inline-flex items-center gap-2 rounded-full bg-violet-100 pl-3 pr-1 py-1 text-sm text-violet-800 ring-1 ring-inset ring-violet-200"
                >
                  <Tag className="h-3.5 w-3.5 text-violet-500 shrink-0" />
                  <span className="font-medium">{b.template_key}</span>
                  <span className="text-violet-400">/</span>
                  <span className="text-violet-600">{b.label}</span>
                  <span className="text-violet-400">/</span>
                  <span className="text-violet-600 text-xs">p={b.priority}</span>
                  {!b.enabled && (
                    <span className="text-violet-400 text-xs">(off)</span>
                  )}
                  <button
                    type="button"
                    onClick={() => onDeleteBinding(b)}
                    disabled={deletingBinding === b.prompt_binding_id}
                    className="ml-1 rounded-full p-1 hover:bg-violet-200 text-violet-500 hover:text-violet-900 disabled:opacity-50 transition-colors"
                    aria-label="Remove binding"
                  >
                    {deletingBinding === b.prompt_binding_id ? (
                      <div className="h-3 w-3 animate-spin rounded-full border-b border-violet-500" />
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Bind form */}
          {templates.length === 0 && !loadingTemplates ? (
            <div className="rounded-xl border border-dashed border-violet-200 bg-violet-50/40 px-4 py-5 text-center">
              <p className="text-sm font-medium text-violet-900">No prompts registered yet</p>
              <p className="mt-1 text-xs text-violet-600/70">
                Create a prompt in{' '}
                <Link to="/prompts" className="underline hover:text-violet-900">
                  Prompt Registry
                </Link>{' '}
                first, then return here to bind it.
              </p>
              <Link
                to="/prompts"
                className="mt-3 inline-flex rounded-lg border border-violet-200 bg-white px-3 py-1.5 text-xs font-medium text-violet-700 hover:bg-violet-50 transition-colors"
              >
                + New Prompt
              </Link>
            </div>
          ) : (
            <div className="rounded-lg border border-slate-100 bg-slate-50 p-4 space-y-4">
              <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400">
                {bindings.length > 0 ? 'Add another binding' : 'Add binding'}
              </p>
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex-1 min-w-[180px]">
                  <label className="mb-1.5 block text-xs font-medium text-slate-700">Prompt Template</label>
                  <select
                    value={bindingForm.template_key}
                    onChange={(e) => onBindingFormChange({ ...bindingForm, template_key: e.target.value })}
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm"
                  >
                    <option value="">{loadingTemplates ? 'Loading…' : 'Select a prompt…'}</option>
                    {templates.map((t) => (
                      <option key={t.prompt_template_id} value={t.template_key}>
                        {t.name} ({t.template_key})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="w-36">
                  <label className="mb-1.5 block text-xs font-medium text-slate-700">Label</label>
                  <input
                    value={bindingForm.label}
                    onChange={(e) => onBindingFormChange({ ...bindingForm, label: e.target.value })}
                    placeholder="production"
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm"
                  />
                </div>
                <div className="w-24">
                  <label className="mb-1.5 block text-xs font-medium text-slate-700">Priority</label>
                  <input
                    value={bindingForm.priority}
                    onChange={(e) => onBindingFormChange({ ...bindingForm, priority: e.target.value })}
                    placeholder="100"
                    type="number"
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm"
                  />
                </div>
                <button
                  type="button"
                  onClick={onSaveBinding}
                  disabled={savingBinding || !bindingForm.template_key.trim()}
                  className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-violet-700 disabled:opacity-50 transition-colors"
                >
                  {savingBinding ? 'Saving…' : 'Bind'}
                </button>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  role="switch"
                  aria-checked={bindingForm.enabled}
                  onClick={() => onBindingFormChange({ ...bindingForm, enabled: !bindingForm.enabled })}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ${bindingForm.enabled ? 'bg-violet-600' : 'bg-slate-200'}`}
                >
                  <span
                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ${bindingForm.enabled ? 'translate-x-4' : 'translate-x-0'}`}
                  />
                </button>
                <span className="text-xs text-slate-600">
                  Active — resolves for live requests
                </span>
              </div>
            </div>
          )}
        </div>
      </AccordionCard>

      {/* ── 2. Routing Policy ── */}
      <AccordionCard
        id="routing-policy"
        open={openSections.has('routing-policy')}
        onToggle={() => toggle('routing-policy')}
        icon={GitBranch}
        iconBg="bg-blue-100"
        iconColor="text-blue-600"
        title="Routing Policy"
        subtitle="Override the default shuffle only when you need weighted splits, ordered fallback, or rate-limit awareness."
        borderAccent="border-blue-200"
        badge={
          publishedPolicy ? (
            <span className="inline-flex items-center rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-600/20">
              {strategyLabel}
            </span>
          ) : (
            <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
              default shuffle
            </span>
          )
        }
      >
        <div className="px-5 py-5 space-y-5">
          {/* Header actions row */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            {/* Guided / Raw JSON toggle */}
            <div className="flex items-center gap-1 border border-slate-200 bg-slate-100 rounded-lg p-1">
              <button
                type="button"
                onClick={() => showAdvancedJson && onToggleAdvancedJson()}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${!showAdvancedJson ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
              >
                <ListChecks className="h-3.5 w-3.5" /> Guided
              </button>
              <button
                type="button"
                onClick={() => !showAdvancedJson && onToggleAdvancedJson()}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${showAdvancedJson ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
              >
                <Code2 className="h-3.5 w-3.5" /> Raw JSON
              </button>
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-2">
              {policyMessage && (
                <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  {policyMessage}
                </span>
              )}
              <button
                type="button"
                onClick={onValidate}
                disabled={isPolicyBusy || !hasMembers}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 transition-colors"
              >
                {policyAction === 'validate' ? 'Validating…' : 'Validate'}
              </button>
              <button
                type="button"
                onClick={onSaveDraft}
                disabled={isPolicyBusy || !hasMembers}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 transition-colors"
              >
                {policyAction === 'save-draft' ? 'Saving…' : 'Save Draft'}
              </button>
              <button
                type="button"
                onClick={onPublish}
                disabled={isPolicyBusy || !hasMembers}
                className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {policyAction === 'publish-json' ? 'Publishing…' : 'Publish ↑'}
              </button>
            </div>
          </div>

          {!hasMembers && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
              Add at least one deployment in the Models tab before validating or publishing a policy.
            </div>
          )}

          {policyError && (
            <div className="rounded-xl border border-red-100 bg-red-50 px-3 py-2.5 text-sm text-red-700">
              {policyError}
            </div>
          )}

          {/* Guided editor */}
          {!showAdvancedJson && (
            <PolicyGuidedEditor
              values={guidedPolicy}
              onChange={onGuidedPolicyChange}
              strategyOptions={[...ROUTE_GROUP_STRATEGY_OPTIONS]}
              memberOptions={memberIds}
            />
          )}

          {/* Policy preview / raw JSON */}
          <div className="space-y-1.5">
            <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400">
              {showAdvancedJson ? 'Raw JSON Editor' : 'Effective Policy Preview'}
            </p>
            {showAdvancedJson ? (
              <textarea
                value={policyText}
                onChange={(e) => onPolicyTextChange(e.target.value)}
                rows={10}
                spellCheck={false}
                className="w-full rounded-lg bg-gray-950 p-4 text-sm text-green-400 font-mono leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500 shadow-inner"
              />
            ) : (
              <div className="rounded-lg bg-gray-950 p-4 shadow-inner overflow-x-auto">
                <pre className="text-sm text-green-400 font-mono leading-relaxed">{guidedPreview}</pre>
              </div>
            )}
          </div>
        </div>
      </AccordionCard>

      {/* ── 3. Policy History ── */}
      <AccordionCard
        id="policy-history"
        open={openSections.has('policy-history')}
        onToggle={() => toggle('policy-history')}
        icon={Clock}
        iconBg="bg-slate-100"
        iconColor="text-slate-500"
        title="Policy History"
        subtitle="Audit and rollback previous routing policy versions."
        borderAccent="border-slate-200"
        badge={
          publishedPolicy ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 inline-block" />
              v{publishedPolicy.version} live
            </span>
          ) : (
            <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
              no policy
            </span>
          )
        }
      >
        <div className="px-5 py-5">
          {/* Rollback controls */}
          {canRollbackVersions.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
              <p className="text-xs text-slate-500">
                Rollback restores a previous version as the new published policy.
              </p>
              <div className="flex items-center gap-2">
                <div className="relative">
                  <select
                    value={selectedRollbackVersion ?? ''}
                    onChange={(e) => onRollbackVersionChange(e.target.value ? Number(e.target.value) : null)}
                    className="appearance-none rounded-lg border border-slate-200 bg-white pl-3 pr-8 py-1.5 text-xs text-slate-700 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 shadow-sm"
                  >
                    <option value="">Select version…</option>
                    {canRollbackVersions.map((p) => (
                      <option key={p.route_policy_id} value={p.version}>
                        v{p.version} ({p.status})
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
                </div>
                <button
                  type="button"
                  onClick={onRollback}
                  disabled={!selectedRollbackVersion || isPolicyBusy}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  {policyAction === 'rollback' ? 'Restoring…' : 'Rollback'}
                </button>
              </div>
            </div>
          )}

          {hasPoliciesError && (
            <div className="mb-4 rounded-xl border border-red-100 bg-red-50 px-3 py-2.5 text-sm text-red-700">
              Failed to load policy history.
            </div>
          )}

          {loadingPolicies && (
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <div className="h-4 w-4 animate-spin rounded-full border-b-2 border-slate-400" />
              Loading versions…
            </div>
          )}

          {!loadingPolicies && policies.length === 0 && !hasPoliciesError && (
            <div className="rounded-xl border border-dashed border-slate-200 py-8 text-center">
              <Clock className="mx-auto h-6 w-6 text-slate-300 mb-2" />
              <p className="text-sm text-slate-400">No policy versions yet.</p>
              <p className="text-xs text-slate-400 mt-0.5">Publish a policy override to start tracking history.</p>
            </div>
          )}

          {policies.length > 0 && (
            <div className="relative border-l-2 border-slate-200 space-y-5 pl-6 pb-1">
              {policies.map((policy) => {
                const isPublished = policy.status === 'published';
                const isNonPublished = policy.status !== 'published';
                return (
                  <div key={policy.route_policy_id} className="relative flex flex-col sm:flex-row sm:items-center sm:justify-between group">
                    <div className={`absolute -left-[31px] top-1 h-3.5 w-3.5 rounded-full border-2 ${STATUS_DOT[policy.status] ?? STATUS_DOT.archived} ring-4 ring-white`} />
                    <div className="flex items-center flex-wrap gap-3">
                      <span className="text-sm font-semibold text-slate-900">Version {policy.version}</span>
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border uppercase tracking-wider ${STATUS_BADGE[policy.status] ?? STATUS_BADGE.archived}`}>
                        {policy.status}
                      </span>
                      {policy.published_by && (
                        <span className="text-xs text-slate-500">by {policy.published_by}</span>
                      )}
                      {policy.published_at && (
                        <span className="text-xs text-slate-400">· {new Date(policy.published_at).toLocaleDateString()}</span>
                      )}
                      {isPublished && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 inline-block" />
                          LIVE
                        </span>
                      )}
                    </div>
                    {isNonPublished && (
                      <button
                        type="button"
                        onClick={() => { onRollbackVersionChange(policy.version); onRollback(); }}
                        disabled={isPolicyBusy}
                        className="mt-2 sm:mt-0 self-start sm:self-auto text-xs font-medium text-slate-500 hover:text-slate-800 opacity-0 group-hover:opacity-100 rounded-lg border border-slate-200 px-2.5 py-1 hover:bg-slate-50 disabled:opacity-50 transition-all"
                      >
                        Restore v{policy.version}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </AccordionCard>

    </div>
  );
}
