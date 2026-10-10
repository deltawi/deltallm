import { useId } from 'react';
import type { ModelOutputRow } from '../../lib/modelOutputTpm';

export default function ModelOutputTpmEditor({ rows, onChange, disabled = false }: {
  rows: ModelOutputRow[]; onChange: (rows: ModelOutputRow[]) => void; disabled?: boolean;
}) {
  const id = useId();
  const update = (index: number, field: keyof ModelOutputRow, value: string) => {
    onChange(rows.map((row, i) => i === index ? { ...row, [field]: value } : row));
  };
  return <fieldset disabled={disabled} className="min-w-0 space-y-3" aria-describedby={`${id}-help`}>
    <legend className="text-sm font-medium text-gray-700">Output TPM per model</legend>
    <p id={`${id}-help`} className="text-xs text-gray-500">
      Use the exact model ID sent by clients. These limits share usage across this scope and apply
      alongside its total and the organization’s tier allowance. Removing a row clears only this limit.
    </p>
    {rows.map((row, index) => <div key={index} className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
      <div><label className="text-xs text-gray-600" htmlFor={`${id}-model-${index}`}>Model ID {index + 1}</label>
        <input id={`${id}-model-${index}`} value={row.model} onChange={(e) => update(index, 'model', e.target.value)}
          placeholder="Exact model ID" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" /></div>
      <div><label className="text-xs text-gray-600" htmlFor={`${id}-limit-${index}`}>Output TPM {index + 1}</label>
        <input id={`${id}-limit-${index}`} type="number" min={1} max={2147483647} step={1} value={row.limit}
          onChange={(e) => update(index, 'limit', e.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" /></div>
      <button type="button" className="self-end rounded-lg border px-3 py-2 text-sm" aria-label={`Remove model limit ${index + 1}`}
        onClick={() => onChange(rows.filter((_, i) => i !== index))}>Remove</button>
    </div>)}
    <button type="button" disabled={disabled || rows.length >= 64} className="rounded-lg border px-3 py-2 text-sm"
      onClick={() => onChange([...rows, { model: '', limit: '' }])}>Add model limit</button>
  </fieldset>;
}
