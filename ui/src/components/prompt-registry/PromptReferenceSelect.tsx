import type { PromptLabel, PromptVersion } from '../../lib/api';
import { parseReferenceValue, referenceValue, type PromptReference } from '../../lib/promptVersions';

interface PromptReferenceSelectProps {
  id: string;
  label: string;
  value: PromptReference;
  labels: PromptLabel[];
  versions: PromptVersion[];
  onChange: (reference: PromptReference) => void;
  className?: string;
}

/** One list for "a label" or "a fixed version", so a test or view never has two conflicting inputs. */
export default function PromptReferenceSelect({ id, label, value, labels, versions, onChange, className = '' }: PromptReferenceSelectProps) {
  return (
    <select
      id={id}
      aria-label={label}
      value={referenceValue(value)}
      onChange={(event) => onChange(parseReferenceValue(event.target.value))}
      className={`rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-brand-primary ${className}`}
    >
      {labels.length > 0 ? (
        <optgroup label="Labels">
          {labels.map((item) => (
            <option key={item.prompt_label_id} value={`label:${item.label}`}>{item.label} · v{item.version}</option>
          ))}
        </optgroup>
      ) : null}
      <optgroup label="Versions">
        {versions.map((item) => (
          <option key={item.prompt_version_id} value={`version:${item.version}`}>v{item.version}</option>
        ))}
      </optgroup>
    </select>
  );
}
