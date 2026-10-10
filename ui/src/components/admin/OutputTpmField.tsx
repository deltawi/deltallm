import { useId } from 'react';
import { OUTPUT_TPM_HELP } from '../../lib/outputTpm';

export default function OutputTpmField({ value, onChange, error, disabled }: { value: string; onChange: (value: string) => void; error?: string | null; disabled?: boolean }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-gray-700 mb-1">Output TPM limit</label>
      <input id={id} type="number" min={1} max={2147483647} step={1} value={value}
        onChange={(event) => onChange(event.target.value)} placeholder="Unlimited"
        disabled={disabled} aria-invalid={Boolean(error)}
        aria-describedby={`${id}-help${error ? ` ${id}-error` : ''}`}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary" />
      <p id={`${id}-help`} className="mt-1 text-xs text-gray-500">{OUTPUT_TPM_HELP}</p>
      {error && <p id={`${id}-error`} role="alert" className="mt-2 text-sm text-red-600">{error}</p>}
    </div>
  );
}
