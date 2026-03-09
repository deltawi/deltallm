import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import JourneyChecklist from '../components/JourneyChecklist';
import { useToast } from '../components/ToastProvider';
import { models, promptRegistry, routeGroups, type PromptBinding } from '../lib/api';
import { useApi } from '../lib/hooks';
import { buildPolicyFromGuided, GUIDED_POLICY_DEFAULTS, parsePolicyTextLoose, toGuidedPolicy, type PolicyAction, type PolicyGuidedValues } from '../lib/routeGroups';
import RouteGroupSettingsCard from '../components/route-groups/RouteGroupSettingsCard';
import RouteGroupMembersCard from '../components/route-groups/RouteGroupMembersCard';
import RouteGroupPolicyEditorCard from '../components/route-groups/RouteGroupPolicyEditorCard';
import RouteGroupPolicyVersionsCard from '../components/route-groups/RouteGroupPolicyVersionsCard';
import RouteGroupUsageCard from '../components/route-groups/RouteGroupUsageCard';
import RouteGroupPromptBindingCard from '../components/route-groups/RouteGroupPromptBindingCard';

function requiredPromptVariables(schema: unknown): string[] {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return [];
  const required = (schema as Record<string, unknown>).required;
  if (!Array.isArray(required)) return [];
  return required.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

export default function RouteGroupDetail() {
  const { groupKey } = useParams<{ groupKey: string }>();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const [savingGroup, setSavingGroup] = useState(false);
  const [addingMember, setAddingMember] = useState(false);
  const [savingBinding, setSavingBinding] = useState(false);
  const [deletingBinding, setDeletingBinding] = useState<string | null>(null);
  const [policyMessage, setPolicyMessage] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [selectedRollbackVersion, setSelectedRollbackVersion] = useState<number | null>(null);
  const [showAdvancedJson, setShowAdvancedJson] = useState(false);
  const [showAdvancedSection, setShowAdvancedSection] = useState(false);
  const [policyAction, setPolicyAction] = useState<PolicyAction>(null);
  const [memberSearchInput, setMemberSearchInput] = useState('');
  const [memberSearch, setMemberSearch] = useState('');
  const [manualMemberEntry, setManualMemberEntry] = useState(false);
  const [memberToRemove, setMemberToRemove] = useState<string | null>(null);
  const [removingMember, setRemovingMember] = useState(false);
  const [form, setForm] = useState({
    name: '',
    mode: 'chat',
    enabled: true,
  });
  const [memberForm, setMemberForm] = useState({
    deployment_id: '',
    weight: '',
    priority: '',
    enabled: true,
  });
  const [bindingForm, setBindingForm] = useState({
    template_key: '',
    label: 'production',
    priority: '100',
    enabled: true,
  });
  const [guidedPolicy, setGuidedPolicy] = useState<PolicyGuidedValues>(GUIDED_POLICY_DEFAULTS);
  const [policyText, setPolicyText] = useState('{\n  "strategy": "weighted"\n}');

  const detail = useApi(() => routeGroups.get(groupKey!), [groupKey]);
  const policyHistory = useApi(() => routeGroups.listPolicies(groupKey!), [groupKey]);
  const groupBindings = useApi(
    () => promptRegistry.listBindings({ scope_type: 'group', scope_id: groupKey!, limit: 20, offset: 0 }),
    [groupKey]
  );
  const promptTemplates = useApi(() => promptRegistry.listTemplates({ limit: 100, offset: 0 }), []);
  const bindingPreview = useApi(() => promptRegistry.previewResolution({ route_group_key: groupKey! }), [groupKey]);
  const deploymentCandidates = useApi(
    () => models.list({ search: memberSearch, mode: form.mode, limit: 20, offset: 0 }),
    [memberSearch, form.mode]
  );

  const policies = policyHistory.data?.policies || [];
  const members = detail.data?.members || [];
  const bindings = groupBindings.data?.data || [];
  const memberIds = useMemo(() => members.map((member) => member.deployment_id), [members]);
  const isPolicyBusy = policyAction !== null;
  const publishedPolicy = useMemo(() => policies.find((policy) => policy.status === 'published') || null, [policies]);
  const draftPolicy = useMemo(() => policies.find((policy) => policy.status === 'draft') || null, [policies]);
  const winningPrompt = bindingPreview.data?.winner || null;
  const winningPromptDetail = useApi(
    () => (winningPrompt?.template_key ? promptRegistry.getTemplate(String(winningPrompt.template_key)) : Promise.resolve(null)),
    [winningPrompt?.template_key]
  );

  useEffect(() => {
    const group = detail.data?.group;
    if (!group) return;
    setForm({
      name: group.name || '',
      mode: group.mode || 'chat',
      enabled: !!group.enabled,
    });
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
    const policyJson =
      preferred.policy_json && typeof preferred.policy_json === 'object' && !Array.isArray(preferred.policy_json)
        ? preferred.policy_json
        : {};
    setPolicyText(JSON.stringify(policyJson, null, 2));
    setGuidedPolicy(toGuidedPolicy(policyJson, memberIds));
    const rollbackCandidates = policies
      .filter((policy) => policy.status === 'archived' || policy.status === 'published')
      .sort((left, right) => right.version - left.version);
    setSelectedRollbackVersion(rollbackCandidates[0]?.version ?? null);
  }, [draftPolicy, memberIds, policies]);

  useEffect(() => {
    if (memberIds.length === 0) {
      setGuidedPolicy((current) => (current.memberIds.length === 0 ? current : { ...current, memberIds: [] }));
      return;
    }
    setGuidedPolicy((current) => {
      const filtered = current.memberIds.filter((memberId) => memberIds.includes(memberId));
      const nextMembers = filtered.length > 0 ? filtered : memberIds;
      if (nextMembers.length === current.memberIds.length && nextMembers.every((memberId, index) => memberId === current.memberIds[index])) {
        return current;
      }
      return { ...current, memberIds: nextMembers };
    });
  }, [memberIds]);

  const canRollbackVersions = useMemo(
    () => policies.filter((policy) => policy.status === 'archived' || policy.status === 'published'),
    [policies]
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

  const promptSummary = useMemo(() => {
    if (!winningPrompt || !winningPromptDetail.data) return null;
    const detailPayload = winningPromptDetail.data;
    const versionFromLabel =
      typeof winningPrompt.label === 'string'
        ? detailPayload.labels.find((item) => item.label === winningPrompt.label)?.version
        : null;
    const resolvedVersion =
      (typeof versionFromLabel === 'number' ? versionFromLabel : null) ??
      detailPayload.versions.find((item) => item.status === 'published')?.version ??
      detailPayload.versions[0]?.version;
    const versionRecord = detailPayload.versions.find((item) => item.version === resolvedVersion) || null;

    return {
      templateKey: String(winningPrompt.template_key),
      label: typeof winningPrompt.label === 'string' ? winningPrompt.label : null,
      requiredVariables: requiredPromptVariables(versionRecord?.variables_schema),
    };
  }, [winningPrompt, winningPromptDetail.data]);

  const checklistSteps = [
    { label: 'Add members', done: members.length > 0, hint: 'At least one deployment is required before the group can serve traffic.' },
    {
      label: 'Choose prompt',
      done: !winningPrompt || !!promptSummary,
      hint: winningPrompt ? `Bound prompt ${String(winningPrompt.template_key)} is ready to resolve for this group.` : 'Optional. Leave this empty if the group should run without an attached prompt.',
    },
    { label: 'Enable live traffic', done: !!detail.data?.group?.enabled, hint: 'Leave this off until members and any prompt binding look correct.' },
  ];

  if (detail.loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[400px]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (detail.error && !detail.data?.group) {
    return (
      <div className="p-6">
        <button onClick={() => navigate('/route-groups')} className="mb-4 flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700">
          <ArrowLeft className="w-4 h-4" /> Back to Route Groups
        </button>
        <div className="mb-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">Failed to load route group details.</div>
        <button type="button" onClick={detail.refetch} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">
          Retry
        </button>
      </div>
    );
  }

  if (!detail.data?.group) {
    return (
      <div className="p-6">
        <button onClick={() => navigate('/route-groups')} className="mb-4 flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700">
          <ArrowLeft className="w-4 h-4" /> Back to Route Groups
        </button>
        <p className="text-gray-500">Route group not found.</p>
      </div>
    );
  }

  const parsePolicy = (): Record<string, unknown> | null => {
    if (!showAdvancedJson) {
      const basePolicy = parsePolicyTextLoose(policyText) || {};
      const payload = buildPolicyFromGuided(basePolicy, guidedPolicy);
      setPolicyText(JSON.stringify(payload, null, 2));
      setPolicyError(null);
      return payload;
    }
    const parsed = parsePolicyTextLoose(policyText);
    if (!parsed) {
      setPolicyError('Invalid JSON payload');
      return null;
    }
    setPolicyError(null);
    return parsed;
  };

  const handleSaveGroup = async () => {
    setSavingGroup(true);
    try {
      await routeGroups.update(groupKey!, {
        name: form.name.trim() || null,
        mode: form.mode,
        enabled: form.enabled,
      });
      await detail.refetch();
      pushToast({ tone: 'success', title: 'Group updated', message: 'Route group settings were saved.' });
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Update failed', message: error?.message || 'Failed to update route group.' });
    } finally {
      setSavingGroup(false);
    }
  };

  const handleAddMember = async () => {
    if (!memberForm.deployment_id.trim()) {
      pushToast({ tone: 'error', title: 'Missing deployment', message: 'Select or enter a deployment ID before adding.' });
      return;
    }
    setAddingMember(true);
    try {
      await routeGroups.upsertMember(groupKey!, {
        deployment_id: memberForm.deployment_id.trim(),
        enabled: memberForm.enabled,
        weight: memberForm.weight ? Number(memberForm.weight) : null,
        priority: memberForm.priority ? Number(memberForm.priority) : null,
      });
      setMemberForm({ deployment_id: '', weight: '', priority: '', enabled: true });
      setMemberSearchInput('');
      setMemberSearch('');
      setManualMemberEntry(false);
      pushToast({ tone: 'success', title: 'Member added', message: 'Deployment was added to the route group.' });
      await detail.refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Add member failed', message: error?.message || 'Failed to add deployment to group.' });
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
      await Promise.all([groupBindings.refetch(), bindingPreview.refetch()]);
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Bind prompt failed', message: error?.message || 'Failed to save prompt binding.' });
    } finally {
      setSavingBinding(false);
    }
  };

  const handleDeleteBinding = async (binding: PromptBinding) => {
    setDeletingBinding(binding.prompt_binding_id);
    try {
      await promptRegistry.deleteBinding(binding.prompt_binding_id);
      pushToast({ tone: 'success', title: 'Binding removed', message: 'The prompt is no longer bound to this group.' });
      await Promise.all([groupBindings.refetch(), bindingPreview.refetch()]);
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Remove binding failed', message: error?.message || 'Failed to remove prompt binding.' });
    } finally {
      setDeletingBinding(null);
    }
  };

  const handleRemoveMember = async () => {
    if (!memberToRemove) return;
    setRemovingMember(true);
    try {
      await routeGroups.removeMember(groupKey!, memberToRemove);
      pushToast({ tone: 'success', title: 'Member removed', message: `Deployment "${memberToRemove}" was removed.` });
      setMemberToRemove(null);
      await detail.refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Remove member failed', message: error?.message || 'Failed to remove member.' });
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
      setGuidedPolicy(toGuidedPolicy(result.policy, memberIds));
      setPolicyMessage(result.warnings?.length ? `Valid with warnings: ${result.warnings.join(' ')}` : 'Policy is valid.');
      setPolicyError(null);
    } catch (error: any) {
      setPolicyError(error?.message || 'Policy validation failed');
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
      setPolicyMessage(`Draft saved (v${result.policy.version}).`);
      setPolicyError(null);
      await policyHistory.refetch();
    } catch (error: any) {
      setPolicyError(error?.message || 'Failed to save draft');
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
      setPolicyMessage(`Published policy version ${result.policy.version}.`);
      setPolicyError(null);
      await Promise.all([detail.refetch(), policyHistory.refetch()]);
    } catch (error: any) {
      setPolicyError(error?.message || 'Failed to publish policy');
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
      setPolicyMessage(`Rolled back from version ${result.rolled_back_from_version} to new published version ${result.policy.version}.`);
      setPolicyError(null);
      await Promise.all([detail.refetch(), policyHistory.refetch()]);
    } catch (error: any) {
      setPolicyError(error?.message || 'Failed to rollback policy');
      setPolicyMessage(null);
    } finally {
      setPolicyAction(null);
    }
  };

  return (
    <div className="max-w-6xl mx-auto p-4 sm:p-6">
      <button onClick={() => navigate('/route-groups')} className="mb-5 flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 transition-colors">
        <ArrowLeft className="w-4 h-4" /> Back to Route Groups
      </button>

      <div className="mb-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{detail.data.group.name || detail.data.group.group_key}</h1>
            <p className="mt-1 text-sm text-gray-500">
              Group key: <code className="rounded bg-gray-100 px-1.5 py-0.5">{detail.data.group.group_key}</code>
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Members</div>
              <div className="mt-1 text-lg font-semibold text-slate-900">{members.length}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Routing</div>
              <div className="mt-1 text-sm font-semibold text-slate-900">{publishedPolicy ? `Override v${publishedPolicy.version}` : 'Default shuffle'}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Prompt</div>
              <div className="mt-1 text-sm font-semibold text-slate-900">{promptSummary ? promptSummary.templateKey : 'None bound'}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Traffic</div>
              <div className="mt-1 text-sm font-semibold text-slate-900">{detail.data.group.enabled ? 'Live' : 'Off'}</div>
            </div>
          </div>
        </div>
      </div>

      <JourneyChecklist
        title="Setup Progress"
        description="Model groups are easiest to configure in this order: add members, optionally choose a prompt, then turn on live traffic."
        steps={checklistSteps}
      />

      <div className="mt-5 space-y-5">
        <RouteGroupSettingsCard form={form} saving={savingGroup} onChange={setForm} onSave={handleSaveGroup} />
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
          onToggleManualEntry={() => setManualMemberEntry((current) => !current)}
          onMemberSearchChange={setMemberSearchInput}
          onAddMember={handleAddMember}
          onRequestRemoveMember={setMemberToRemove}
        />
        <RouteGroupUsageCard
          groupKey={detail.data.group.group_key}
          mode={form.mode}
          liveTrafficEnabled={detail.data.group.enabled}
          boundPrompt={promptSummary}
        />

        <details
          open={showAdvancedSection}
          className="rounded-2xl border border-slate-200 bg-white px-4 py-4"
        >
          <summary
            className="cursor-pointer list-none text-sm font-semibold text-slate-900"
            onClick={(event) => {
              event.preventDefault();
              setShowAdvancedSection((current) => !current);
            }}
          >
            4. Advanced
          </summary>
          {showAdvancedSection && (
            <div className="mt-4 space-y-5">
              <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700">
                Use advanced settings only when this group needs something beyond default shuffle and no-prompt behavior.
              </div>

              <RouteGroupPromptBindingCard
                bindings={bindings}
                templates={promptTemplates.data?.data || []}
                bindingForm={bindingForm}
                loadingTemplates={promptTemplates.loading}
                savingBinding={savingBinding}
                deletingBinding={deletingBinding}
                onBindingFormChange={setBindingForm}
                onSaveBinding={handleSaveBinding}
                onDeleteBinding={handleDeleteBinding}
              />

              <RouteGroupPolicyEditorCard
                guidedPolicy={guidedPolicy}
                memberIds={memberIds}
                guidedPreview={guidedPreview}
                policyText={policyText}
                policyMessage={policyMessage}
                policyError={policyError}
                isPolicyBusy={isPolicyBusy}
                policyAction={policyAction}
                showAdvancedJson={showAdvancedJson}
                hasMembers={memberIds.length > 0}
                onToggleAdvancedJson={() => setShowAdvancedJson((current) => !current)}
                onGuidedPolicyChange={setGuidedPolicy}
                onPolicyTextChange={setPolicyText}
                onValidate={handleValidatePolicy}
                onSaveDraft={handleSaveDraft}
                onPublish={handlePublish}
              />
              <RouteGroupPolicyVersionsCard
                policies={policies}
                canRollbackVersions={canRollbackVersions}
                selectedRollbackVersion={selectedRollbackVersion}
                loading={policyHistory.loading}
                hasError={!!policyHistory.error}
                isPolicyBusy={isPolicyBusy}
                policyAction={policyAction}
                onRollbackVersionChange={setSelectedRollbackVersion}
                onRollback={handleRollback}
              />
            </div>
          )}
        </details>
      </div>

      <ConfirmDialog
        open={!!memberToRemove}
        title="Remove route group member"
        description={memberToRemove ? `Remove "${memberToRemove}" from this route group? Policy references to this member may become invalid.` : ''}
        confirmLabel="Remove Member"
        destructive
        confirming={removingMember}
        onConfirm={handleRemoveMember}
        onClose={() => {
          if (!removingMember) setMemberToRemove(null);
        }}
      />
    </div>
  );
}
