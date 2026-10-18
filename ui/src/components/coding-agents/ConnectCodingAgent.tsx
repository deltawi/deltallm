import { useEffect, useMemo, useState } from 'react';
import { SquareTerminal } from 'lucide-react';
import Button from '../Button';
import ConfirmDialog from '../ConfirmDialog';
import { IndexShell } from '../admin/shells';
import { Skeleton } from '../usage/ReportBits';
import AgentSetupPanel from './AgentSetupPanel';
import KeySetupDrawer from './KeySetupDrawer';
import { FIELD } from './fieldClass';
import { Field, MoneyInput } from './fields';
import { useKeyFirstUse } from './useKeyFirstUse';
import { useApi } from '../../lib/hooks';
import { useAuth } from '../../lib/auth';
import { keys, teams, type ApiKey } from '../../lib/api';
import { keyLastUsedDisplay } from '../../lib/apiKeyLastUsed';
import {
  CODING_AGENTS,
  codingAgentInfo,
  codingAgentKeyName,
  currentPeriodSpend,
  defaultAgentModels,
  defaultSmallModel,
  expiryFromDays,
  expiryOptions,
  type CodingAgentId,
} from '../../lib/codingAgentSetup';
import { navSectionLabel } from '../../lib/navigation';

const AGENT_HINTS: Record<CodingAgentId, string> = {
  'claude-code': 'Anthropic CLI',
  codex: 'OpenAI CLI',
  opencode: 'Open source CLI',
  other: 'Any OpenAI or Anthropic client',
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-3 border-t border-gray-100 px-5 py-4 first:border-t-0 sm:grid-cols-[140px_minmax(0,1fr)] sm:gap-6">
      <h2 className="text-[13px] font-semibold text-gray-900">{title}</h2>
      <div className="grid gap-4">{children}</div>
    </section>
  );
}

type Created = { token: string; rawKey: string; agent: CodingAgentId; mainModel: string; smallModel: string };

export default function ConnectCodingAgent() {
  const { session } = useAuth();
  const email = session?.email || 'Me';
  const teamList = useApi((signal) => teams.list({ limit: 500 }, signal), []);
  const selfServiceTeams = useMemo(
    () => (teamList.data?.data ?? []).filter((team) => team.self_service_keys_enabled),
    [teamList.data],
  );
  const [agent, setAgent] = useState<CodingAgentId>('claude-code');
  const [teamId, setTeamId] = useState('');
  const [mainModel, setMainModel] = useState('');
  const [budget, setBudget] = useState('');
  const [expiryDays, setExpiryDays] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const [setupAgent, setSetupAgent] = useState<CodingAgentId>('claude-code');
  const [setupKey, setSetupKey] = useState<ApiKey | null>(null);
  const [revokeKey, setRevokeKey] = useState<ApiKey | null>(null);
  const [revoking, setRevoking] = useState(false);
  const lastUsedAt = useKeyFirstUse(created?.token ?? null);

  const team = selfServiceTeams.find((item) => item.team_id === teamId);
  const ceiling = team?.self_service_budget_ceiling ?? null;
  const expiryChoices = useMemo(
    () => expiryOptions({ maxDays: team?.self_service_max_expiry_days ?? null, required: Boolean(team?.self_service_require_expiry) }),
    [team],
  );

  useEffect(() => {
    if (!teamId && selfServiceTeams.length > 0) setTeamId(selfServiceTeams[0].team_id);
  }, [selfServiceTeams, teamId]);

  useEffect(() => {
    // New team: the defaults follow its limits.
    setBudget(ceiling !== null ? String(Math.min(20, ceiling)) : '20');
    const preferred = expiryChoices.find((option) => option.days === 90) ?? expiryChoices[expiryChoices.length - 1];
    setExpiryDays(preferred?.days ?? null);
  }, [teamId, ceiling, expiryChoices]);

  const visibility = useApi(() => (teamId ? teams.assetVisibility(teamId) : Promise.resolve(null)), [teamId]);
  const models = useMemo(
    () => (visibility.data?.callable_targets.items ?? [])
      .filter((item) => item.effective_visible !== false)
      .map((item) => item.callable_key)
      .sort(),
    [visibility.data],
  );
  const smallModel = useMemo(() => defaultSmallModel(agent, models, mainModel), [agent, models, mainModel]);
  useEffect(() => setMainModel(defaultAgentModels(agent, models).main), [agent, models]);

  const myKeys = useApi((signal) => keys.list({ coding_agent: true, my_keys: true, limit: 100 }, signal), []);
  const now = Date.now();

  const create = async () => {
    const amount = Number(budget);
    if (!teamId) return setError('Select a team.');
    if (!budget.trim() || !Number.isFinite(amount) || amount <= 0) return setError('Enter a daily budget greater than $0.');
    if (ceiling !== null && amount > ceiling) return setError(`The daily budget cannot be more than the team maximum of $${ceiling}.`);
    setError(null);
    setSaving(true);
    try {
      const small = agent === 'claude-code' || agent === 'opencode' ? smallModel : '';
      const result = await keys.create({
        key_name: codingAgentKeyName(email, agent),
        team_id: teamId,
        max_budget: amount,
        budget_duration: '1d',
        expires: expiryFromDays(expiryDays),
        coding_agent: { agent, main_model: mainModel || null, small_model: small || null },
      });
      setCreated({ token: result.token, rawKey: result.raw_key, agent, mainModel, smallModel: small });
      setSetupAgent(agent);
      myKeys.refetch();
    } catch (err: unknown) {
      setError(err instanceof Error && err.message ? err.message : 'Could not create the key.');
    } finally {
      setSaving(false);
    }
  };

  const revoke = async () => {
    if (!revokeKey) return;
    setRevoking(true);
    try {
      await keys.revoke(revokeKey.token);
      myKeys.refetch();
    } catch (err: unknown) {
      setError(err instanceof Error && err.message ? err.message : 'Could not revoke the key.');
    } finally {
      setRevoking(false);
      setRevokeKey(null);
    }
  };

  const expiryLabel = expiryChoices.find((option) => option.days === expiryDays)?.label ?? '';
  const agentKeys = myKeys.data?.data ?? [];

  let body: React.ReactNode;
  if (teamList.loading && !teamList.data) {
    body = <div className="grid gap-3 rounded-xl border border-gray-200 bg-white p-5">{Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-9 w-full" />)}</div>;
  } else if (selfServiceTeams.length === 0) {
    body = (
      <div className="rounded-xl border border-gray-200 bg-white px-5 py-8 text-center">
        <h2 className="text-sm font-semibold text-gray-900">Personal keys are not turned on for your teams</h2>
        <p className="mx-auto mt-1 max-w-md text-[13px] leading-5 text-gray-500">Ask a team admin to turn on self-service keys, or to set up a coding agent for you.</p>
      </div>
    );
  } else if (created) {
    body = (
      <div className="grid gap-4 rounded-xl border border-gray-200 bg-white p-5">
        <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-[13px] leading-5 text-emerald-900">
          <strong className="font-semibold">Key created.</strong> Copy the setup now. DeltaLLM shows the key only once.
        </p>
        <AgentSetupPanel
          agent={setupAgent}
          onAgentChange={setSetupAgent}
          rawKey={created.rawKey}
          mainModel={created.mainModel}
          smallModel={created.smallModel}
          lastUsedAt={lastUsedAt}
        />
        <div className="flex justify-end border-t border-gray-100 pt-4">
          <Button onClick={() => setCreated(null)}>Done</Button>
        </div>
      </div>
    );
  } else {
    body = (
      <form
        className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm"
        onSubmit={(event) => { event.preventDefault(); void create(); }}
      >
        <Section title="Agent">
          <div role="radiogroup" aria-label="Agent" className="grid gap-2 sm:grid-cols-2">
            {CODING_AGENTS.map((item) => (
              <label
                key={item.id}
                className={`flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 ${
                  agent === item.id ? 'border-brand-primary bg-brand-primary-soft' : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <input type="radio" name="agent" value={item.id} checked={agent === item.id} onChange={() => setAgent(item.id)} className="mt-0.5 accent-brand-primary" />
                <span>
                  <span className="block text-[13px] font-medium text-gray-900">{item.label}</span>
                  <span className="block text-xs text-gray-500">{AGENT_HINTS[item.id]}</span>
                </span>
              </label>
            ))}
          </div>
        </Section>
        <Section title="Access">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Team" htmlFor="connect-team" hint={team?.self_service_max_keys_per_user ? `Up to ${team.self_service_max_keys_per_user} personal keys in this team.` : undefined}>
              <select id="connect-team" value={teamId} onChange={(event) => setTeamId(event.target.value)} className={FIELD}>
                {selfServiceTeams.map((item) => <option key={item.team_id} value={item.team_id}>{item.team_alias || item.team_id}</option>)}
              </select>
            </Field>
            <Field label="Main model" htmlFor="connect-model" hint={models.length === 0 && !visibility.loading ? 'This team has no models yet.' : 'You can change the model in the agent later.'}>
              <select id="connect-model" value={mainModel} onChange={(event) => setMainModel(event.target.value)} className={FIELD} disabled={visibility.loading}>
                <option value="">{visibility.loading ? 'Loading models…' : 'Agent default'}</option>
                {models.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </Field>
          </div>
        </Section>
        <Section title="Limits">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Daily budget" htmlFor="connect-budget" hint={ceiling !== null ? `Team maximum $${ceiling} per day. Resets at 00:00 UTC.` : 'Resets every day at 00:00 UTC.'}>
              <MoneyInput id="connect-budget" value={budget} onChange={setBudget} suffix="per day" />
            </Field>
            <Field
              label="Expires"
              htmlFor="connect-expiry"
              hint={team?.self_service_max_expiry_days ? `Team maximum ${team.self_service_max_expiry_days} days.` : undefined}
            >
              <select
                id="connect-expiry"
                value={expiryDays === null ? 'never' : String(expiryDays)}
                onChange={(event) => setExpiryDays(event.target.value === 'never' ? null : Number(event.target.value))}
                className={FIELD}
              >
                {expiryChoices.map((option) => <option key={option.label} value={option.days === null ? 'never' : option.days}>{option.label}</option>)}
              </select>
            </Field>
          </div>
        </Section>
        {error ? <p role="alert" className="mx-5 mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 bg-gray-50 px-5 py-3">
          <div className="text-xs leading-5 text-gray-500">
            <div className="font-medium text-gray-900">{codingAgentInfo(agent).label} · {team?.team_alias || teamId}</div>
            {budget ? `$${budget} per day` : 'No budget'} · {expiryDays === null ? 'no expiry' : `expires in ${expiryLabel}`}
          </div>
          <Button type="submit" loading={saving}>Create key</Button>
        </div>
      </form>
    );
  }

  return (
    <IndexShell
      eyebrowPrefix={navSectionLabel('/coding-agents')}
      title="Connect a coding agent"
      titleIcon={SquareTerminal}
      description="Create a personal key with a daily budget, then paste the setup into your agent."
    >
      <div className="grid max-w-4xl gap-6">
        {body}
        <section aria-labelledby="my-agent-keys" className="grid gap-2">
          <h2 id="my-agent-keys" className="text-[13px] font-semibold text-gray-900">Your agent keys</h2>
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
            {myKeys.loading && !myKeys.data ? (
              <div className="grid gap-2 p-4">{Array.from({ length: 2 }, (_, index) => <Skeleton key={index} className="h-5 w-full" />)}</div>
            ) : agentKeys.length === 0 ? (
              <p className="px-4 py-6 text-center text-[13px] text-gray-500">You have no agent keys yet.</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {agentKeys.map((row) => {
                  const used = keyLastUsedDisplay(row.last_used_at, now);
                  const spend = currentPeriodSpend(row, now);
                  return (
                    <li key={row.token} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-[13px]">
                      <span className="inline-flex min-w-[120px] items-center gap-1.5 font-medium text-gray-900">
                        <i aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: codingAgentInfo(row.coding_agent?.agent).color }} />
                        {codingAgentInfo(row.coding_agent?.agent).label}
                      </span>
                      <span className="text-gray-600">{row.team_alias || row.team_id}</span>
                      <span className="tabular-nums text-gray-700">Today ${spend.toFixed(2)}{row.max_budget ? ` / $${row.max_budget}` : ''}</span>
                      <span className="text-gray-500">{row.expires ? `Expires ${new Date(row.expires).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}` : 'No expiry'}</span>
                      <span className="text-gray-500" title={used.title || undefined}>Last used {used.label.toLowerCase()}</span>
                      <span className="ml-auto inline-flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setSetupKey(row)}>Show setup</Button>
                        <Button variant="ghost" size="sm" className="text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => setRevokeKey(row)}>Revoke</Button>
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>
      </div>
      {setupKey ? <KeySetupDrawer apiKey={setupKey} onClose={() => setSetupKey(null)} /> : null}
      <ConfirmDialog
        open={Boolean(revokeKey)}
        title="Revoke this key?"
        description="The agent that uses this key stops working at once. You cannot undo this."
        confirmLabel="Revoke key"
        destructive
        confirming={revoking}
        onConfirm={() => void revoke()}
        onClose={() => setRevokeKey(null)}
      />
    </IndexShell>
  );
}
