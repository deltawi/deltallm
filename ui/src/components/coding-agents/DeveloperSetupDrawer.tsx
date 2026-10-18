import { useEffect, useMemo, useState } from 'react';
import { Check } from 'lucide-react';
import Button from '../Button';
import SideDrawer from '../SideDrawer';
import AgentSetupPanel from './AgentSetupPanel';
import { FIELD } from './fieldClass';
import { Field, MoneyInput } from './fields';
import { useKeyFirstUse } from './useKeyFirstUse';
import { useApi } from '../../lib/hooks';
import { keys, teams, type TeamRecord } from '../../lib/api';
import {
  CODING_AGENTS,
  codingAgentInfo,
  codingAgentKeyName,
  defaultAgentModels,
  expiryFromDays,
  expiryOptions,
  type CodingAgentId,
} from '../../lib/codingAgentSetup';

const STEPS = ['Developer', 'Limits', 'Connect'] as const;

type Created = { token: string; rawKey: string };

function Steps({ current }: { current: number }) {
  return (
    <ol className="grid grid-cols-3 gap-2" aria-label="Steps">
      {STEPS.map((label, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <li key={label} aria-current={active ? 'step' : undefined} className={`flex items-center gap-2 text-xs ${active ? 'font-semibold text-brand-primary-ink' : 'text-gray-600'}`}>
            <span
              className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] ${
                done ? 'bg-brand-primary text-white' : active ? 'border-2 border-brand-primary' : 'border border-gray-300 text-gray-500'
              }`}
            >
              {done ? <Check className="h-3 w-3" aria-hidden="true" /> : index + 1}
            </span>
            {label}
          </li>
        );
      })}
    </ol>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}

export default function DeveloperSetupDrawer({
  teamOptions,
  onClose,
  onCreated,
}: {
  teamOptions: TeamRecord[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [step, setStep] = useState(0);
  const [teamId, setTeamId] = useState(teamOptions.length === 1 ? teamOptions[0].team_id : '');
  const [developerId, setDeveloperId] = useState('');
  const [agent, setAgent] = useState<CodingAgentId>('claude-code');
  const [budget, setBudget] = useState('20');
  const [expiryDays, setExpiryDays] = useState<number | null>(90);
  const [mainModel, setMainModel] = useState('');
  const [smallModel, setSmallModel] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const lastUsedAt = useKeyFirstUse(created?.token ?? null);

  const members = useApi(() => (teamId ? teams.members(teamId) : Promise.resolve([])), [teamId]);
  const visibility = useApi(() => (teamId ? teams.assetVisibility(teamId) : Promise.resolve(null)), [teamId]);
  const models = useMemo(
    () => (visibility.data?.callable_targets.items ?? [])
      .filter((item) => item.effective_visible !== false)
      .map((item) => item.callable_key)
      .sort(),
    [visibility.data],
  );
  const developer = (members.data ?? []).find((member) => member.user_id === developerId);
  const developerLabel = developer?.user_email || developerId;
  const team = teamOptions.find((item) => item.team_id === teamId);
  const showsSmallModel = agent === 'claude-code' || agent === 'opencode';

  useEffect(() => {
    const defaults = defaultAgentModels(agent, models);
    setMainModel(defaults.main);
    setSmallModel(defaults.small);
  }, [agent, models]);

  const validateDeveloper = (): string | null => {
    if (!teamId) return 'Select a team.';
    if (!developerId) return 'Select a developer.';
    return null;
  };

  const next = () => {
    const issue = validateDeveloper();
    setError(issue);
    if (!issue) setStep(1);
  };

  const create = async () => {
    const amount = Number(budget);
    if (!budget.trim() || !Number.isFinite(amount) || amount <= 0) {
      setError('Enter a daily budget greater than $0.');
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const result = await keys.create({
        key_name: codingAgentKeyName(developerLabel, agent),
        team_id: teamId,
        owner_account_id: developerId,
        max_budget: amount,
        budget_duration: '1d',
        expires: expiryFromDays(expiryDays),
        coding_agent: {
          agent,
          main_model: mainModel || null,
          small_model: showsSmallModel ? smallModel || null : null,
        },
      });
      setCreated({ token: result.token, rawKey: result.raw_key });
      setStep(2);
      onCreated();
    } catch (err: unknown) {
      setError(errorMessage(err, 'Could not create the key.'));
    } finally {
      setSaving(false);
    }
  };

  const description = step === 0
    ? 'Give one developer a key with a daily budget.'
    : `${developerLabel} · ${team?.team_alias || teamId}`;

  const footer = step === 0 ? (
    <>
      <Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button onClick={next}>Next</Button>
    </>
  ) : step === 1 ? (
    <>
      <Button variant="ghost" onClick={() => { setError(null); setStep(0); }} disabled={saving}>Back</Button>
      <Button onClick={() => void create()} loading={saving}>Create key</Button>
    </>
  ) : (
    <>
      <span className="mr-auto text-xs text-gray-500">You can open this setup again from the list, without the key.</span>
      <Button onClick={onClose}>Done</Button>
    </>
  );

  return (
    <SideDrawer title="Set up a developer" description={description} saving={saving} onClose={onClose} footer={footer} size="md" overlayLabel="Close setup">
      <div className="grid gap-5">
        <Steps current={step} />
        {error ? <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}

        {step === 0 ? (
          <div className="grid gap-4 rounded-xl border border-gray-200 bg-white p-4">
            <Field label="Team" htmlFor="setup-team">
              <select
                id="setup-team"
                value={teamId}
                onChange={(event) => { setTeamId(event.target.value); setDeveloperId(''); }}
                className={FIELD}
              >
                <option value="">Select a team</option>
                {teamOptions.map((item) => <option key={item.team_id} value={item.team_id}>{item.team_alias || item.team_id}</option>)}
              </select>
            </Field>
            <Field
              label="Developer"
              htmlFor="setup-developer"
              hint={teamId && members.data?.length === 0 ? 'This team has no members. Add the developer to the team first.' : 'The key belongs to this person. They see it under My keys.'}
            >
              <select
                id="setup-developer"
                value={developerId}
                disabled={!teamId || members.loading}
                onChange={(event) => setDeveloperId(event.target.value)}
                className={FIELD}
              >
                <option value="">{members.loading ? 'Loading members…' : 'Select a developer'}</option>
                {(members.data ?? []).map((member) => (
                  <option key={member.user_id} value={member.user_id}>{member.user_email || member.user_id}</option>
                ))}
              </select>
            </Field>
            <fieldset className="grid gap-1.5">
              <legend className="mb-1.5 text-xs font-medium text-gray-700">Agent</legend>
              <div className="grid grid-cols-2 gap-2">
                {CODING_AGENTS.map((item) => (
                  <label
                    key={item.id}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-[13px] ${
                      agent === item.id ? 'border-brand-primary bg-brand-primary-soft font-medium text-gray-900' : 'border-gray-200 text-gray-700 hover:border-gray-300'
                    }`}
                  >
                    <input type="radio" name="setup-agent" value={item.id} checked={agent === item.id} onChange={() => setAgent(item.id)} className="accent-brand-primary" />
                    {item.label}
                  </label>
                ))}
              </div>
              <p className="text-xs leading-5 text-gray-500">Sets the default models and the setup text. The key works with any agent.</p>
            </fieldset>
          </div>
        ) : null}

        {step === 1 ? (
          <div className="grid gap-4 rounded-xl border border-gray-200 bg-white p-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Daily budget" htmlFor="setup-budget" hint="Resets every day at 00:00 UTC.">
                <MoneyInput id="setup-budget" value={budget} onChange={setBudget} suffix="per day" />
              </Field>
              <Field label="Expires" htmlFor="setup-expiry">
                <select
                  id="setup-expiry"
                  value={expiryDays === null ? 'never' : String(expiryDays)}
                  onChange={(event) => setExpiryDays(event.target.value === 'never' ? null : Number(event.target.value))}
                  className={FIELD}
                >
                  {expiryOptions().map((option) => (
                    <option key={option.label} value={option.days === null ? 'never' : option.days}>{option.label}</option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Main model" htmlFor="setup-main-model" hint={models.length === 0 && !visibility.loading ? 'This team has no models yet.' : undefined}>
                <select id="setup-main-model" value={mainModel} onChange={(event) => setMainModel(event.target.value)} className={FIELD} disabled={visibility.loading}>
                  <option value="">{visibility.loading ? 'Loading models…' : 'Agent default'}</option>
                  {models.map((model) => <option key={model} value={model}>{model}</option>)}
                </select>
              </Field>
              {showsSmallModel ? (
                <Field label="Small model" htmlFor="setup-small-model" hint="For quick background tasks.">
                  <select id="setup-small-model" value={smallModel} onChange={(event) => setSmallModel(event.target.value)} className={FIELD} disabled={visibility.loading}>
                    <option value="">Agent default</option>
                    {models.map((model) => <option key={model} value={model}>{model}</option>)}
                  </select>
                </Field>
              ) : null}
            </div>
            <p className="text-xs leading-5 text-gray-500">
              The key can use every model of the {team?.team_alias || 'selected'} team. The models above only set the defaults in the setup text.
            </p>
          </div>
        ) : null}

        {step === 2 && created ? (
          <div className="grid gap-4">
            <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-[13px] leading-5 text-emerald-900">
              <strong className="font-semibold">Key created.</strong> Copy the setup now and send it to {developerLabel}. DeltaLLM shows the key only once.
            </p>
            <div className="flex flex-wrap gap-1.5 text-xs text-gray-700">
              <span className="rounded-full bg-gray-100 px-2.5 py-0.5">${Number(budget)} / day</span>
              <span className="rounded-full bg-gray-100 px-2.5 py-0.5">{expiryOptions().find((option) => option.days === expiryDays)?.label === 'Never' ? 'No expiry' : `Expires in ${expiryOptions().find((option) => option.days === expiryDays)?.label}`}</span>
              <span className="rounded-full bg-gray-100 px-2.5 py-0.5">{codingAgentInfo(agent).label}</span>
            </div>
            <AgentSetupPanel
              agent={agent}
              onAgentChange={setAgent}
              rawKey={created.rawKey}
              mainModel={mainModel}
              smallModel={showsSmallModel ? smallModel : ''}
              lastUsedAt={lastUsedAt}
            />
          </div>
        ) : null}
      </div>
    </SideDrawer>
  );
}
