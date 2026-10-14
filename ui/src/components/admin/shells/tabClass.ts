// Classes for a line or header tab. Route-based tabs (links) use it too, so they match IconTabs.
export function tabClass(active: boolean): string {
  return `flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${
    active ? 'border-brand-primary text-brand-primary-ink' : 'border-transparent text-gray-500 hover:text-gray-700'
  }`;
}
