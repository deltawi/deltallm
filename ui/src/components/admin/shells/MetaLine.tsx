import { useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';

// Pieces of the one-line summary under a detail page title (route groups, models).

export function MetaLink({ children, onClick, warn = false, label }: { children: ReactNode; onClick: () => void; warn?: boolean; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className={`rounded-sm border-b border-dotted text-xs transition hover:border-solid focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${warn ? 'border-amber-400 text-amber-700 hover:text-amber-800' : 'border-gray-300 text-gray-600 hover:text-gray-900'}`}
    >
      {children}
    </button>
  );
}

export function MetaSeparator() {
  return <span aria-hidden="true" className="h-3 w-px bg-gray-200" />;
}

// A state word with a colored dot, e.g. "Live" or "Healthy".
export function MetaState({ tone, children }: { tone: 'good' | 'off' | 'bad'; children: ReactNode }) {
  const colors = tone === 'good' ? ['text-emerald-700', 'bg-emerald-500'] : tone === 'bad' ? ['text-red-700', 'bg-red-500'] : ['text-gray-500', 'bg-gray-400'];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${colors[0]}`}>
      <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${colors[1]}`} />
      {children}
    </span>
  );
}

// A monospace identifier with a copy button.
export function MetaCopyValue({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(value).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }).catch(() => setCopied(false));
  };
  return (
    <span className="inline-flex items-center gap-1">
      <code className="font-mono text-xs text-gray-700">{value}</code>
      <button
        type="button"
        onClick={copy}
        aria-label={`Copy ${label}`}
        title={`Copy ${label}`}
        className="rounded p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      >
        {copied ? <Check className="h-3 w-3 text-emerald-600" aria-hidden="true" /> : <Copy className="h-3 w-3" aria-hidden="true" />}
      </button>
      <span role="status" className="sr-only">{copied ? `${label.charAt(0).toUpperCase()}${label.slice(1)} copied` : ''}</span>
    </span>
  );
}
