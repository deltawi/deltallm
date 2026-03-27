import { useEffect, useMemo, useState } from 'react';
import Modal from '../Modal';
import type { GuardrailCatalog, GuardrailPreset, GuardrailRecord } from '../../lib/api';
import {
  buildGuardrailInput,
  defaultGuardrailFormState,
  getPresetById,
  guardrailFormStateFromRecord,
  hasConfiguredAdvancedPresetValues,
  isAdvancedField,
  presetDefaultFieldValues,
  supportsAdvancedPresetFields,
  type GuardrailConfigInput,
  type GuardrailFormState,
} from '../../lib/guardrails';
import GuardrailFieldInput from './GuardrailFieldInput';

interface GuardrailFormModalProps {
  open: boolean;
  item: GuardrailRecord | null;
  catalog: GuardrailCatalog | null;
  onClose: () => void;
  onSave: (payload: GuardrailConfigInput) => Promise<void>;
}

export default function GuardrailFormModal({ open, item, catalog, onClose, onSave }: GuardrailFormModalProps) {
  const [form, setForm] = useState<GuardrailFormState | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open || !catalog) return;
    const nextForm = guardrailFormStateFromRecord(catalog, item);
    const preset = nextForm.editorKind === 'preset' ? getPresetById(catalog, nextForm.presetId) : undefined;
    setForm(nextForm);
    setShowAdvanced(
      Boolean(nextForm.additionalParamsText.trim()) ||
        hasConfiguredAdvancedPresetValues(preset, nextForm.fieldValues)
    );
    setError(null);
    setSaving(false);
  }, [catalog, item, open]);

  const selectedPreset = useMemo<GuardrailPreset | undefined>(() => {
    if (!catalog || !form || form.editorKind !== 'preset') return undefined;
    return getPresetById(catalog, form.presetId);
  }, [catalog, form]);
  const presidioCapability = catalog?.capabilities.presidio;
  const isPresidioPreset = selectedPreset?.preset_id === 'presidio_pii';
  const isLakeraPreset = selectedPreset?.preset_id === 'lakera_prompt_injection';
  const lakeraApiKeyMissing = isLakeraPreset && !String(form?.fieldValues.api_key ?? '').trim();

  const modeOptions = selectedPreset?.supported_modes ?? catalog?.supported_modes ?? ['pre_call'];
  const actionOptions = selectedPreset?.supported_actions ?? catalog?.supported_actions ?? ['block'];

  const visiblePresetFields = useMemo(() => {
    if (!selectedPreset) return [];
    return selectedPreset.fields.filter((field) => showAdvanced || !isAdvancedField(field));
  }, [selectedPreset, showAdvanced]);

  const handlePresetChange = (preset: GuardrailPreset) => {
    setForm((current) => {
      if (!current) return current;
      return {
        ...current,
        editorKind: 'preset',
        presetId: preset.preset_id,
        classPath: preset.class_path,
        mode: preset.supported_modes.includes(current.mode) ? current.mode : preset.supported_modes[0],
        defaultAction: preset.supported_actions.includes(current.defaultAction) ? current.defaultAction : preset.supported_actions[0],
        fieldValues: presetDefaultFieldValues(preset),
        additionalParamsText: '',
      };
    });
    setShowAdvanced(false);
  };

  const handleEditorKindChange = (kind: 'preset' | 'custom') => {
    if (!catalog) return;
    if (kind === 'preset') {
      setForm((current) => {
        const next = current ? { ...current } : defaultGuardrailFormState(catalog);
        const preset = getPresetById(catalog, next.presetId) ?? catalog.presets[0];
        return {
          ...next,
          editorKind: 'preset',
          presetId: preset?.preset_id ?? '',
          classPath: preset?.class_path ?? '',
          mode: preset?.supported_modes[0] ?? catalog.supported_modes[0] ?? 'pre_call',
          defaultAction: preset?.supported_actions[0] ?? catalog.supported_actions[0] ?? 'block',
          fieldValues: preset ? presetDefaultFieldValues(preset) : {},
          additionalParamsText: '',
        };
      });
      setShowAdvanced(false);
      return;
    }

    setForm((current) => {
      const next = current ? { ...current } : defaultGuardrailFormState(catalog);
      return {
        ...next,
        editorKind: 'custom',
        presetId: '',
        classPath: next.classPath || '',
        mode: catalog.supported_modes.includes(next.mode) ? next.mode : catalog.supported_modes[0] ?? 'pre_call',
        defaultAction: catalog.supported_actions.includes(next.defaultAction) ? next.defaultAction : catalog.supported_actions[0] ?? 'block',
      };
    });
    setShowAdvanced(true);
  };

  const submit = async () => {
    if (!catalog || !form) return;
    setError(null);
    setSaving(true);
    try {
      await onSave(buildGuardrailInput(form, catalog));
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save guardrail');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={item ? 'Edit Guardrail' : 'Add Guardrail'} wide>
      {!catalog || !form ? (
        <div className="py-8 text-sm text-slate-500">Loading guardrail presets…</div>
      ) : (
        <div className="space-y-5">
          {error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-700">{error}</div>
          ) : null}

          {presidioCapability && presidioCapability.engine_mode !== 'full' ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800">
              Full Presidio packages are not installed. DeltaLLM is using limited regex fallback mode.
              Build Docker with <code className="rounded bg-amber-100 px-1 py-0.5">INSTALL_PRESIDIO=true</code> or
              install the optional extra with <code className="rounded bg-amber-100 px-1 py-0.5">uv sync --extra guardrails-presidio</code>.
            </div>
          ) : null}

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Guardrail name</label>
            <input
              data-autofocus="true"
              value={form.guardrailName}
              onChange={(event) => setForm({ ...form, guardrailName: event.target.value })}
              placeholder="presidio-pii"
              disabled={Boolean(item)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-slate-50 disabled:text-slate-500"
            />
          </div>

          <div className="space-y-3">
            <div>
              <p className="text-sm font-medium text-slate-700">Guardrail type</p>
              <p className="mt-1 text-xs text-slate-500">Use a built-in preset for common safety policies, or switch to advanced mode for a custom class.</p>
            </div>
            <div className="inline-flex rounded-lg border border-slate-200 p-1">
              <button
                type="button"
                onClick={() => handleEditorKindChange('preset')}
                className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  form.editorKind === 'preset' ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                Built-in presets
              </button>
              <button
                type="button"
                onClick={() => handleEditorKindChange('custom')}
                className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  form.editorKind === 'custom' ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                Advanced custom
              </button>
            </div>
          </div>

          {form.editorKind === 'preset' ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {catalog.presets.map((preset) => {
                const selected = preset.preset_id === form.presetId;
                return (
                  <button
                    key={preset.preset_id}
                    type="button"
                    onClick={() => handlePresetChange(preset)}
                    className={`rounded-xl border px-4 py-3 text-left transition-colors ${
                      selected ? 'border-blue-300 bg-blue-50 shadow-sm' : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                    }`}
                  >
                    <div className="text-sm font-medium text-slate-900">{preset.label}</div>
                    <p className="mt-1 text-xs text-slate-500">{preset.description}</p>
                  </button>
                );
              })}
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Guardrail class path</label>
              <input
                value={form.classPath}
                onChange={(event) => setForm({ ...form, classPath: event.target.value })}
                placeholder="src.guardrails.custom.MyGuardrail"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="mt-1 text-xs text-slate-500">Use this only for custom runtime guardrails that are already available in the backend codebase.</p>
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Mode</label>
              <select
                value={form.mode}
                onChange={(event) => setForm({ ...form, mode: event.target.value as typeof form.mode })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {modeOptions.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode === 'pre_call' ? 'Pre-call' : 'Post-call'}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Action</label>
              <select
                value={form.defaultAction}
                onChange={(event) => setForm({ ...form, defaultAction: event.target.value as typeof form.defaultAction })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {actionOptions.map((action) => (
                  <option key={action} value={action}>
                    {action === 'block' ? 'Block' : 'Log only'}
                  </option>
                ))}
              </select>
            </div>
            <label className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700">
              <input
                type="checkbox"
                className="rounded border-slate-300"
                checked={form.defaultOn}
                onChange={(event) => setForm({ ...form, defaultOn: event.target.checked })}
              />
              Enabled by default
            </label>
          </div>

          {form.editorKind === 'preset' && selectedPreset ? (
            <div className="space-y-4">
              {isPresidioPreset && presidioCapability && presidioCapability.engine_mode !== 'full' ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800">
                  Only regex-backed entities are available in fallback mode: {presidioCapability.fallback_supported_entities.join(', ')}.
                </div>
              ) : null}
              {lakeraApiKeyMissing ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800">
                  Lakera checks are skipped when no API key is configured. Add an API key before saving this guardrail.
                </div>
              ) : null}
              {visiblePresetFields.map((field) => (
                <GuardrailFieldInput
                  key={field.key}
                  field={field}
                  value={form.fieldValues[field.key]}
                  onChange={(value) =>
                    setForm((current) =>
                      current
                        ? {
                            ...current,
                            fieldValues: {
                              ...current.fieldValues,
                              [field.key]: value,
                            },
                          }
                        : current
                    )
                  }
                />
              ))}
              {(supportsAdvancedPresetFields(selectedPreset) || form.additionalParamsText.trim()) ? (
                <div className="space-y-3 rounded-xl border border-dashed border-slate-300 px-4 py-4">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <h3 className="text-sm font-medium text-slate-900">Advanced options</h3>
                      <p className="mt-1 text-xs text-slate-500">Use these only when the default preset fields are not enough.</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setShowAdvanced((current) => !current)}
                      className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
                    >
                      {showAdvanced ? 'Hide advanced' : 'Show advanced'}
                    </button>
                  </div>
                  {showAdvanced ? (
                    <div className="space-y-4">
                      {selectedPreset.fields.filter(isAdvancedField).map((field) => (
                        <GuardrailFieldInput
                          key={field.key}
                          field={field}
                          value={form.fieldValues[field.key]}
                          onChange={(value) =>
                            setForm((current) =>
                              current
                                ? {
                                    ...current,
                                    fieldValues: {
                                      ...current.fieldValues,
                                      [field.key]: value,
                                    },
                                  }
                                : current
                            )
                          }
                        />
                      ))}
                      <div>
                        <label className="block text-sm font-medium text-slate-700 mb-1">Additional parameters (JSON)</label>
                        <textarea
                          rows={6}
                          value={form.additionalParamsText}
                          onChange={(event) => setForm({ ...form, additionalParamsText: event.target.value })}
                          placeholder='{"custom_flag": true}'
                          className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          {form.editorKind === 'custom' ? (
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Constructor parameters (JSON)</label>
              <textarea
                rows={8}
                value={form.additionalParamsText}
                onChange={(event) => setForm({ ...form, additionalParamsText: event.target.value })}
                placeholder='{"threshold": 0.5}'
                className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          ) : null}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-4 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-100"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={saving}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? 'Saving…' : item ? 'Save changes' : 'Create'}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
