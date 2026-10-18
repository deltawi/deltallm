import { useId, useState } from 'react';
import { ChevronDown, ChevronRight, Search, Trash2 } from 'lucide-react';
import Button from '../../Button';
import type { MCPBundleServerTools } from '../../../lib/api';
import { exposedToolName, memberToolNames, type BundleMemberDraft } from '../../../lib/mcpBundles';

type BundleServerSectionProps = {
  member: BundleMemberDraft;
  server: MCPBundleServerTools | undefined;
  expanded: boolean;
  readOnly: boolean;
  conflictNames: ReadonlySet<string>;
  onToggle: () => void;
  onChange: (patch: Partial<BundleMemberDraft>) => void;
  onRemove: () => void;
};

function Badge({ tone, children }: { tone: 'amber' | 'gray' | 'red'; children: string }) {
  const tones = {
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    gray: 'border-gray-200 bg-gray-100 text-gray-600',
    red: 'border-red-200 bg-red-50 text-red-700',
  };
  return <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>{children}</span>;
}

export default function BundleServerSection({
  member,
  server,
  expanded,
  readOnly,
  conflictNames,
  onToggle,
  onChange,
  onRemove,
}: BundleServerSectionProps) {
  const [filter, setFilter] = useState('');
  const bodyId = useId();
  const tools = server?.tools ?? [];
  const toolNames = tools.map((tool) => tool.name);
  const selected = new Set(memberToolNames(member, toolNames));
  const selectedCount = server ? toolNames.filter((name) => selected.has(name)).length : null;
  const query = filter.trim().toLowerCase();
  const visibleTools = query
    ? tools.filter((tool) => tool.name.toLowerCase().includes(query) || (tool.description ?? '').toLowerCase().includes(query))
    : tools;

  const toggleTool = (name: string) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange({ all_tools: false, tool_names: toolNames.filter((item) => next.has(item)) });
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white" data-testid={`bundle-member-${member.server_key}`}>
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={onToggle}
        >
          {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" /> : <ChevronRight className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />}
          <span className="min-w-0">
            <span className="block truncate font-medium text-gray-900">{member.server_name || member.server_key}</span>
            <span className="block font-mono text-xs text-gray-400">{member.server_key}</span>
          </span>
          <span className="ml-auto shrink-0 rounded-md border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-600">
            {member.all_tools ? 'All tools' : `${selectedCount ?? member.tool_names.length} of ${server ? toolNames.length : '…'} tools`}
          </span>
        </button>
        <label className="flex items-center gap-2 text-xs text-gray-500">
          Name prefix
          <input
            aria-label={`Name prefix for ${member.server_key}`}
            className="control-field-md w-28 rounded-lg border border-gray-200 bg-white px-2 font-mono text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-brand-primary/30"
            value={member.name_prefix}
            maxLength={32}
            disabled={readOnly}
            placeholder="none"
            onChange={(event) => onChange({ name_prefix: event.target.value })}
          />
        </label>
        {!readOnly ? (
          <Button variant="ghost" size="icon-sm" aria-label={`Remove ${member.server_key}`} title="Remove server" onClick={onRemove}>
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        ) : null}
      </div>

      {expanded ? (
        <div id={bodyId} className="border-t border-gray-100 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[12rem] flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
              <input
                aria-label={`Search tools of ${member.server_key}`}
                placeholder="Search tools..."
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                className="control-field-md w-full rounded-lg border border-gray-200 bg-white pl-9 pr-3 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-brand-primary/30"
              />
            </div>
            {!readOnly ? (
              <>
                <Button variant="outline" size="sm" onClick={() => onChange({ all_tools: true, tool_names: [] })}>Select all</Button>
                <Button variant="outline" size="sm" onClick={() => onChange({ all_tools: false, tool_names: [] })}>Clear</Button>
              </>
            ) : null}
          </div>
          {member.all_tools ? (
            <p className="mt-2 text-[11px] text-gray-500">All tools are selected. Tools that the server adds later are also in the bundle.</p>
          ) : null}
          {!server ? (
            <div className="mt-3 space-y-2" aria-label="Loading tools">
              {[0, 1, 2].map((row) => <div key={row} className="h-8 animate-pulse rounded bg-gray-100" />)}
            </div>
          ) : (
            <ul className="mt-3 divide-y divide-gray-100 rounded-lg border border-gray-100">
              {visibleTools.length === 0 ? (
                <li className="px-3 py-4 text-center text-sm text-gray-400">{tools.length === 0 ? 'This server has no tools.' : 'No tool matches the search.'}</li>
              ) : null}
              {visibleTools.map((tool) => {
                const exposed = exposedToolName(member.name_prefix, tool.name);
                const checked = selected.has(tool.name);
                return (
                  <li key={tool.name} className="flex items-start gap-3 px-3 py-2">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 rounded border-gray-300"
                      checked={checked}
                      disabled={readOnly}
                      aria-label={`Include ${tool.name}`}
                      onChange={() => toggleTool(tool.name)}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm text-gray-900">{tool.name}</span>
                        {exposed !== tool.name ? <span className="font-mono text-xs text-gray-400">{`as ${exposed}`}</span> : null}
                        {tool.needs_approval ? <Badge tone="amber">Needs approval</Badge> : null}
                        {tool.disabled_by_policy ? <Badge tone="gray">Disabled by policy</Badge> : null}
                        {checked && conflictNames.has(exposed) ? <Badge tone="red">Name conflict</Badge> : null}
                      </div>
                      {tool.description ? <p className="mt-0.5 line-clamp-2 text-xs text-gray-500">{tool.description}</p> : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
