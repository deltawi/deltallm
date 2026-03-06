import { Save } from 'lucide-react';
import Card from '../Card';
import { ROUTE_GROUP_MODE_OPTIONS } from '../../lib/routeGroups';

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
    <Card title="1. Basics">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Define the group shell</h4>
          <p className="mt-1 text-xs text-slate-500">Keep the first pass minimal. Groups use shuffle routing until you open Advanced and publish an override.</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Display Name</label>
          <input
            value={form.name}
            onChange={(event) => onChange({ ...form, name: event.target.value })}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Workload Type</label>
            <select
              value={form.mode}
              onChange={(event) => onChange({ ...form, mode: event.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {ROUTE_GROUP_MODE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700">
            <div className="font-medium text-slate-900">Default routing</div>
            <div className="mt-1 text-xs text-slate-500">Shuffle traffic across eligible members until you publish an override in Advanced.</div>
          </div>
        </div>
        <label className="flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(event) => onChange({ ...form, enabled: event.target.checked })}
            className="mt-0.5 rounded border-gray-300"
          />
          <span>
            Accept live traffic
            <span className="block text-xs text-gray-500">Turn this off while you are still assembling members or testing policy behavior.</span>
          </span>
        </label>
        <button
          type="button"
          onClick={onSave}
          disabled={saving}
          className="w-full mt-2 flex items-center justify-center gap-2 px-3 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {saving ? 'Saving...' : 'Save Basics'}
        </button>
      </div>
    </Card>
  );
}
