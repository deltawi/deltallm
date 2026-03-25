import { useState } from 'react';
import ToggleSwitch from '../ToggleSwitch';
import { ChevronRight, Info, Key } from 'lucide-react';

type Props = {
  enabled: boolean;
  maxKeysPerUser: string;
  budgetCeiling: string;
  requireExpiry: boolean;
  maxExpiryDays: string;
  disabled?: boolean;
  onEnabledChange: (value: boolean) => void;
  onMaxKeysPerUserChange: (value: string) => void;
  onBudgetCeilingChange: (value: string) => void;
  onRequireExpiryChange: (value: boolean) => void;
  onMaxExpiryDaysChange: (value: string) => void;
};

export default function TeamSelfServicePolicySection({
  enabled,
  maxKeysPerUser,
  budgetCeiling,
  requireExpiry,
  maxExpiryDays,
  disabled = false,
  onEnabledChange,
  onMaxKeysPerUserChange,
  onBudgetCeilingChange,
  onRequireExpiryChange,
  onMaxExpiryDaysChange,
}: Props) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  return (
    <div className="space-y-3">
      <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-2.5">
            <Key className="w-4 h-4 text-indigo-600 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium text-gray-800">Allow developers to create personal API keys</p>
              <p className="text-xs text-gray-500 mt-0.5">
                Recommended for most teams. Developers can create their own keys within the team policy.
              </p>
            </div>
          </div>
          <ToggleSwitch
            checked={enabled}
            onCheckedChange={onEnabledChange}
            disabled={disabled}
            aria-label="Toggle self-service key creation"
          />
        </div>

        <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-indigo-50 border border-indigo-200 text-xs text-indigo-700">
          <Info className="w-3.5 h-3.5 shrink-0" />
          <span>Leave the optional limits blank unless this team needs tighter self-service guardrails.</span>
        </div>

        {enabled && (
          <>
            <button
              type="button"
              onClick={() => setShowAdvanced((current) => !current)}
              disabled={disabled}
              className="flex items-center gap-1.5 text-xs font-medium text-indigo-700 hover:text-indigo-800 disabled:opacity-50"
            >
              <ChevronRight className={`w-3.5 h-3.5 transition-transform ${showAdvanced ? 'rotate-90' : ''}`} />
              Optional self-service limits
            </button>

            {showAdvanced && (
              <div className="ml-6 pl-3 border-l-2 border-indigo-200 space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">Max keys per user</label>
                    <input
                      value={maxKeysPerUser}
                      onChange={(event) => onMaxKeysPerUserChange(event.target.value)}
                      type="number"
                      min="1"
                      placeholder="Unlimited"
                      disabled={disabled}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">Budget ceiling ($)</label>
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
                      <input
                        value={budgetCeiling}
                        onChange={(event) => onBudgetCeilingChange(event.target.value)}
                        type="number"
                        min="0"
                        step="0.01"
                        placeholder="No ceiling"
                        disabled={disabled}
                        className="w-full pl-7 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
                      />
                    </div>
                  </div>
                </div>

                <div className="p-3 rounded-lg bg-white border border-gray-200 space-y-3">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium text-gray-800">Require an expiry date</p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        Enable this if self-service keys must always expire.
                      </p>
                    </div>
                    <ToggleSwitch
                      checked={requireExpiry}
                      onCheckedChange={onRequireExpiryChange}
                      disabled={disabled}
                      aria-label="Toggle self-service expiry requirement"
                    />
                  </div>

                  {requireExpiry && (
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1.5">Max expiry days</label>
                      <input
                        value={maxExpiryDays}
                        onChange={(event) => onMaxExpiryDaysChange(event.target.value)}
                        type="number"
                        min="1"
                        placeholder="No limit"
                        disabled={disabled}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
                      />
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
