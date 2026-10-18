import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Boxes, ChevronLeft, ChevronRight, Info, Plus, RefreshCw, Search } from 'lucide-react';
import Button from '../../Button';
import { CopyButton } from '../../batch-jobs/BatchBits';
import { mcpBundles, type MCPBundle } from '../../../lib/api';
import { useAuth } from '../../../lib/auth';
import { hasPermission, isPlatformAdminSession } from '../../../lib/authorization';
import { errorMessage } from '../../../lib/errorMessage';
import { useApi } from '../../../lib/hooks';
import {
  BUNDLE_INFO_TEXT,
  MCP_BUNDLE_NEW_PATH,
  bundleEndpointUrl,
  mcpBundlePath,
  ownerScopeLabel,
} from '../../../lib/mcpBundles';

const PAGE_SIZE = 20;
const HEAD = 'px-6 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500';

function SkeletonRows() {
  return (
    <>
      {[0, 1, 2].map((row) => (
        <tr key={row} data-testid="bundle-skeleton-row">
          {[0, 1, 2, 3].map((cell) => (
            <td key={cell} className="px-6 py-4">
              <div className="h-4 w-full max-w-[12rem] animate-pulse rounded bg-gray-100" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function ServerChips({ bundle }: { bundle: MCPBundle }) {
  if (bundle.members.length === 0) return <span className="text-xs text-gray-400">No servers</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {bundle.members.map((member) => (
        <span
          key={member.mcp_server_id}
          title={member.server_name}
          className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${
            member.server_enabled ? 'border-gray-200 bg-gray-50 text-gray-700' : 'border-gray-200 bg-gray-100 text-gray-400 line-through'
          }`}
        >
          <span className="font-mono">{member.server_key}</span>
          <span className="rounded bg-white px-1 text-[11px] tabular-nums text-gray-500">{member.tool_count}</span>
        </span>
      ))}
    </div>
  );
}

export default function MCPBundlesTab() {
  const navigate = useNavigate();
  const { authMode, session } = useAuth();
  const canCreate = isPlatformAdminSession(authMode, session) || hasPermission(authMode, session, 'org.update');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const { data, error, loading, refetch } = useApi(
    (signal) => mcpBundles.list({ search: search || undefined, limit: PAGE_SIZE, offset }, signal),
    [search, offset],
  );
  const bundles = data?.data ?? [];
  const pagination = data?.pagination;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900" role="note">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
        <p>{BUNDLE_INFO_TEXT}</p>
      </div>

      <div className="flex flex-col items-center justify-between gap-3 sm:flex-row">
        <div className="relative w-full sm:w-80">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
          <input
            type="text"
            aria-label="Search bundles"
            placeholder="Search bundles..."
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            className="control-field-md w-full rounded-lg border border-gray-200 bg-white pl-9 pr-4 transition focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-brand-primary/30"
          />
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" aria-label="Refresh list" title="Refresh list" onClick={() => refetch()}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          </Button>
          {canCreate ? (
            <Button onClick={() => navigate(MCP_BUNDLE_NEW_PATH)}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              Create bundle
            </Button>
          ) : null}
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className={`${HEAD} w-72`}>Bundle</th>
                <th className={HEAD}>Servers</th>
                <th className={`${HEAD} w-24`}>Tools</th>
                <th className={HEAD}>Endpoint</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading && !data ? <SkeletonRows /> : null}
              {error && !loading ? (
                <tr>
                  <td colSpan={4} className="px-6 py-10 text-center text-sm text-red-600">
                    {errorMessage(error, 'Could not load the bundles.')}
                  </td>
                </tr>
              ) : null}
              {data && !error && bundles.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-16 text-center text-gray-400">
                    <Boxes className="mx-auto mb-3 h-10 w-10 text-gray-200" aria-hidden="true" />
                    <p className="font-medium text-gray-500">No bundles found</p>
                    <p className="mt-1 text-sm">{search ? 'Try a different search.' : 'Create a bundle to show tools from several servers at one endpoint.'}</p>
                  </td>
                </tr>
              ) : null}
              {!error ? bundles.map((bundle) => {
                const endpoint = bundleEndpointUrl(bundle.bundle_key);
                return (
                  <tr
                    key={bundle.mcp_bundle_id}
                    className="cursor-pointer transition-colors hover:bg-blue-50/30"
                    onClick={() => navigate(mcpBundlePath(bundle.mcp_bundle_id))}
                  >
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2 font-medium text-gray-900">
                        {bundle.name}
                        {!bundle.enabled ? (
                          <span className="rounded-full border border-gray-200 bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-500">Disabled</span>
                        ) : null}
                      </div>
                      <div className="mt-0.5 font-mono text-xs text-gray-400">{bundle.bundle_key}</div>
                      <div className="mt-1 inline-flex items-center rounded-md border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-500">
                        {ownerScopeLabel(bundle.owner_scope_type, bundle.owner_scope_id)}
                      </div>
                    </td>
                    <td className="px-6 py-4"><ServerChips bundle={bundle} /></td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center rounded-md border border-gray-200 bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">
                        {bundle.tool_count ?? '—'}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-1">
                        <code className="truncate rounded bg-gray-50 px-1.5 py-0.5 text-xs text-gray-700">{`/mcp/${bundle.bundle_key}`}</code>
                        <CopyButton value={endpoint} label={`Copy endpoint of ${bundle.bundle_key}`} />
                      </div>
                    </td>
                  </tr>
                );
              }) : null}
            </tbody>
          </table>
        </div>
        {pagination && pagination.total > PAGE_SIZE ? (
          <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50/50 px-6 py-3 text-sm text-gray-500">
            <span>{`${pagination.offset + 1}–${Math.min(pagination.offset + PAGE_SIZE, pagination.total)} of ${pagination.total} bundles`}</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="icon-sm" aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              </Button>
              <Button variant="outline" size="icon-sm" aria-label="Next page" disabled={!pagination.has_more} onClick={() => setOffset(offset + PAGE_SIZE)}>
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
