import { GitBranch, ListOrdered, Shuffle } from 'lucide-react';
import { routeGroupStrategyLabel } from '../../lib/routeGroups';

const PRIMARY_STRATEGIES = [
  { id: 'simple-shuffle', label: 'Simple shuffle', description: 'Use random selection.', icon: Shuffle },
  { id: 'weighted', label: 'Weighted split', description: 'Set the traffic share.', icon: GitBranch },
  { id: 'priority-based-routing', label: 'Primary & fallback', description: 'Try deployments in order.', icon: ListOrdered },
];

export default function PolicyStrategyPicker({ value, options, onChange }: { value: string; options: string[]; onChange: (strategy: string) => void }) {
  const other = options.filter((option) => !PRIMARY_STRATEGIES.some((strategy) => strategy.id === option));
  return <div className="space-y-3">
    <p className="text-sm font-semibold text-gray-800">Routing strategy</p>
    <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
      {PRIMARY_STRATEGIES.filter((strategy) => options.includes(strategy.id)).map(({ id, label, description, icon: Icon }) => <button type="button" key={id} aria-pressed={value === id} onClick={() => onChange(id)} className={`flex items-start gap-2 rounded-lg border p-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${value === id ? 'border-brand-primary bg-brand-primary-soft text-brand-primary-ink' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}>
        <Icon className="mt-0.5 h-4 w-4 shrink-0" />
        <span><span className="block text-sm font-medium">{label}</span><span className="mt-1 block text-xs font-normal text-gray-500">{description}</span></span>
      </button>)}
    </div>
    {other.length > 0 && <label className="flex flex-wrap items-center gap-2 text-xs text-gray-500">Other strategies
      <select aria-label="Other routing strategies" value={other.includes(value) ? value : ''} onChange={(event) => event.target.value && onChange(event.target.value)} className="max-w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-brand-primary">
        <option value="" disabled>Select a strategy</option>
        {other.map((option) => <option key={option} value={option}>{routeGroupStrategyLabel(option)}</option>)}
      </select>
    </label>}
  </div>;
}
