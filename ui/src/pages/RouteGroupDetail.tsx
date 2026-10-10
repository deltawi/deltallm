import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  GitBranch,
  Layers,
  Server,
  Settings,
  Terminal,
} from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { useAuth } from '../lib/auth';
import {
  ApiError,
  models,
  promptRegistry,
  routeGroups,
  type PromptBinding,
} from '../lib/api';
import { isPlatformAdminSession, resolveUiAccess } from '../lib/authorization';
import { useApi } from '../lib/hooks';
import { useRouteGroupMutationScope } from '../lib/useRouteGroupMutationScope';
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
import RouteGroupSettingsPanel from '../components/route-groups/RouteGroupSettingsPanel';
import RouteGroupMembersCard from '../components/route-groups/RouteGroupMembersCard';
import RouteGroupUsageCard from '../components/route-groups/RouteGroupUsageCard';
import RouteGroupAdvancedTab from '../components/route-groups/RouteGroupAdvancedTab';
import { HeroTabbedDetailShell, IconTabs, PanelCard } from '../components/admin/shells';
import RouteGroupHero from '../components/route-groups/RouteGroupHero';
import { useManagedAssetAudienceOptions } from '../lib/useManagedAssetAudienceOptions';

/* ─── Tab definitions ────────────────────────────────────────────────────── */

const TABS = [
  { id: 'models',   label: 'Models',   icon: Server   },
  { id: 'policy',   label: 'Policy',   icon: GitBranch },
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

export default function RouteGroupDetail({ routeGroupId }: { routeGroupId: string }) {
  const mutations = useRouteGroupMutationScope();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { authMode, session } = useAuth();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const { teamOptions, organizationOptions } = useManagedAssetAudienceOptions(
    session,
    isPlatformAdmin,
  );

  const [activeTab, setActiveTab] = useState<TabId>('models');
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
  const detail = useApi((signal) => routeGroups.get(routeGroupId, signal), [routeGroupId]);
  const groupKey = detail.data?.group.group_key;
  const policyHistory = useApi(
    (signal) => routeGroups.listPolicies(routeGroupId, signal),
    [routeGroupId],
  );
  const groupBindings = useApi(
    (signal) => isPlatformAdmin && groupKey ? promptRegistry.listBindings({ scope_type: 'group', scope_id: groupKey, limit: 20, offset: 0 }, signal) : Promise.resolve(null),
    [groupKey, isPlatformAdmin],
  );
  const promptTemplates = useApi((signal) => promptRegistry.listTemplates({ limit: 100, offset: 0 }, signal), []);
  const bindingPreview = useApi(
    (signal) => isPlatformAdmin && groupKey ? promptRegistry.previewResolution({ route_group_key: groupKey }, signal) : Promise.resolve(null),
    [groupKey, isPlatformAdmin],
  );
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
  const unknownHealth = members.filter((m) => m.healthy == null).length;
  const missingMembers = members.filter((m) => m.healthy == null).length;
  const memberIds = useMemo(() => members.map((m) => m.deployment_id), [members]);
  const isPolicyBusy = policyAction !== null;
  const canSimulatePolicy = resolveUiAccess(authMode, session).route_groups;
  const groupAccess = detail.data?.group.access;
  const canWrite = isPlatformAdmin || !groupAccess || groupAccess.capabilities.write;
  const canDelete = isPlatformAdmin || !groupAccess || groupAccess.capabilities.delete;
  const publishedPolicy = useMemo(() => policies.find((p) => p.status === 'published') || detail.data?.policy || null, [policies, detail.data?.policy]);
  const draftPolicy = useMemo(() => policies.find((p) => p.status === 'draft') || null, [policies]);
  const winningPrompt = bindingPreview.data?.winner || null;
  const winningPromptDetail = useApi(
    (signal) => (winningPrompt?.template_key ? promptRegistry.getTemplate(String(winningPrompt.template_key), signal) : Promise.resolve(null)),
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

  const handleDeleteGroup = async () => {
    if (!canDelete) return;
    const operation = mutations.begin();
    if (!operation) return;
    setDeletingGroup(true);
    try {
      const result = await routeGroups.delete(routeGroupId, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      const outcome = routeGroupMutationOutcome(`"${groupKey}" was deleted.`, result.warnings);
      pushToast({ tone: outcome.tone, title: 'Group deleted', message: outcome.message });
      navigate('/route-groups');
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Delete failed', message: mutationErrorMessage(error, 'Failed to delete route group.') });
      setDeletingGroup(false);
    } finally {
      mutations.finish(operation);
    }
  };

  const handleAddMember = async () => {
    if (!canWrite) return;
    if (!memberForm.deployment_id.trim()) {
      pushToast({ tone: 'error', title: 'Missing deployment', message: 'Select or enter a deployment ID before adding.' });
      return;
    }
    const operation = mutations.begin();
    if (!operation) return;
    setAddingMember(true);
    try {
      const result = await routeGroups.upsertMember(routeGroupId, {
        deployment_id: memberForm.deployment_id.trim(),
        enabled: memberForm.enabled,
        weight: memberForm.weight ? Number(memberForm.weight) : null,
        priority: memberForm.priority ? Number(memberForm.priority) : null,
      }, operation.signal);
      if (!mutations.isCurrent(operation)) return;
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
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Add member failed', message: mutationErrorMessage(error, 'Failed to add deployment to group.') });
    } finally {
      if (mutations.finish(operation)) setAddingMember(false);
    }
  };

  const handleSaveBinding = async () => {
    if (!isPlatformAdmin) return;
    if (!bindingForm.template_key.trim()) {
      pushToast({ tone: 'error', title: 'Missing prompt', message: 'Select a prompt before saving the binding.' });
      return;
    }
    const operation = mutations.begin();
    if (!operation) return;
    setSavingBinding(true);
    try {
      await promptRegistry.upsertBinding({
        scope_type: 'group',
        scope_id: groupKey,
        template_key: bindingForm.template_key.trim(),
        label: bindingForm.label.trim() || 'production',
        priority: Number(bindingForm.priority || 100),
        enabled: bindingForm.enabled,
      }, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'success', title: 'Prompt bound', message: 'This group will now resolve the selected prompt binding.' });
      groupBindings.refetch();
      bindingPreview.refetch();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Bind prompt failed', message: mutationErrorMessage(error, 'Failed to save prompt binding.') });
    } finally {
      if (mutations.finish(operation)) setSavingBinding(false);
    }
  };

  const handleDeleteBinding = async (binding: PromptBinding) => {
    if (!isPlatformAdmin) return;
    const operation = mutations.begin();
    if (!operation) return;
    setDeletingBinding(binding.prompt_binding_id);
    try {
      await promptRegistry.deleteBinding(binding.prompt_binding_id, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'success', title: 'Binding removed', message: 'The prompt is no longer bound to this group.' });
      groupBindings.refetch();
      bindingPreview.refetch();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Remove binding failed', message: mutationErrorMessage(error, 'Failed to remove prompt binding.') });
    } finally {
      if (mutations.finish(operation)) setDeletingBinding(null);
    }
  };

  const handleRemoveMember = async () => {
    if (!canWrite) return;
    if (!memberToRemove) return;
    const operation = mutations.begin();
    if (!operation) return;
    setRemovingMember(true);
    try {
      const result = await routeGroups.removeMember(routeGroupId, memberToRemove, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      const outcome = routeGroupMutationOutcome(`"${memberToRemove}" was removed.`, result.warnings);
      pushToast({ tone: outcome.tone, title: 'Member removed', message: outcome.message });
      setMemberToRemove(null);
      detail.refetch();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Remove member failed', message: mutationErrorMessage(error, 'Failed to remove member.') });
    } finally {
      if (mutations.finish(operation)) setRemovingMember(false);
    }
  };

  const handleValidatePolicy = async () => {
    if (!canWrite) return;
    const parsed = parsePolicy();
    if (!parsed) return;
    const operation = mutations.begin();
    if (!operation) return;
    setPolicyAction('validate');
    try {
      const result = await routeGroups.validatePolicy(routeGroupId, parsed, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      const validated = restoreDraftPolicyTombstones(result.policy, parsed);
      setPolicyText(JSON.stringify(validated, null, 2));
      setGuidedPolicy(toGuidedPolicy(validated, members));
      setPolicyMessage(result.warnings?.length ? `Valid with warnings: ${result.warnings.join(' ')}` : 'Policy is valid.');
      setPolicyError(null);
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      setPolicyError(mutationErrorMessage(error, 'Policy validation failed'));
      setPolicyMessage(null);
    } finally {
      if (mutations.finish(operation)) setPolicyAction(null);
    }
  };

  const handleSaveDraft = async () => {
    if (!canWrite) return;
    const parsed = parsePolicy();
    if (!parsed) return;
    const operation = mutations.begin();
    if (!operation) return;
    setPolicyAction('save-draft');
    try {
      const result = await routeGroups.savePolicyDraft(routeGroupId, parsed, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      setPolicyMessage(
        routeGroupMutationOutcome(`Draft saved (v${result.policy.version}).`, result.warnings).message,
      );
      setPolicyError(null);
      policyHistory.refetch();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      setPolicyError(mutationErrorMessage(error, 'Failed to save draft'));
      setPolicyMessage(null);
    } finally {
      if (mutations.finish(operation)) setPolicyAction(null);
    }
  };

  const handlePublish = async () => {
    if (!canWrite) return;
    const parsed = parsePolicy();
    if (!parsed) return;
    const operation = mutations.begin();
    if (!operation) return;
    setPolicyAction('publish-json');
    try {
      const result = await routeGroups.publishPolicy(routeGroupId, parsed, operation.signal);
      if (!mutations.isCurrent(operation)) return;
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
      if (!mutations.isCurrent(operation)) return;
      setPolicyError(mutationErrorMessage(error, 'Failed to publish policy'));
      setPolicyMessage(null);
    } finally {
      if (mutations.finish(operation)) setPolicyAction(null);
    }
  };

  const handleRollback = async (version = selectedRollbackVersion) => {
    if (!canWrite) return;
    if (!version) return;
    const operation = mutations.begin();
    if (!operation) return;
    setPolicyAction('rollback');
    try {
      const result = await routeGroups.rollbackPolicy(routeGroupId, version, operation.signal);
      if (!mutations.isCurrent(operation)) return;
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
      if (!mutations.isCurrent(operation)) return;
      setPolicyError(mutationErrorMessage(error, 'Failed to rollback policy'));
      setPolicyMessage(null);
    } finally {
      if (mutations.finish(operation)) setPolicyAction(null);
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
        <div className="flex min-h-[400px] items-center justify-center" role="status" aria-label="Loading model group">
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
          <div role="alert" className="mb-3 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
            {detail.error instanceof ApiError && detail.error.status === 403
              ? 'You do not have permission to view this model group.'
              : detail.error instanceof ApiError && detail.error.status === 404
                ? 'Model group not found.' : 'Failed to load route group details.'}
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
        <RouteGroupHero group={group} publishedPolicy={publishedPolicy} members={members.length}
          healthyMembers={healthyMembers} unknownHealth={unknownHealth} missingMembers={missingMembers}
          promptKey={promptSummary?.templateKey ?? null} teamOptions={teamOptions} organizationOptions={organizationOptions}
          canWrite={canWrite} canDelete={canDelete} onEdit={() => setActiveTab('settings')}
          onDelete={() => setConfirmDeleteGroup(true)} />
      )}
      body={(
        <>
          <IconTabs
            id="model-group-tabs"
            label="Model group sections"
            active={activeTab}
            onChange={setActiveTab}
            items={TABS.map(({ id, label, icon }) => ({ id, label, icon }))}
          />
          {TABS.map(({ id }) => <div key={id} role="tabpanel" id={`model-group-tabs-panel-${id}`} aria-labelledby={`model-group-tabs-tab-${id}`} hidden={activeTab !== id}>
          {activeTab === id && (<>
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
              canWrite={canWrite}
            />
          ) : activeTab === 'advanced' || activeTab === 'policy' ? (
            <RouteGroupAdvancedTab
              section={activeTab}
              defaultStrategy={group.routing_strategy}
              routeGroupId={group.route_group_id}
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
              canWrite={canWrite}
              canManageBindings={isPlatformAdmin}
              policyText={policyText}
              policyMessage={policyMessage}
              policyError={policyError}
              isPolicyBusy={isPolicyBusy}
              policyAction={policyAction}
              showAdvancedJson={showAdvancedJson}
              hasMembers={memberIds.length > 0}
              onToggleAdvancedJson={() => {
                if (showAdvancedJson) {
                  const parsed = parsePolicyTextLoose(policyText);
                  if (!parsed) { setPolicyError('Invalid JSON payload'); return; }
                  setGuidedPolicy(toGuidedPolicy(parsed, members));
                } else setPolicyText(guidedPreview);
                setShowAdvancedJson((cur) => !cur);
              }}
              onGuidedPolicyChange={(next) => { setGuidedPolicy(next); setPolicyMessage(null); setPolicyError(null); }}
              onPolicyTextChange={(next) => { setPolicyText(next); setPolicyMessage(null); setPolicyError(null); }}
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
                <RouteGroupSettingsPanel
                  routeGroupId={routeGroupId}
                  group={group}
                  form={form}
                  onChange={setForm}
                  onSaved={detail.refetch}
                  mutations={mutations}
                />
              )}
            </PanelCard>
          )}
          </>)}
          </div>)}
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
