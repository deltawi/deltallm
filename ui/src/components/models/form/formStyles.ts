// Form fields are 36 px (control-field), see ui/README.md "Control Sizes".
const FIELD_BASE = 'control-field w-full min-w-0 rounded-lg border bg-white px-3 text-gray-900 placeholder:text-gray-400 transition focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500 read-only:bg-gray-50 read-only:text-gray-600';

export function fieldClass(hasError = false): string {
  return `${FIELD_BASE} ${hasError
    ? 'border-red-300 focus:border-red-400 focus:ring-red-500/30'
    : 'border-gray-200 focus:border-blue-400 focus:ring-brand-primary/30'}`;
}

export const HELP_CLASS = 'mt-1 text-[11px] text-gray-400';
