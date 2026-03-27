import type { GuardrailPresetField } from '../../lib/api';
import { toggleMultiSelectValue } from '../../lib/guardrails';

interface GuardrailFieldInputProps {
  field: GuardrailPresetField;
  value: unknown;
  onChange: (value: unknown) => void;
}

export default function GuardrailFieldInput({ field, value, onChange }: GuardrailFieldInputProps) {
  if (field.input === 'boolean') {
    return (
      <label className="flex items-start gap-3 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-3">
        <input
          type="checkbox"
          className="mt-1 rounded border-slate-300"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="space-y-1">
          <span className="block text-sm font-medium text-slate-900">{field.label}</span>
          {field.help_text ? <span className="block text-xs text-slate-500">{field.help_text}</span> : null}
        </span>
      </label>
    );
  }

  if (field.input === 'multiselect') {
    const currentValues = Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
    return (
      <div className="space-y-2">
        <div>
          <label className="block text-sm font-medium text-slate-700">{field.label}</label>
          {field.help_text ? <p className="mt-1 text-xs text-slate-500">{field.help_text}</p> : null}
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {(field.options || []).map((option) => {
            const checked = currentValues.includes(option.value);
            const disabled = option.disabled === true;
            return (
              <label
                key={option.value}
                className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors ${
                  disabled
                    ? 'cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400'
                    : checked
                      ? 'border-blue-300 bg-blue-50 text-blue-900'
                      : 'border-slate-200 text-slate-700'
                }`}
              >
                <input
                  type="checkbox"
                  className="rounded border-slate-300"
                  checked={checked}
                  disabled={disabled}
                  onChange={() => onChange(toggleMultiSelectValue(currentValues, option.value))}
                />
                <span className="min-w-0">
                  <span className="block">{option.label}</span>
                  {option.description ? <span className="block text-xs text-slate-500">{option.description}</span> : null}
                </span>
              </label>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1">{field.label}</label>
      <input
        type={field.input === 'number' ? 'number' : field.input === 'secret' ? 'password' : 'text'}
        value={typeof value === 'string' || typeof value === 'number' ? value : ''}
        onChange={(event) => onChange(field.input === 'number' ? event.target.value : event.target.value)}
        placeholder={field.placeholder}
        min={field.min}
        max={field.max}
        step={field.step}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
      {field.help_text ? <p className="mt-1 text-xs text-slate-500">{field.help_text}</p> : null}
    </div>
  );
}
