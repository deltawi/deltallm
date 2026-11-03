import ToggleSwitch from '../ToggleSwitch';
import {
  CHAT_CAPABILITY_OPTIONS,
  type ChatCapabilityFlags,
  type ChatCapabilityKey,
} from '../modelFormShared';

interface ChatCapabilitiesFieldsProps {
  declared: boolean;
  capabilities: ChatCapabilityFlags;
  onDeclaredChange: (declared: boolean) => void;
  onCapabilityChange: (key: ChatCapabilityKey, supported: boolean) => void;
}

export default function ChatCapabilitiesFields({
  declared,
  capabilities,
  onDeclaredChange,
  onCapabilityChange,
}: ChatCapabilitiesFieldsProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-gray-700">Declare capabilities</p>
          <p className="mt-0.5 text-xs text-gray-500">
            Off means unknown. When on, auto-routing only sends a request to this model if every feature it needs is
            checked, so check only what this deployment really supports.
          </p>
        </div>
        <ToggleSwitch checked={declared} onCheckedChange={onDeclaredChange} aria-label="Declare capabilities" />
      </div>
      {declared ? (
        <fieldset className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <legend className="sr-only">Supported features</legend>
          {CHAT_CAPABILITY_OPTIONS.map((option) => (
            <label
              key={option.key}
              className="flex cursor-pointer items-start gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50"
            >
              <input
                type="checkbox"
                className="mt-0.5"
                checked={capabilities[option.key]}
                onChange={(event) => onCapabilityChange(option.key, event.target.checked)}
              />
              <span>
                <span className="block font-medium text-gray-700">{option.label}</span>
                <span className="block text-xs text-gray-500">{option.description}</span>
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}
    </div>
  );
}
