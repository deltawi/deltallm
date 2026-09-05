import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Brain,
  CheckCircle2,
  GitBranch,
  Layers,
  Mic,
  Pencil,
  Server,
  Settings,
  Shuffle,
  Terminal,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { useAuth } from '../lib/auth';
import { models, promptRegistry, routeGroups, type PromptBinding } from '../lib/api';
import { resolveUiAccess } from '../lib/authorization';
import { useApi } from '../lib/hooks';
import {
  buildPolicyFromGuided,
  GUIDED_POLICY_DEFAULTS,
  parsePolicyTextLoose,
  reconcileGuidedPolicyMembers,
  restoreDraftPolicyTombstones,
  routeGroupMutationOutcome,
  toGuidedPolicy,
  validateGuidedPolicy,
  validatePolicyContextCompatibility,
  type PolicyAction,
  type PolicyGuidedValues,
} from '../lib/routeGroups';
import RouteGroupSettingsCard from '../components/route-groups/RouteGroupSettingsCard';
import RouteGroupMembersCard from '../components/route-groups/RouteGroupMembersCard';
import RouteGroupUsageCard from '../components/route-groups/RouteGroupUsageCard';
import RouteGroupAdvancedTab from '../components/route-groups/RouteGroupAdvancedTab';
import { HeroTabbedDetailShell, IconTabs, InlineStat, PanelCard } from '../components/admin/shells';

/* ─── Visual helpers ─────────────────────────────────────────────────────── */

const MODE_ICONS: Record<string, React.ElementType> = {
  chat:                Brain,
  embedding:           Zap,
  audio_speech:        Mic,
  audio_transcription: Mic,
  image_generation:    Layers,
  rerank:              GitBranch,
};

const MODE_COLORS: Record<string, string> = {
  chat:                'bg-blue-100 text-blue-700',
  embedding:           'bg-violet-100 text-violet-700',
  audio_speech:        'bg-orange-100 text-orange-700',
  audio_transcription: 'bg-orange-100 text-orange-700',
  image_generation:    'bg-pink-100 text-pink-700',
  rerank:              'bg-teal-100 text-teal-700',
};

const ROUTING_LABELS: Record<string, string> = {
  'simple-shuffle':        'Shuffle',
  weighted:                'Weighted',
  'least-busy':            'Least Busy',
  'latency-based-routing': 'Latency',
  'cost-based-routing':    'Cost',
  'usage-based-routing':   'Usage',
  'tag-based-routing':     'Tag (Legacy)',
  'priority-based-routing':'Priority',
  'rate-limit-aware':      'Rate Limit',
};

/* ─── Tab definitions ────────────────────────────────────────────────────── */

const TABS = [
  { id: 'models',   label: 'Models',   icon: Server   },
  { id: 'test',     label: 'Test',     icon: Terminal  },
  { id: 'settings', label: 'Settings', icon: Settings  },
  { id: 'advanced', label: 'Advanced', icon: Layers    },
] as const;

type TabId = (typeof TABS)[number]['id'];

/* ─── Utilities ──────────────────────────────────────────────────────────── */

function requiredPromptVariables(schema: unknown): string[] {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return [];
  const required = (schema as Record<string, unknown>).required;
  if (!Array.isArray(required)) return [];
  return required.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

function mutationErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/* ─── Page ───────────────────────────────────────────────────────────────── */

export default function RouteGroupDetail() {
  const { groupKey } = useParams<{ groupKey: string }>();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { authMode, session } = useAuth();

  const [activeTab, setActiveTab] = useState<TabId>('models');
  const [savingGroup, setSavingGroup] = useState(false);
  const [deletingGroup, setDeletingGroup] = useState(false);
  const [confirmDeleteGroup, setConfirmDeleteGroup] = useState(false);
  const [addingMember, setAddingMember] = useState(false);
  const [savingBinding, setSavingBinding] = useState(false);
  const [deletingBinding, setDeletingBinding] = useState<string | null>(null);
  const [policyMessage, setPolicyMessage] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [selectedRollbackVersion, setSelectedRollbackVersion] = useState<number | null>(null);
  const [showAdvancedJson, setShowAdvancedJson] = useState(false);
  const [policyAction, setPolicyAction] = useState<PolicyAction>(null);
  const [memberSearchInput, setMemberSearchInput] = useState('');
  const [memberSearch, setMemberSearch] = useState('');
  const [manualMemberEntry, setManualMemberEntry] = useState(false);
  const [memberToRemove, setMemberToRemove] = useState<string | null>(null);
  const [removingMember, setRemovingMember] = useState(false);
  const [form, setForm] = useState({ name: '', mode: 'chat', enabled: true });
  const [memberForm, setMemberForm] = useState({ deployment_id: '', weight: '', priority: '', enabled: true });
  const [bindingForm, setBindingForm] = useState({ template_key: '', label: 'production', priority: '100', enabled: true });
  const [guidedPolicy, setGuidedPolicy] = useState<PolicyGuidedValues>(GUIDED_POLICY_DEFAULTS);
  const [policyText, setPolicyText] = useState('{\n  "strategy": "weighted"\n}');

  /* API */
  const detail = useApi((signal) => routeGroups.get(groupKey!, signal), [groupKey]);
  const policyHistory = useApi(
    (signal) => routeGroups.listPolicies(groupKey!, signal),
    [groupKey],
  );
  const groupBindings = useApi(
    () => promptRegistry.listBindings({ scope_type: 'group', scope_id: groupKey!, limit: 20, offset: 0 }),
    [groupKey],
  );
  const promptTemplates = useApi(() => promptRegistry.listTemplates({ limit: 100, offset: 0 }), []);
  const bindingPreview = useApi(() => promptRegistry.previewResolution({ route_group_key: groupKey! }), [groupKey]);
  const deploymentCandidates = useApi(
    (signal) => models.list(
      { search: memberSearch, mode: form.mode, limit: 20, offset: 0 },
      signal,
    ),
    [memberSearch, form.mode],
  );

  const policies = useMemo(() => policyHistory.data?.policies || [], [policyHistory.data?.policies]);
  const members = useMemo(() => detail.data?.members || [], [detail.data?.members]);
  const workloadMode = detail.data?.group.mode || 'chat';
  const bindings = useMemo(() => groupBindings.data?.data || [], [groupBindings.data?.data]);
  const healthyMembers = members.filter((m) => m.healthy === true).length;
  const missingMembers = members.filter((m) => m.healthy == null).length;
  const memberIds = useMemo(() => members.map((m) => m.deployment_id), [members]);
  const isPolicyBusy = policyAction !== null;
  const canSimulatePolicy = resolveUiAccess(authMode, session).route_groups;
  const publishedPolicy = useMemo(() => policies.find((p) => p.status === 'published') || null, [policies]);
  const draftPolicy = useMemo(() => policies.find((p) => p.status === 'draft') || null, [policies]);
  const winningPrompt = bindingPreview.data?.winner || null;
  const winningPromptDetail = useApi(
    () => (winningPrompt?.template_key ? promptRegistry.getTemplate(String(winningPrompt.template_key)) : Promise.resolve(null)),
    [winningPrompt?.template_key],
  );

  /* Sync form from API data */
  useEffect(() => {
    const group = detail.data?.group;
    if (!group) return;
    setForm({ name: group.name || '', mode: group.mode || 'chat', enabled: !!group.enabled });
  }, [detail.data?.group]);

  useEffect(() => {
    const firstBinding = bindings[0];
    if (!firstBinding) return;
    setBindingForm({
      template_key: firstBinding.template_key,
      label: firstBinding.label || 'production',
      priority: String(firstBinding.priority ?? 100),
      enabled: firstBinding.enabled,
    });
  }, [bindings]);

  useEffect(() => {
    const timer = window.setTimeout(() => setMemberSearch(memberSearchInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [memberSearchInput]);

  useEffect(() => {
    if (policies.length === 0) return;
    const preferred = draftPolicy || policies[0];
    const storedPolicyJson =
      preferred.policy_json && typeof preferred.policy_json === 'object' && !Array.isArray(preferred.policy_json)
        ? preferred.policy_json
        : {};
    const storedPublishedPolicy = publishedPolicy?.policy_json;
    const publishedPolicyJson =
      storedPublishedPolicy
      && typeof storedPublishedPolicy === 'object'
      && !Array.isArray(storedPublishedPolicy)
        ? storedPublishedPolicy
        : null;
    const policyJson = preferred.status === 'draft'
      ? restoreDraftPolicyTombstones(storedPolicyJson, publishedPolicyJson)
      : storedPolicyJson;
    setPolicyText(JSON.stringify(policyJson, null, 2));
    setGuidedPolicy(toGuidedPolicy(policyJson, members));
    const rollbackCandidates = policies
      .filter((p) => p.status === 'archived' || p.status === 'published')
      .sort((a, b) => b.version - a.version);
    setSelectedRollbackVersion(rollbackCandidates[0]?.version ?? null);
  }, [draftPolicy, members, policies, publishedPolicy]);

  useEffect(() => {
    setGuidedPolicy((current) => reconcileGuidedPolicyMembers(current, members));
  }, [members]);

  const canRollbackVersions = useMemo(
    () => policies.filter((p) => p.status === 'archived' || p.status === 'published'),
    [policies],
  );

  const candidateDeployments = useMemo(() => {
    const items = deploymentCandidates.data?.data || [];
    const assigned = new Set(memberIds);
    return items.filter((item) => !assigned.has(item.deployment_id));
  }, [deploymentCandidates.data?.data, memberIds]);

  const guidedPreview = useMemo(() => {
    const base = parsePolicyTextLoose(policyText) || {};
    return JSON.stringify(buildPolicyFromGuided(base, guidedPolicy), null, 2);
  }, [guidedPolicy, policyText]);

  const simulationPolicy = useMemo(() => {
    if (showAdvancedJson) {
      const parsed = parsePolicyTextLoose(policyText);
      if (!parsed || validatePolicyContextCompatibility(parsed, workloadMode)) return null;
      return parsed;
    }
    if (validateGuidedPolicy(guidedPolicy, members, workloadMode)) return null;
    return buildPolicyFromGuided(parsePolicyTextLoose(policyText) || {}, guidedPolicy);
  }, [guidedPolicy, members, policyText, showAdvancedJson, workloadMode]);
  const simulationPolicyError = useMemo(() => {
    if (showAdvancedJson) {
      const parsed = parsePolicyTextLoose(policyText);
      if (!parsed) return 'Enter valid policy JSON before simulating.';
      return validatePolicyContextCompatibility(parsed, workloadMode);
    }
    return validateGuidedPolicy(guidedPolicy, members, workloadMode);
  }, [guidedPolicy, members, policyText, showAdvancedJson, workloadMode]);

  const promptSummary = useMemo(() => {
    if (!winningPrompt || !winningPromptDetail.data) return null;
    const detail = winningPromptDetail.data;
    const versionFromLabel =
      typeof winningPrompt.label === 'string'
        ? detail.labels.find((l) => l.label === winningPrompt.label)?.version
        : null;
    const resolvedVersion =
      (typeof versionFromLabel === 'number' ? versionFromLabel : null) ??
      detail.versions.find((v) => v.status === 'published')?.version ??
      detail.versions[0]?.version;
    const versionRecord = detail.versions.find((v) => v.version === resolvedVersion) || null;
    return {
      templateKey: String(winningPrompt.template_key),
      label: typeof winningPrompt.label === 'string' ? winningPrompt.label : null,
      requiredVariables: requiredPromptVariables(versionRecord?.variables_schema),
    };
  }, [winningPrompt, winningPromptDetail.data]);

  /* ── Handlers ── */
  const parsePolicy = (): Record<string, unknown> | null => {
    if (!showAdvancedJson) {
      const guidedError = validateGuidedPolicy(guidedPolicy, members, workloadMode);
      if (guidedError) {
        setPolicyError(guidedError);
        return null;
      }
      const base = parsePolicyTextLoose(policyText) || {};
      const payload = buildPolicyFromGuided(base, guidedPolicy);
      setPolicyText(JSON.stringify(payload, null, 2));
      setPolicyError(null);
      return payload;
    }
    const parsed = parsePolicyTextLoose(policyText);
    if (!parsed) { setPolicyError('Invalid JSON payload'); return null; }
    const compatibilityError = validatePolicyContextCompatibility(parsed, workloadMode);
    if (compatibilityError) { setPolicyError(compatibilityError); return null; }
    setPolicyError(null);
    return parsed;
  };

  const handleSaveGroup = async () => {
    setSavingGroup(true);
    try {
      const result = await routeGroups.update(groupKey!, { name: form.name.trim() || null, mode: form.mode, enabled: form.enabled });
      detail.refetch();
      const outcome = routeGroupMutationOutcome('Route group settings were saved.', result.warnings);
      pushToast({ tone: outcome.tone, title: 'Group updated', message: outcome.message });
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Update failed', message: mutationErrorMessage(error, 'Failed to update route group.') });
    } finally {
      setSavingGroup(false);
    }
  };

  const handleDeleteGroup = async () => {
    setDeletingGroup(true);
    try {
      const result = await routeGroups.delete(groupKey!);
      const outcome = routeGroupMutationOutcome(`"${groupKey}" was deleted.`, result.warnings);
      pushToast({ tone: outcome.tone, title: 'Group deleted', message: outcome.message });
      navigate('/route-groups');
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Delete failed', message: mutationErrorMessage(error, 'Failed to delete route group.') });
      setDeletingGroup(false);
    }
  };

  const handleAddMember = async () => {
    if (!memberForm.deployment_id.trim()) {
      pushToast({ tone: 'error', title: 'Missing deployment', message: 'Select or enter a deployment ID before adding.' });
      return;
    }
    setAddingMember(true);
    try {
      const result = await routeGroups.upsertMember(groupKey!, {
        deployment_id: memberForm.deployment_id.trim(),
        enabled: memberForm.enabled,
        weight: memberForm.weight ? Number(memberForm.weight) : null,
        priority: memberForm.priority ? Number(memberForm.priority) : null,
      });
      setMemberForm({ deployment_id: '', weight: '', priority: '', enabled: true });
      setMemberSearchInput('');
      setMemberSearch('');
      setManualMemberEntry(false);
      const outcome = routeGroupMutationOutcome(
        'Deployment was added to the route group.',
        result.warnings,
      );
      pushToast({ tone: outcome.tone, title: 'Member added', message: outcome.message });
      detail.refetch();
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Add member failed', message: mutationErrorMessage(error, 'Failed to add deployment to group.') });
    } finally {
      setAddingMember(false);
    }
  };

  const handleSaveBinding = async () => {
    if (!bindingForm.template_key.trim()) {
      pushToast({ tone: 'error', title: 'Missing prompt', message: 'Select a prompt before saving the binding.' });
      return;
    }
    setSavingBinding(true);
    try {
      await promptRegistry.upsertBinding({
        scope_type: 'group',
        scope_id: groupKey,
        template_key: bindingForm.template_key.trim(),
        label: bindingForm.label.trim() || 'production',
        priority: Number(bindingForm.priority || 100),
        enabled: bindingForm.enabled,
      });
      pushToast({ tone: 'success', title: 'Prompt bound', message: 'This group will now resolve the selected prompt binding.' });
      groupBindings.refetch();
      bindingPreview.refetch();
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Bind prompt failed', message: mutationErrorMessage(error, 'Failed to save prompt binding.') });
    } finally {
      setSavingBinding(false);
    }
  };

  const handleDeleteBinding = async (binding: PromptBinding) => {
    setDeletingBinding(binding.prompt_binding_id);
    try {
      await promptRegistry.deleteBinding(binding.prompt_binding_id);
      pushToast({ tone: 'success', title: 'Binding removed', message: 'The prompt is no longer bound to this group.' });
      groupBindings.refetch();
      bindingPreview.refetch();
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Remove binding failed', message: mutationErrorMessage(error, 'Failed to remove prompt binding.') });
    } finally {
      setDeletingBinding(null);
    }
  };

  const handleRemoveMember = async () => {
    if (!memberToRemove) return;
    setRemovingMember(true);
    try {
      const result = await routeGroups.removeMember(groupKey!, memberToRemove);
      const outcome = routeGroupMutationOutcome(`"${memberToRemove}" was removed.`, result.warnings);
      pushToast({ tone: outcome.tone, title: 'Member removed', message: outcome.message });
      setMemberToRemove(null);
      detail.refetch();
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Remove member failed', message: mutationErrorMessage(error, 'Failed to remove member.') });
    } finally {
      setRemovingMember(false);
    }
  };

  const handleValidatePolicy = async () => {
    const parsed = parsePolicy();
    if (!parsed) return;
    setPolicyAction('validate');
    try {
      const result = await routeGroups.validatePolicy(groupKey!, parsed);
      setPolicyText(JSON.stringify(result.policy, null, 2));
      setGuidedPolicy(toGuidedPolicy(result.policy, members));
      setPolicyMessage(result.warnings?.length ? `Valid with warnings: ${result.warnings.join(' ')}` : 'Policy is valid.');
      setPolicyError(null);
    } catch (error: unknown) {
      setPolicyError(mutationErrorMessage(error, 'Policy validation failed'));
      setPolicyMessage(null);
    } finally {
      setPolicyAction(null);
    }
  };

  const handleSaveDraft = async () => {
    const parsed = parsePolicy();
    if (!parsed) return;
    setPolicyAction('save-draft');
    try {
      const result = await routeGroups.savePolicyDraft(groupKey!, parsed);
      setPolicyMessage(
        routeGroupMutationOutcome(`Draft saved (v${result.policy.version}).`, result.warnings).message,
      );
      setPolicyError(null);
      policyHistory.refetch();
    } catch (error: unknown) {
      setPolicyError(mutationErrorMessage(error, 'Failed to save draft'));
      setPolicyMessage(null);
    } finally {
      setPolicyAction(null);
    }
  };

  const handlePublish = async () => {
    const parsed = parsePolicy();
    if (!parsed) return;
    setPolicyAction('publish-json');
    try {
      const result = await routeGroups.publishPolicy(groupKey!, parsed);
      setPolicyMessage(
        routeGroupMutationOutcome(
          `Published policy version ${result.policy.version}.`,
          result.warnings,
        ).message,
      );
      setPolicyError(null);
      detail.refetch();
      policyHistory.refetch();
    } catch (error: unknown) {
      setPolicyError(mutationErrorMessage(error, 'Failed to publish policy'));
      setPolicyMessage(null);
    } finally {
      setPolicyAction(null);
    }
  };

  const handleRollback = async () => {
    if (!selectedRollbackVersion) return;
    setPolicyAction('rollback');
    try {
      const result = await routeGroups.rollbackPolicy(groupKey!, selectedRollbackVersion);
      setPolicyMessage(
        routeGroupMutationOutcome(
          `Rolled back to new published version ${result.policy.version}.`,
          result.warnings,
        ).message,
      );
      setPolicyError(null);
      detail.refetch();
      policyHistory.refetch();
    } catch (error: unknown) {
      setPolicyError(mutationErrorMessage(error, 'Failed to rollback policy'));
      setPolicyMessage(null);
    } finally {
      setPolicyAction(null);
    }
  };

  /* ── Loading / error / not-found guards ── */
  if (detail.loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <div className="border-b border-gray-200 bg-white px-6 py-3">
          <button onClick={() => navigate('/route-groups')} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
            <ArrowLeft className="h-4 w-4" /> Back to Model Groups
          </button>
        </div>
        <div className="flex min-h-[400px] items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-brand-primary" />
        </div>
      </div>
    );
  }

  if (detail.error && !detail.data?.group) {
    return (
      <div className="min-h-screen bg-gray-50">
        <div className="border-b border-gray-200 bg-white px-6 py-3">
          <button onClick={() => navigate('/route-groups')} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
            <ArrowLeft className="h-4 w-4" /> Back to Model Groups
          </button>
        </div>
        <div className="p-6">
          <div className="mb-3 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
            Failed to load route group details.
          </div>
          <button onClick={detail.refetch} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!detail.data?.group) {
    return (
      <div className="min-h-screen bg-gray-50">
        <div className="border-b border-gray-200 bg-white px-6 py-3">
          <button onClick={() => navigate('/route-groups')} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
            <ArrowLeft className="h-4 w-4" /> Back to Model Groups
          </button>
        </div>
        <div className="p-6 text-sm text-gray-500">Route group not found.</div>
      </div>
    );
  }

  const group = detail.data.group;
  const ModeIcon = MODE_ICONS[group.mode] || Layers;
  const modeColor = MODE_COLORS[group.mode] || 'bg-gray-100 text-gray-700';
  const routingLabel = group.routing_strategy
    ? (ROUTING_LABELS[group.routing_strategy] || group.routing_strategy)
    : 'Shuffle';
  const RoutingIcon = !group.routing_strategy || group.routing_strategy === 'simple-shuffle' ? Shuffle : GitBranch;

  return (
    <>
      <HeroTabbedDetailShell
      backBar={(
        <button
          onClick={() => navigate('/route-groups')}
          className="flex items-center gap-1.5 text-sm text-gray-500 transition hover:text-gray-800"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Model Groups
        </button>
      )}
      hero={(
        <div className="relative overflow-hidden border-b border-gray-200 bg-white">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-blue-50 via-white to-slate-50 opacity-70" />
          <div className="pointer-events-none absolute right-0 top-0 h-40 w-40 rounded-full bg-blue-100/40 blur-3xl" />

          <div className="relative px-6 pb-5 pt-6">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${modeColor}`}>
                <ModeIcon className="h-3.5 w-3.5" />
                {group.mode.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
              </span>
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${group.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
                {group.enabled ? <><CheckCircle2 className="h-3.5 w-3.5" /> Live</> : <><XCircle className="h-3.5 w-3.5" /> Off</>}
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">
                <RoutingIcon className="h-3.5 w-3.5" />
                {publishedPolicy ? `Override v${publishedPolicy.version}` : `${routingLabel} routing`}
              </span>
            </div>

            <div className="flex items-start justify-between gap-4">
              <div>
                <h1 className="text-2xl font-bold text-gray-900">{group.name || group.group_key}</h1>
                <p className="mt-0.5 text-sm text-gray-500">
                  Group key:{' '}
                  <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-700">{group.group_key}</code>
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  onClick={() => setActiveTab('settings')}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 shadow-sm hover:bg-gray-50"
                >
                  <Pencil className="h-4 w-4" /> Edit
                </button>
                <button
                  onClick={() => setConfirmDeleteGroup(true)}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-500 shadow-sm hover:bg-red-50"
                  title="Delete group"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-6 divide-x divide-gray-100">
              <InlineStat label="Members" value={String(members.length)} />
              <div className="pl-6">
                <InlineStat label="Healthy" value={members.length > 0 ? `${healthyMembers}/${members.length}` : '—'} />
              </div>
              <div className="pl-6">
                <InlineStat label="Policy" value={publishedPolicy ? `v${publishedPolicy.version} published` : 'Default shuffle'} />
              </div>
              <div className="pl-6">
                <InlineStat label="Prompt" value={promptSummary ? promptSummary.templateKey : 'None bound'} />
              </div>
              {missingMembers > 0 && (
                <div className="pl-6">
                  <InlineStat label="Registry Gaps" value={`${missingMembers} missing`} />
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      body={(
        <>
          <IconTabs
            active={activeTab}
            onChange={setActiveTab}
            items={TABS.map(({ id, label, icon }) => ({ id, label, icon }))}
          />
          {activeTab === 'models' ? (
            <RouteGroupMembersCard
              mode={form.mode}
              memberForm={memberForm}
              manualMemberEntry={manualMemberEntry}
              memberSearchInput={memberSearchInput}
              candidateDeployments={candidateDeployments}
              loadingCandidates={deploymentCandidates.loading}
              hasCandidateError={!!deploymentCandidates.error}
              addingMember={addingMember}
              members={members}
              onMemberFormChange={setMemberForm}
              onToggleManualEntry={() => setManualMemberEntry((cur) => !cur)}
              onMemberSearchChange={setMemberSearchInput}
              onAddMember={handleAddMember}
              onRequestRemoveMember={setMemberToRemove}
            />
          ) : activeTab === 'advanced' ? (
            <RouteGroupAdvancedTab
              groupKey={group.group_key}
              workloadMode={workloadMode}
              bindings={bindings}
              templates={promptTemplates.data?.data || []}
              bindingForm={bindingForm}
              loadingTemplates={promptTemplates.loading}
              savingBinding={savingBinding}
              deletingBinding={deletingBinding}
              onBindingFormChange={setBindingForm}
              onSaveBinding={handleSaveBinding}
              onDeleteBinding={handleDeleteBinding}
              guidedPolicy={guidedPolicy}
              members={members}
              guidedPreview={guidedPreview}
              simulationPolicy={simulationPolicy}
              simulationPolicyError={simulationPolicyError}
              simulationPromptRef={promptSummary ? {
                template_key: promptSummary.templateKey,
                ...(promptSummary.label ? { label: promptSummary.label } : {}),
              } : null}
              canSimulate={canSimulatePolicy}
              policyText={policyText}
              policyMessage={policyMessage}
              policyError={policyError}
              isPolicyBusy={isPolicyBusy}
              policyAction={policyAction}
              showAdvancedJson={showAdvancedJson}
              hasMembers={memberIds.length > 0}
              onToggleAdvancedJson={() => setShowAdvancedJson((cur) => !cur)}
              onGuidedPolicyChange={setGuidedPolicy}
              onPolicyTextChange={setPolicyText}
              onValidate={handleValidatePolicy}
              onSaveDraft={handleSaveDraft}
              onPublish={handlePublish}
              policies={policies}
              canRollbackVersions={canRollbackVersions}
              selectedRollbackVersion={selectedRollbackVersion}
              loadingPolicies={policyHistory.loading}
              hasPoliciesError={!!policyHistory.error}
              onRollbackVersionChange={setSelectedRollbackVersion}
              onRollback={handleRollback}
            />
          ) : (
            <PanelCard>
              {activeTab === 'test' && (
                <RouteGroupUsageCard
                  groupKey={group.group_key}
                  mode={form.mode}
                  liveTrafficEnabled={group.enabled}
                  boundPrompt={promptSummary}
                />
              )}
              {activeTab === 'settings' && (
                <RouteGroupSettingsCard form={form} saving={savingGroup} onChange={setForm} onSave={handleSaveGroup} />
              )}
            </PanelCard>
          )}
        </>
      )}
      />

      {/* Remove member confirmation */}
      <ConfirmDialog
        open={!!memberToRemove}
        title="Remove route group member"
        description={memberToRemove ? `Remove "${memberToRemove}" from this route group? Policy references to this member may become invalid.` : ''}
        confirmLabel="Remove Member"
        destructive
        confirming={removingMember}
        onConfirm={handleRemoveMember}
        onClose={() => { if (!removingMember) setMemberToRemove(null); }}
      />

      {/* Delete group confirmation */}
      <ConfirmDialog
        open={confirmDeleteGroup}
        title="Delete model group"
        description={`Delete "${group.group_key}"? This removes all members, policy history, and prompt bindings for this group.`}
        confirmLabel="Delete Group"
        destructive
        confirming={deletingGroup}
        onConfirm={handleDeleteGroup}
        onClose={() => { if (!deletingGroup) setConfirmDeleteGroup(false); }}
      />
    </>
  );
}
