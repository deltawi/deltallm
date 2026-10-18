import { useEffect, useState } from 'react';
import { KeyRound, Search, X } from 'lucide-react';
import Button from '../../Button';
import { PanelCard } from '../../admin/shells';
import { keys, type ApiKey, type MCPBundleKeyPreview, type MCPBundleToolAccess } from '../../../lib/api';
import { useApi } from '../../../lib/hooks';
import { ACCESS_LABELS } from '../../../lib/mcpBundles';

export type SelectedKey = { token: string; label: string };

type BundlePreviewCardProps = {
  selected: SelectedKey | null;
  result: MCPBundleKeyPreview | null;
  loading: boolean;
  onSelect: (key: SelectedKey | null) => void;
};

const ACCESS_TONES: Record<MCPBundleToolAccess, string> = {
  available: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  needs_approval: 'border-amber-200 bg-amber-50 text-amber-700',
  disabled_by_policy: 'border-gray-200 bg-gray-100 text-gray-600',
  no_access: 'border-red-200 bg-red-50 text-red-700',
};

function keyLabel(key: ApiKey): string {
  return key.key_name || `${key.token.slice(0, 10)}…`;
}

function KeyPicker({ onSelect }: { onSelect: (key: SelectedKey) => void }) {
  const [input, setInput] = useState('');
  const [search, setSearch] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(input.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [input]);
  const { data, loading } = useApi((signal) => keys.list({ search: search || undefined, limit: 8 }, signal), [search]);
  const items = data?.data ?? [];
  return (
    <div>
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
        <input
          aria-label="Search API keys"
          placeholder="Search keys..."
          value={input}
          onChange={(event) => setInput(event.target.value)}
          className="control-field-md w-full rounded-lg border border-gray-200 bg-white pl-9 pr-3 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-brand-primary/30"
        />
      </div>
      <ul className="mt-2 max-h-56 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200">
        {loading && !data ? <li className="px-3 py-3"><div className="h-5 animate-pulse rounded bg-gray-100" /></li> : null}
        {data && items.length === 0 ? <li className="px-3 py-3 text-sm text-gray-400">No key matches.</li> : null}
        {items.map((key) => (
          <li key={key.token}>
            <button
              type="button"
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-blue-50/50"
              onClick={() => onSelect({ token: key.token, label: keyLabel(key) })}
            >
              <span className="truncate text-sm text-gray-900">{keyLabel(key)}</span>
              <span className="shrink-0 text-xs text-gray-400">{key.team_alias || key.team_id}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function BundlePreviewCard({ selected, result, loading, onSelect }: BundlePreviewCardProps) {
  const [picking, setPicking] = useState(false);
  return (
    <PanelCard>
      <h2 className="text-sm font-semibold text-gray-900">Preview</h2>
      <p className="mt-0.5 text-xs text-gray-500">Check which tools of the bundle one API key can use.</p>
      <div className="mt-3">
        {selected ? (
          <div className="flex items-center justify-between gap-2 rounded-lg border border-gray-200 px-3 py-2">
            <span className="flex min-w-0 items-center gap-2 text-sm text-gray-900">
              <KeyRound className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
              <span className="truncate">{selected.label}</span>
            </span>
            <Button variant="ghost" size="icon-sm" aria-label="Clear key" onClick={() => onSelect(null)}>
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>
        ) : picking ? (
          <KeyPicker onSelect={(key) => { setPicking(false); onSelect(key); }} />
        ) : (
          <Button variant="outline" fullWidth onClick={() => setPicking(true)}>
            <KeyRound className="h-4 w-4" aria-hidden="true" />
            Check a key
          </Button>
        )}
      </div>
      {selected ? (
        <div className="mt-3" aria-live="polite" data-testid="bundle-key-preview">
          {loading && !result ? <div className="h-16 animate-pulse rounded-lg bg-gray-100" /> : null}
          {result ? (
            <>
              <p className={`text-sm font-medium ${result.sees_bundle ? 'text-gray-900' : 'text-red-700'}`}>
                {result.sees_bundle ? `This key can use ${result.usable_count} of ${result.tools.length} tools.` : result.reason || 'This key cannot see the bundle.'}
              </p>
              <ul className="mt-2 space-y-1">
                {result.tools.map((tool) => (
                  <li key={tool.name} className="flex items-center justify-between gap-2 text-xs">
                    <span className="truncate font-mono text-gray-800">{tool.name}</span>
                    <span className={`shrink-0 rounded-full border px-2 py-0.5 font-medium ${ACCESS_TONES[tool.access]}`}>{ACCESS_LABELS[tool.access]}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </PanelCard>
  );
}
