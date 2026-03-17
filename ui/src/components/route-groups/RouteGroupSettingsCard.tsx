import { Save } from 'lucide-react';
import { ROUTE_GROUP_MODE_OPTIONS } from '../../lib/routeGroups';
import ToggleSwitch from '../ToggleSwitch';

interface GroupFormValues {
  name: string;
  mode: string;
  enabled: boolean;
}

interface RouteGroupSettingsCardProps {
  form: GroupFormValues;
  saving: boolean;
  onChange: (next: GroupFormValues) => void;
  onSave: () => void;
}

export default function RouteGroupSettingsCard({ form, saving, onChange, onSave }: RouteGroupSettingsCardProps) {
  return (
    <div className="space-y-5 max-w-lg">
      <div>
        <h4 className="text-sm font-semibold text-gray-900">Group identity &amp; traffic state</h4>
        <p className="mt-1 text-xs text-gray-500">Routing behavior is configured separately in the Advanced tab after you have the right members in place.</p>
      </div>

      {/* Name + Mode */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Display Name</label>
          <input
            value={form.name}
            onChange={(e) => onChange({ ...form, name: e.target.value })}
            placeholder="e.g. Production Chat"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Workload Mode</label>
          <select
            value={form.mode}
            onChange={(e) => onChange({ ...form, mode: e.target.value })}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {ROUTE_GROUP_MODE_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
              </option>
            ))}
          </select>
          <p className="mt-1 text-[11px] text-gray-400">Only deployments matching this mode can be added as members.</p>
        </div>
      </div>

      {/* Live traffic toggle */}
      <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <div className="text-sm font-semibold text-gray-900">Live Traffic</div>
          <div className="text-xs text-gray-500">
            {form.enabled
              ? 'This group is accepting requests from the gateway.'
              : 'Disabled — no requests will be routed through this group.'}
          </div>
        </div>
        <ToggleSwitch
          checked={form.enabled}
          onCheckedChange={(enabled) => onChange({ ...form, enabled })}
          aria-label="Toggle live traffic"
        />
      </div>

      {/* Save */}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={onSave}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          <Save className="h-4 w-4" />
          {saving ? 'Saving…' : 'Save Settings'}
        </button>
      </div>
    </div>
  );
}
