import { MODE_BADGE_COLORS, MODE_OPTIONS } from '../modelFormShared';

export default function ModelTypePill({ mode }: { mode: string }) {
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${MODE_BADGE_COLORS[mode] || 'bg-gray-100 text-gray-700'}`}>{MODE_OPTIONS.find((option) => option.value === mode)?.label || mode}</span>;
}
