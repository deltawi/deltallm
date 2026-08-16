import { Edit3, LockKeyhole, Plus, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  OrganizationTierAssignment,
  OrganizationTierAssignmentPayload,
  Tier,
  TierPolicySimulation,
  TierPolicySimulationPayload,
} from '../../lib/api';
import { organizations, tiers } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import {
  errorMessage,
  formatDateTime,
  isAssignableTierVersion,
  parsePositiveIntegerInput,
  summarizeSimulation,
  tierAssignmentRequiresActiveVersion,
} from '../../lib/tiers';
import { useToast } from '../ToastProvider';
import StatusBadge from '../StatusBadge';
import OrganizationTierAssignmentDrawer, { type AssignmentForm } from './OrganizationTierAssignmentDrawer';
import TierPolicyPreviewPanel from './TierPolicyPreviewPanel';
import TierSimulationPanel from './TierSimulationPanel';

type OrganizationTierPanelProps = {
  organizationId: string;
  canManage: boolean;
};

const EMPTY_FORM: AssignmentForm = {
  assignment_id: null,
  tier_id: '',
  tier_version_id: '',
  assignment_type: 'primary',
  enabled: true,
  weight: '1',
  starts_at: '',
  ends_at: '',
};

function createEmptyForm(): AssignmentForm {
  return { ...EMPTY_FORM };
}

export default function OrganizationTierPanel({ organizationId, canManage }: OrganizationTierPanelProps) {
  const { pushToast } = useToast();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<AssignmentForm>(() => createEmptyForm());
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [simulation, setSimulation] = useState<TierPolicySimulation | null>(null);
  const [simulationLoading, setSimulationLoading] = useState(false);
  const [simulationError, setSimulationError] = useState<string | null>(null);
  const organizationIdRef = useRef(organizationId);
  const assignmentMutationRequestRef = useRef(0);
  const simulationRequestRef = useRef(0);

  useEffect(() => {
    organizationIdRef.current = organizationId;
    assignmentMutationRequestRef.current += 1;
    simulationRequestRef.current += 1;
    setFormOpen(false);
    setForm(createEmptyForm());
    setFormError(null);
    setSaving(false);
    setSimulation(null);
    setSimulationLoading(false);
    setSimulationError(null);
  }, [organizationId]);

  const { data: assignmentsResponse, loading: assignmentsLoading, error: assignmentsError, refetch: refetchAssignments } = useApi(
    () => canManage ? organizations.tierAssignments(organizationId) : Promise.resolve({ data: [] }),
    [organizationId, canManage],
  );
  const { data: tierPage } = useApi(
    () => canManage ? tiers.listAll({ enabled: true }) : Promise.resolve([]),
    [canManage],
  );
  const { data: selectedTierDetail } = useApi(
    () => canManage && form.tier_id ? tiers.get(form.tier_id) : Promise.resolve(null),
    [canManage, form.tier_id],
  );
  const { data: preview, loading: previewLoading, error: previewError, refetch: refetchPreview } = useApi(
    () => canManage ? organizations.tierPolicyPreview(organizationId) : Promise.resolve(null),
    [organizationId, canManage],
  );

  const assignments = useMemo(
    () => assignmentsResponse?.data || [],
    [assignmentsResponse],
  );
  const tierOptions = useMemo(
    () => withCurrentAssignmentTierOption(tierPage || [], assignments, form.assignment_id),
    [assignments, form.assignment_id, tierPage],
  );
  const currentSelectedTierDetail = selectedTierDetail?.tier?.tier_id === form.tier_id
    ? selectedTierDetail
    : null;
  const selectedTierVersions = currentSelectedTierDetail?.versions || [];
  const requireActiveVersion = tierAssignmentRequiresActiveVersion(form);
  const callableOptions = useMemo(() => {
    if (!preview) return [];
    return Array.from(
      new Set([
        ...preview.allowed_callable_keys,
        ...preview.model_policies.map((policy) => policy.callable_key),
      ]),
    ).sort();
  }, [preview]);

  const isCurrentAssignmentMutation = (requestId: number, requestOrganizationId: string) => (
    assignmentMutationRequestRef.current === requestId && organizationIdRef.current === requestOrganizationId
  );

  const isCurrentSimulationRequest = (requestId: number, requestOrganizationId: string) => (
    simulationRequestRef.current === requestId && organizationIdRef.current === requestOrganizationId
  );

  const invalidateAssignmentMutation = () => {
    assignmentMutationRequestRef.current += 1;
  };

  const openCreate = () => {
    if (!canManage || saving) return;
    invalidateAssignmentMutation();
    setForm(createEmptyForm());
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (assignment: OrganizationTierAssignment) => {
    if (!canManage || saving) return;
    invalidateAssignmentMutation();
    setForm({
      assignment_id: assignment.assignment_id,
      tier_id: assignment.tier_id,
      tier_version_id: assignment.tier_version_id || '',
      assignment_type: assignment.assignment_type || 'primary',
      enabled: assignment.enabled,
      weight: String(assignment.weight || 1),
      starts_at: toDateTimeLocal(assignment.starts_at),
      ends_at: toDateTimeLocal(assignment.ends_at),
    });
    setFormError(null);
    setFormOpen(true);
  };

  const closeForm = () => {
    if (saving) return;
    invalidateAssignmentMutation();
    setFormOpen(false);
  };

  const handleFormChange = (nextForm: AssignmentForm) => {
    if (saving) return;
    if (nextForm.tier_version_id && tierAssignmentRequiresActiveVersion(nextForm)) {
      const selectedVersion = selectedTierVersions.find(
        (version) => version.tier_version_id === nextForm.tier_version_id,
      );
      if (selectedVersion && !isAssignableTierVersion(selectedVersion, true)) {
        setForm({ ...nextForm, tier_version_id: '' });
        return;
      }
    }
    setForm(nextForm);
  };

  const saveAssignment = async () => {
    if (!canManage || saving) return;
    if (!form.tier_id) {
      setFormError('Select a tier.');
      return;
    }
    setFormError(null);
    let weight: number;
    try {
      weight = parsePositiveIntegerInput(form.weight, 'Weight');
    } catch (err: unknown) {
      setFormError(errorMessage(err, 'Weight must be a positive integer.'));
      return;
    }
    const saveRequiresActiveVersion = tierAssignmentRequiresActiveVersion(form);
    if (form.tier_version_id) {
      if (!currentSelectedTierDetail) {
        setFormError('Wait for tier versions to load before saving.');
        return;
      }
      const selectedVersion = selectedTierVersions.find(
        (version) => version.tier_version_id === form.tier_version_id,
      );
      if (!selectedVersion) {
        setFormError('Selected tier version is not available for this tier.');
        return;
      }
      if (!isAssignableTierVersion(selectedVersion, saveRequiresActiveVersion)) {
        setFormError('Enabled tier assignments can only pin an active version.');
        return;
      }
    }
    const requestOrganizationId = organizationId;
    const requestId = assignmentMutationRequestRef.current + 1;
    assignmentMutationRequestRef.current = requestId;
    setSaving(true);
    const payload: OrganizationTierAssignmentPayload = {
      tier_id: form.tier_id,
      tier_version_id: form.tier_version_id || null,
      assignment_type: form.assignment_type,
      enabled: form.enabled,
      weight,
      starts_at: isoOrNull(form.starts_at),
      ends_at: isoOrNull(form.ends_at),
    };
    try {
      if (form.assignment_id) {
        await organizations.updateTierAssignment(requestOrganizationId, form.assignment_id, payload);
      } else {
        await organizations.createTierAssignment(requestOrganizationId, payload);
      }
      if (!isCurrentAssignmentMutation(requestId, requestOrganizationId)) return;
      setFormOpen(false);
      refetchAssignments();
      refetchPreview();
      pushToast({ tone: 'success', title: 'Tier assignment saved', message: 'Organization tier policy will refresh.' });
    } catch (err: unknown) {
      if (!isCurrentAssignmentMutation(requestId, requestOrganizationId)) return;
      setFormError(errorMessage(err, 'Failed to save tier assignment.'));
    } finally {
      if (isCurrentAssignmentMutation(requestId, requestOrganizationId)) {
        setSaving(false);
      }
    }
  };

  const deleteAssignment = async (assignment: OrganizationTierAssignment) => {
    if (!canManage || saving) return;
    if (!confirm(`Remove ${assignment.tier_name || assignment.tier_key || assignment.tier_id} from this organization?`)) return;
    const requestOrganizationId = organizationId;
    const requestId = assignmentMutationRequestRef.current + 1;
    assignmentMutationRequestRef.current = requestId;
    setSaving(true);
    try {
      await organizations.deleteTierAssignment(requestOrganizationId, assignment.assignment_id);
      if (!isCurrentAssignmentMutation(requestId, requestOrganizationId)) return;
      refetchAssignments();
      refetchPreview();
      pushToast({ tone: 'success', title: 'Tier assignment removed', message: 'Organization tier policy will refresh.' });
    } catch (err: unknown) {
      if (!isCurrentAssignmentMutation(requestId, requestOrganizationId)) return;
      pushToast({ tone: 'error', title: 'Remove failed', message: errorMessage(err, 'Failed to remove tier assignment.') });
    } finally {
      if (isCurrentAssignmentMutation(requestId, requestOrganizationId)) {
        setSaving(false);
      }
    }
  };

  const runSimulation = async (payload: TierPolicySimulationPayload) => {
    if (!canManage) return;
    const requestOrganizationId = organizationId;
    const requestId = simulationRequestRef.current + 1;
    simulationRequestRef.current = requestId;
    setSimulationLoading(true);
    setSimulationError(null);
    try {
      const result = await organizations.simulateTierPolicy(requestOrganizationId, payload);
      if (!isCurrentSimulationRequest(requestId, requestOrganizationId)) return;
      setSimulation(result);
    } catch (err: unknown) {
      if (!isCurrentSimulationRequest(requestId, requestOrganizationId)) return;
      setSimulation(null);
      setSimulationError(errorMessage(err, 'Simulation failed.'));
    } finally {
      if (isCurrentSimulationRequest(requestId, requestOrganizationId)) {
        setSimulationLoading(false);
      }
    }
  };

  if (!canManage) {
    return (
      <section className="flex items-start gap-3 rounded-xl border border-gray-200 bg-gray-50 p-4">
        <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0 text-gray-500" />
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Service policy is read-only</h3>
          <p className="mt-1 text-xs leading-relaxed text-gray-600">
            Tier assignments are managed by a platform administrator. Organization roles cannot assign, change, or remove tiers.
          </p>
        </div>
      </section>
    );
  }

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-gray-200 bg-white">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Tier Assignments</h3>
            <p className="mt-0.5 text-xs text-gray-500">Primary, add-on, and override tiers enabled for this organization.</p>
          </div>
          {canManage ? (
            <button
              type="button"
              onClick={openCreate}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-xs font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
            >
              <Plus className="h-3.5 w-3.5" />
              Assign tier
            </button>
          ) : null}
        </div>
        {assignmentsError ? (
          <div className="m-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
            {errorMessage(assignmentsError, 'Failed to load tier assignments.')}
          </div>
        ) : null}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-400">
              <tr>
                <th className="px-4 py-2 text-left">Tier</th>
                <th className="px-4 py-2 text-left">Type</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">Window</th>
                <th className="px-4 py-2 text-left">Weight</th>
                <th className="px-4 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {assignmentsLoading ? (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-400">Loading assignments...</td></tr>
              ) : assignments.length === 0 ? (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-400">No tiers assigned.</td></tr>
              ) : assignments.map((assignment) => (
                <tr key={assignment.assignment_id}>
                  <td className="px-4 py-3">
                    <p className="text-sm font-semibold text-gray-900">{assignment.tier_name || assignment.tier_key || assignment.tier_id}</p>
                    <p className="font-mono text-xs text-gray-400">
                      {assignment.tier_key || assignment.tier_id}
                      {assignment.tier_version_number ? ` · v${assignment.tier_version_number}` : ''}
                    </p>
                  </td>
                  <td className="px-4 py-3 text-xs font-semibold text-gray-700">{assignment.assignment_type}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={assignment.enabled ? 'enabled' : 'disabled'} label={assignment.enabled ? 'Enabled' : 'Disabled'} />
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {formatDateTime(assignment.starts_at)} to {formatDateTime(assignment.ends_at)}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-700">{assignment.weight}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <button type="button" onClick={() => openEdit(assignment)} disabled={!canManage || saving} className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40">
                        <Edit3 className="h-3.5 w-3.5" />
                      </button>
                      <button type="button" onClick={() => deleteAssignment(assignment)} disabled={!canManage || saving} className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-40">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <TierPolicyPreviewPanel
        preview={preview}
        loading={previewLoading}
        error={previewError ? errorMessage(previewError, 'Failed to load effective tier policy.') : null}
        onRefresh={refetchPreview}
      />

      <TierSimulationPanel
        callableOptions={callableOptions}
        simulation={simulation}
        loading={simulationLoading}
        error={simulationError}
        onRun={runSimulation}
      />
      {simulation ? (
        <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs text-gray-500">
          {summarizeSimulation(simulation)}
        </div>
      ) : null}

      {formOpen ? (
        <OrganizationTierAssignmentDrawer
          form={form}
          tierOptions={tierOptions}
          versionOptions={selectedTierVersions}
          requireActiveVersion={requireActiveVersion}
          saving={saving}
          error={formError}
          onChange={handleFormChange}
          onClose={closeForm}
          onSave={saveAssignment}
        />
      ) : null}
    </div>
  );
}

function isoOrNull(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function toDateTimeLocal(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const offsetMs = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

function withCurrentAssignmentTierOption(
  enabledTiers: Tier[],
  assignments: OrganizationTierAssignment[],
  assignmentId: string | null,
): Tier[] {
  if (!assignmentId) return enabledTiers;
  const currentAssignment = assignments.find((assignment) => assignment.assignment_id === assignmentId);
  if (!currentAssignment) return enabledTiers;
  if (enabledTiers.some((tier) => tier.tier_id === currentAssignment.tier_id)) {
    return enabledTiers;
  }
  return [
    ...enabledTiers,
    {
      tier_id: currentAssignment.tier_id,
      tier_key: currentAssignment.tier_key || currentAssignment.tier_id,
      name: currentAssignment.tier_name || currentAssignment.tier_key || currentAssignment.tier_id,
      description: null,
      enabled: false,
      metadata: null,
      active_version_id: currentAssignment.tier_version_id || null,
      version_count: currentAssignment.tier_version_number ? 1 : 0,
      assignment_count: 1,
      created_at: null,
      updated_at: null,
    },
  ].sort((a, b) => a.name.localeCompare(b.name));
}
