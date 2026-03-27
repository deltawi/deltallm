import type {
  GuardrailAction,
  GuardrailCatalog,
  GuardrailMode,
  GuardrailPreset,
  GuardrailPresetField,
  GuardrailRecord,
} from './api';

export interface GuardrailConfigInput {
  guardrail_name: string;
  deltallm_params: Record<string, unknown>;
}

export interface GuardrailFormState {
  guardrailName: string;
  editorKind: 'preset' | 'custom';
  presetId: string;
  classPath: string;
  mode: GuardrailMode;
  defaultAction: GuardrailAction;
  defaultOn: boolean;
  fieldValues: Record<string, unknown>;
  additionalParamsText: string;
}

export function defaultGuardrailFormState(catalog: GuardrailCatalog): GuardrailFormState {
  const preset = catalog.presets[0];
  return {
    guardrailName: '',
    editorKind: 'preset',
    presetId: preset?.preset_id ?? '',
    classPath: preset?.class_path ?? '',
    mode: preset?.supported_modes[0] ?? catalog.supported_modes[0] ?? 'pre_call',
    defaultAction: preset?.supported_actions[0] ?? catalog.supported_actions[0] ?? 'block',
    defaultOn: true,
    fieldValues: preset ? presetDefaultFieldValues(preset) : {},
    additionalParamsText: '',
  };
}

export function guardrailFormStateFromRecord(
  catalog: GuardrailCatalog,
  record: GuardrailRecord | null | undefined
): GuardrailFormState {
  if (!record) return defaultGuardrailFormState(catalog);

  const editor = record.editor;
  if (editor.preset_id) {
    const preset = catalog.presets.find((item) => item.preset_id === editor.preset_id);
    if (preset) {
      return {
        guardrailName: record.guardrail_name,
        editorKind: 'preset',
        presetId: preset.preset_id,
        classPath: preset.class_path,
        mode: clampMode(editor.mode, preset.supported_modes),
        defaultAction: clampAction(editor.default_action, preset.supported_actions),
        defaultOn: editor.default_on,
        fieldValues: {
          ...presetDefaultFieldValues(preset),
          ...editor.field_values,
        },
        additionalParamsText: toJsonText(editor.additional_params),
      };
    }
  }

  return {
    guardrailName: record.guardrail_name,
    editorKind: 'custom',
    presetId: '',
    classPath: editor.class_path || record.class_path || '',
    mode: clampMode(editor.mode, catalog.supported_modes),
    defaultAction: clampAction(editor.default_action, catalog.supported_actions),
    defaultOn: editor.default_on,
    fieldValues: {},
    additionalParamsText: toJsonText(editor.additional_params),
  };
}

export function getPresetById(catalog: GuardrailCatalog, presetId: string): GuardrailPreset | undefined {
  return catalog.presets.find((item) => item.preset_id === presetId);
}

export function buildGuardrailInput(
  form: GuardrailFormState,
  catalog: GuardrailCatalog
): GuardrailConfigInput {
  const guardrailName = form.guardrailName.trim();
  if (!guardrailName) {
    throw new Error('Guardrail name is required');
  }

  const additionalParams = parseAdditionalParams(form.additionalParamsText);
  const deltallmParams: Record<string, unknown> = {
    ...additionalParams,
    mode: form.mode,
    default_action: form.defaultAction,
    default_on: form.defaultOn,
  };

  if (form.editorKind === 'preset') {
    const preset = getPresetById(catalog, form.presetId);
    if (!preset) {
      throw new Error('Select a built-in guardrail preset');
    }
    deltallmParams.guardrail = preset.class_path;
    if (preset.preset_id === 'lakera_prompt_injection') {
      const apiKey = String(form.fieldValues.api_key ?? '').trim();
      if (!apiKey) {
        throw new Error('Lakera API key is required');
      }
    }
    for (const field of preset.fields) {
      const value = normalizePresetFieldValue(field, form.fieldValues[field.key]);
      const defaultValue = normalizePresetFieldValue(field, field.default_value);
      if (
        preset.preset_id === 'presidio_pii' &&
        field.key === 'entities' &&
        catalog.capabilities.presidio.engine_mode !== 'full'
      ) {
        deltallmParams[field.key] = value;
        continue;
      }
      if (!presetFieldShouldPersist(value, defaultValue)) {
        continue;
      }
      deltallmParams[field.key] = value;
    }
  } else {
    const classPath = form.classPath.trim();
    if (!classPath) {
      throw new Error('Guardrail class path is required for advanced custom guardrails');
    }
    deltallmParams.guardrail = classPath;
  }

  return {
    guardrail_name: guardrailName,
    deltallm_params: deltallmParams,
  };
}

export function presetDefaultFieldValues(preset: GuardrailPreset): Record<string, unknown> {
  return Object.fromEntries(
    preset.fields.map((field) => [
      field.key,
      normalizePresetFieldValue(field, field.default_value),
    ])
  );
}

export function hasConfiguredAdvancedPresetValues(
  preset: GuardrailPreset | undefined,
  fieldValues: Record<string, unknown>
): boolean {
  if (!preset) return false;
  return preset.fields
    .filter((field) => field.advanced)
    .some((field) =>
      presetFieldShouldPersist(
        normalizePresetFieldValue(field, fieldValues[field.key]),
        normalizePresetFieldValue(field, field.default_value)
      )
    );
}

function parseAdditionalParams(raw: string): Record<string, unknown> {
  const trimmed = raw.trim();
  if (!trimmed) return {};

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error('Additional parameters must be valid JSON');
  }

  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('Additional parameters must be a JSON object');
  }

  return parsed as Record<string, unknown>;
}

function toJsonText(value: Record<string, unknown>): string {
  if (!value || Object.keys(value).length === 0) return '';
  return JSON.stringify(value, null, 2);
}

function clampMode(mode: string | undefined, allowed: GuardrailMode[]): GuardrailMode {
  if (mode && allowed.includes(mode as GuardrailMode)) {
    return mode as GuardrailMode;
  }
  return allowed[0] ?? 'pre_call';
}

function clampAction(action: string | undefined, allowed: GuardrailAction[]): GuardrailAction {
  if (action && allowed.includes(action as GuardrailAction)) {
    return action as GuardrailAction;
  }
  return allowed[0] ?? 'block';
}

function normalizeNumber(value: unknown, fallback: number): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizePresetFieldValue(field: GuardrailPresetField, value: unknown): unknown {
  if (field.input === 'boolean') {
    return Boolean(value);
  }
  if (field.input === 'multiselect') {
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string')
      : [];
  }
  if (field.input === 'number') {
    return normalizeNumber(value, Number(field.default_value ?? 0));
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed && typeof field.default_value === 'string' && field.default_value !== '') {
      return field.default_value;
    }
    return trimmed;
  }
  return typeof field.default_value === 'string' ? field.default_value : '';
}

function presetFieldShouldPersist(currentValue: unknown, defaultValue: unknown): boolean {
  return JSON.stringify(currentValue) !== JSON.stringify(defaultValue);
}

export function toggleMultiSelectValue(
  currentValue: unknown,
  optionValue: string
): string[] {
  const current = Array.isArray(currentValue)
    ? currentValue.filter((item): item is string => typeof item === 'string')
    : [];
  return current.includes(optionValue)
    ? current.filter((item) => item !== optionValue)
    : [...current, optionValue];
}

export function supportsAdvancedPresetFields(preset: GuardrailPreset | undefined): boolean {
  if (!preset) return false;
  return preset.fields.some((field) => field.advanced);
}

export function isAdvancedField(field: GuardrailPresetField): boolean {
  return field.advanced === true;
}
