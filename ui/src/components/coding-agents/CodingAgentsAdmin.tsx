import { useEffect, useMemo, useState } from 'react';
import { Plus, Search, SquareTerminal } from 'lucide-react';
import Button from '../Button';
import ConfirmDialog from '../ConfirmDialog';
import KeyRevocationNotice from '../KeyRevocationNotice';
import { ContentCard, IndexShell } from '../admin/shells';
import ChipMenu, { MenuItem, MenuNote } from '../audit/ChipMenu';
import { Skeleton } from '../usage/ReportBits';
import DeveloperSetupDrawer from './DeveloperSetupDrawer';
import KeySetupDrawer from './KeySetupDrawer';
import { useApi } from '../../lib/hooks';
import { keys, teams, type ApiKey, type TeamRecord } from '../../lib/api';
import type { KeyRemovalResult } from '../../lib/api/keyRevocations';
import { keyLastUsedDisplay } from '../../lib/apiKeyLastUsed';
import { CODING_AGENTS, codingAgentInfo, currentPeriodSpend, type CodingAgentId } from '../../lib/codingAgentSetup';
import { navSectionLabel } from '../../lib/navigation';

const HEAD = 'whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-500';
const CELL = 'overflow-hidden text-ellipsis whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] align-middle';
const SEARCH_INPUT =
  'control-field-md w-full sm:w-[260px] max-w-full text-ellipsis rounded-lg border border-gray-300 bg-white pl-8 pr-2.5 text-xs text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary';
const WEEK_MS = 7 * 86_400_000;

function money(value: number): string {
  return `$${value.toFixed(2)}`;
}

function developerLabel(row: ApiKey): string {
  return row.owner_account_email || row.owner_service_account_name || row.key_name || row.owner_account_id || '—';
}

function expiryLabel(expires: string | null, now: number): { label: string; tone: string } {
  if (!expires) return { label: 'Never', tone: 'text-gray-500' };
  const at = Date.parse(expires);
  if (!Number.isFinite(at)) return { label: '—', tone: 'text-gray-400' };
  const date = new Date(at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  if (at <= now) return { label: 'Expired', tone: 'text-rose-700' };
  if (at - now < WEEK_MS) return { label: date, tone: 'text-amber-700' };
  return { label: date, tone: 'text-gray-700' };
}

function AgentName({ agent }: { agent?: string | null }) {
  const info = codingAgentInfo(agent);
  return (
    <span className="inline-flex items-center gap-1.5">
      <i aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full" style={{ background: info.color }} />
      {info.label}
    </span>
  );
}

function TodayBar({ row, now }: { row: ApiKey; now: number }) {
  const spend = currentPeriodSpend(row, now);
  if (!row.max_budget) return <span className="text-gray-700">{money(spend)} <span className="text-gray-400">/ no limit</span></span>;
  const pct = Math.min(100, (spend / row.max_budget) * 100);
  const color = pct >= 100 ? 'bg-rose-500' : pct >= 80 ? 'bg-amber-500' : 'bg-brand-primary';
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-14 shrink-0 overflow-hidden rounded-full bg-gray-100" aria-hidden="true">
        <span className={`block h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="text-gray-900">{money(spend)}</span>
      <span className="text-gray-400">/ ${row.max_budget}</span>
    </span>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-primary-soft text-brand-primary-ink">
        <SquareTerminal className="h-5 w-5" aria-hidden="true" />
      </span>
      <h2 className="mt-3 text-sm font-semibold text-gray-900">No coding agents yet</h2>
      <p className="mt-1 max-w-sm text-[13px] leading-5 text-gray-500">
        Set up a developer to give them a key with a daily budget and a copy-paste setup for Claude Code, Codex or OpenCode.
      </p>
      <Button className="mt-4" onClick={onCreate}><Plus className="h-4 w-4" aria-hidden="true" /> Set up a developer</Button>
    </div>
  );
}

export default function CodingAgentsAdmin() {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [agentFilter, setAgentFilter] = useState<CodingAgentId | null>(null);
  const [teamFilter, setTeamFilter] = useState<TeamRecord | null>(null);
  const [creating, setCreating] = useState(false);
  const [setupKey, setSetupKey] = useState<ApiKey | null>(null);
  const [revokeKey, setRevokeKey] = useState<ApiKey | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [revocation, setRevocation] = useState<KeyRemovalResult | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const list = useApi(
    (signal) => keys.list({ coding_agent: true, search: search || undefined, team_id: teamFilter?.team_id, limit: 200 }, signal),
    [search, teamFilter?.team_id],
  );
  const teamList = useApi((signal) => teams.list({ limit: 500 }, signal), []);
  const teamOptions = useMemo(() => teamList.data?.data ?? [], [teamList.data]);

  useEffect(() => setNow(Date.now()), [list.data]);

  const rows = useMemo(
    () => (list.data?.data ?? []).filter((row) => !agentFilter || codingAgentInfo(row.coding_agent?.agent).id === agentFilter),
    [list.data, agentFilter],
  );
  const totals = useMemo(() => {
    const active = rows.filter((row) => !row.expires || Date.parse(row.expires) > now);
    return {
      spend: active.reduce((sum, row) => sum + currentPeriodSpend(row, now), 0),
      budget: active.reduce((sum, row) => sum + (row.max_budget || 0), 0),
      expiring: active.filter((row) => row.expires && Date.parse(row.expires) - now < WEEK_MS).length,
    };
  }, [rows, now]);
  const total = list.data?.pagination.total ?? null;
  const filtered = Boolean(search || agentFilter || teamFilter);

  const revoke = async () => {
    if (!revokeKey) return;
    setRevoking(true);
    try {
      const result = await keys.revoke(revokeKey.token);
      setRevocation(result);
      setRevokeKey(null);
      list.refetch();
    } catch (error: unknown) {
      setPageError(error instanceof Error ? error.message : 'Could not revoke the key.');
      setRevokeKey(null);
    } finally {
      setRevoking(false);
    }
  };

  const toolbar = (
    <div className="flex flex-wrap items-center gap-2">
      <label className="relative w-full sm:w-auto">
        <span className="sr-only">Search developers</span>
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
        <input type="search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="Search by key name" className={SEARCH_INPUT} />
      </label>
      <ChipMenu label="Agent" value={agentFilter ? codingAgentInfo(agentFilter).label : undefined} onClear={() => setAgentFilter(null)} width="w-48">
        {(close) => CODING_AGENTS.map((agent) => (
          <MenuItem key={agent.id} swatch={agent.color} onClick={() => { setAgentFilter(agent.id); close(); }}>{agent.label}</MenuItem>
        ))}
      </ChipMenu>
      <ChipMenu label="Team" value={teamFilter ? teamFilter.team_alias || teamFilter.team_id : undefined} onClear={() => setTeamFilter(null)}>
        {(close) => teamOptions.length ? teamOptions.map((team) => (
          <MenuItem key={team.team_id} onClick={() => { setTeamFilter(team); close(); }}>{team.team_alias || team.team_id}</MenuItem>
        )) : <MenuNote>No teams.</MenuNote>}
      </ChipMenu>
      {rows.length > 0 ? (
        <span className="ml-auto text-xs text-gray-500">
          Today <b className="font-semibold text-gray-900">{money(totals.spend)}</b> of {money(totals.budget)}
          {totals.expiring > 0 ? <> · <span className="text-amber-700">{totals.expiring} {totals.expiring === 1 ? 'key expires' : 'keys expire'} this week</span></> : null}
        </span>
      ) : null}
    </div>
  );

  return (
    <IndexShell
      eyebrowPrefix={navSectionLabel('/coding-agents')}
      title="Coding agents"
      titleIcon={SquareTerminal}
      count={total}
      description="One key per developer, with a daily budget. Budgets reset at 00:00 UTC."
      action={<Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" aria-hidden="true" /> Set up a developer</Button>}
      toolbar={toolbar}
      notice={pageError ? <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{pageError}</div> : null}
    >
      {revocation ? <div className="mb-4"><KeyRevocationNotice key={revocation.invalidation_id} result={revocation} /></div> : null}
      <ContentCard>
        {list.loading && !list.data ? (
          <div className="grid gap-3 p-4" aria-label="Loading coding agents">
            {Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-5 w-full" />)}
          </div>
        ) : list.error ? (
          <p role="alert" className="px-4 py-6 text-sm text-red-700">{list.error instanceof Error ? list.error.message : 'Could not load coding agents.'}</p>
        ) : rows.length === 0 ? (
          filtered
            ? <p className="px-4 py-10 text-center text-sm text-gray-500">No coding agent matches these filters.</p>
            : <EmptyState onCreate={() => setCreating(true)} />
        ) : (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full table-fixed border-collapse text-[13px] tabular-nums">
                <thead>
                  <tr>
                    <th className={`${HEAD} w-[19%]`}>Developer</th>
                    <th className={`${HEAD} w-[11%]`}>Agent</th>
                    <th className={`${HEAD} w-[10%]`}>Team</th>
                    <th className={`${HEAD} w-[16%]`}>Today</th>
                    <th className={`${HEAD} hidden xl:table-cell`}>Model</th>
                    <th className={`${HEAD} w-[8%]`}>Expires</th>
                    <th className={`${HEAD} w-[9%]`}>Last used</th>
                    <th className={`${HEAD} w-[180px]`}><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const expiry = expiryLabel(row.expires, now);
                    const used = keyLastUsedDisplay(row.last_used_at, now);
                    return (
                      <tr key={row.token} className="hover:bg-gray-50">
                        <td className={`${CELL} font-medium text-gray-900`} title={row.key_name || undefined}>{developerLabel(row)}</td>
                        <td className={`${CELL} text-gray-700`}><AgentName agent={row.coding_agent?.agent} /></td>
                        <td className={`${CELL} text-gray-700`}>{row.team_alias || row.team_id}</td>
                        <td className={CELL}><TodayBar row={row} now={now} /></td>
                        <td className={`${CELL} hidden font-mono text-xs text-gray-600 xl:table-cell`} title={row.coding_agent?.main_model || undefined}>{row.coding_agent?.main_model || <span className="font-sans text-gray-400">Agent default</span>}</td>
                        <td className={`${CELL} ${expiry.tone}`}>{expiry.label}</td>
                        <td className={`${CELL} ${used.title ? 'text-gray-700' : 'text-gray-400'}`} title={used.title || undefined}>{used.label}</td>
                        <td className={`${CELL} text-right`}>
                          <span className="inline-flex gap-1">
                            <Button variant="ghost" size="sm" onClick={() => setSetupKey(row)}>Show setup</Button>
                            <Button variant="ghost" size="sm" className="text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => setRevokeKey(row)}>Revoke</Button>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <ul className="divide-y divide-gray-100 md:hidden" aria-label="Coding agents">
              {rows.map((row) => {
                const expiry = expiryLabel(row.expires, now);
                return (
                  <li key={row.token} className="grid gap-1.5 px-4 py-3 text-[13px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-gray-900">{developerLabel(row)}</span>
                      <span className="shrink-0 text-xs text-gray-600"><AgentName agent={row.coding_agent?.agent} /></span>
                    </div>
                    <TodayBar row={row} now={now} />
                    <div className="flex items-center justify-between gap-2 text-xs text-gray-500">
                      <span>{row.team_alias || row.team_id} · Expires <span className={expiry.tone}>{expiry.label}</span></span>
                      <span className="inline-flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setSetupKey(row)}>Setup</Button>
                        <Button variant="ghost" size="sm" className="text-red-600" onClick={() => setRevokeKey(row)}>Revoke</Button>
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </ContentCard>
      {rows.length > 0 ? (
        <p className="mt-3 text-xs text-gray-500">
          The daily budget stops the key for the rest of the day. The team and organization budgets still apply.
        </p>
      ) : null}

      {creating ? (
        <DeveloperSetupDrawer
          teamOptions={teamOptions}
          onClose={() => setCreating(false)}
          onCreated={() => list.refetch()}
        />
      ) : null}
      {setupKey ? <KeySetupDrawer apiKey={setupKey} onClose={() => setSetupKey(null)} /> : null}
      <ConfirmDialog
        open={Boolean(revokeKey)}
        title="Revoke this key?"
        description={`The agent of ${revokeKey ? developerLabel(revokeKey) : ''} stops working at once. You cannot undo this.`}
        confirmLabel="Revoke key"
        destructive
        confirming={revoking}
        onConfirm={() => void revoke()}
        onClose={() => setRevokeKey(null)}
      />
    </IndexShell>
  );
}
