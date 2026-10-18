import { useEffect, useState } from 'react';
import { Plus, Search, Server, X } from 'lucide-react';
import Button from '../../Button';
import { PanelCard } from '../../admin/shells';
import BundleServerSection from './BundleServerSection';
import { mcpBundles, type MCPBundleServerOption, type MCPBundleServerTools } from '../../../lib/api';
import { useApi } from '../../../lib/hooks';
import { MAX_BUNDLE_MEMBERS, type BundleMemberDraft } from '../../../lib/mcpBundles';

type BundleServersCardProps = {
  members: BundleMemberDraft[];
  serverTools: Readonly<Record<string, MCPBundleServerTools>>;
  conflictNames: ReadonlySet<string>;
  readOnly: boolean;
  errors: string[];
  onChange: (members: BundleMemberDraft[]) => void;
};

function ServerPicker({ exclude, onPick, onClose }: { exclude: ReadonlySet<string>; onPick: (server: MCPBundleServerOption) => void; onClose: () => void }) {
  const [input, setInput] = useState('');
  const [search, setSearch] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(input.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [input]);
  const { data, loading } = useApi((signal) => mcpBundles.searchServers({ search: search || undefined, limit: 20 }, signal), [search]);
  const options = (data?.data ?? []).filter((server) => !exclude.has(server.mcp_server_id));
  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50/40 p-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
          <input
            autoFocus
            aria-label="Search servers to add"
            placeholder="Search servers..."
            value={input}
            onChange={(event) => setInput(event.target.value)}
            className="control-field-md w-full rounded-lg border border-gray-200 bg-white pl-9 pr-3 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-brand-primary/30"
          />
        </div>
        <Button variant="ghost" size="icon" aria-label="Close server search" onClick={onClose}>
          <X className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
      <ul className="mt-2 max-h-64 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200 bg-white">
        {loading && !data ? <li className="px-3 py-3"><div className="h-5 animate-pulse rounded bg-gray-100" /></li> : null}
        {data && options.length === 0 ? <li className="px-3 py-3 text-sm text-gray-400">No other server matches.</li> : null}
        {options.map((server) => (
          <li key={server.mcp_server_id}>
            <button
              type="button"
              className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-blue-50/50"
              onClick={() => onPick(server)}
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-gray-900">{server.name}</span>
                <span className="block font-mono text-xs text-gray-400">{server.server_key}</span>
              </span>
              <span className="shrink-0 text-xs text-gray-500">{`${server.tool_count} tools${server.enabled ? '' : ' · disabled'}`}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function BundleServersCard({ members, serverTools, conflictNames, readOnly, errors, onChange }: BundleServersCardProps) {
  const [picking, setPicking] = useState(false);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set(members.slice(0, 1).map((member) => member.mcp_server_id)));
  const exclude = new Set(members.map((member) => member.mcp_server_id));

  const update = (serverId: string, patch: Partial<BundleMemberDraft>) => {
    onChange(members.map((member) => (member.mcp_server_id === serverId ? { ...member, ...patch } : member)));
  };
  const toggle = (serverId: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(serverId)) next.delete(serverId);
      else next.add(serverId);
      return next;
    });
  };
  const add = (server: MCPBundleServerOption) => {
    onChange([
      ...members,
      { mcp_server_id: server.mcp_server_id, server_key: server.server_key, server_name: server.name, all_tools: true, tool_names: [], name_prefix: '' },
    ]);
    setExpanded((current) => new Set([...current, server.mcp_server_id]));
    setPicking(false);
  };

  return (
    <PanelCard>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">Servers and tools</h2>
          <p className="mt-0.5 text-xs text-gray-500">Select the tools to show. A name prefix keeps the tool names of two servers apart.</p>
        </div>
        {!readOnly && !picking ? (
          <Button variant="outline" disabled={members.length >= MAX_BUNDLE_MEMBERS} onClick={() => setPicking(true)}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Add server
          </Button>
        ) : null}
      </div>
      <div className="mt-4 space-y-3">
        {picking ? <ServerPicker exclude={exclude} onPick={add} onClose={() => setPicking(false)} /> : null}
        {members.length === 0 && !picking ? (
          <div className="rounded-xl border border-dashed border-gray-200 py-10 text-center text-gray-400">
            <Server className="mx-auto mb-2 h-8 w-8 text-gray-200" aria-hidden="true" />
            <p className="text-sm">Add a server to select its tools.</p>
          </div>
        ) : null}
        {members.map((member) => (
          <BundleServerSection
            key={member.mcp_server_id}
            member={member}
            server={serverTools[member.mcp_server_id]}
            expanded={expanded.has(member.mcp_server_id)}
            readOnly={readOnly}
            conflictNames={conflictNames}
            onToggle={() => toggle(member.mcp_server_id)}
            onChange={(patch) => update(member.mcp_server_id, patch)}
            onRemove={() => onChange(members.filter((item) => item.mcp_server_id !== member.mcp_server_id))}
          />
        ))}
        {errors.length > 0 ? (
          <ul className="space-y-1 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert" aria-label="Bundle problems">
            {errors.map((error) => <li key={error}>{error}</li>)}
          </ul>
        ) : null}
      </div>
    </PanelCard>
  );
}
