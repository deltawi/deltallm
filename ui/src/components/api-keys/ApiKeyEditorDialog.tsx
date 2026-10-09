import { useId, useState, type ComponentProps } from 'react';
import { Gauge, KeyRound, ShieldCheck, UserRound } from 'lucide-react';
import Modal from '../Modal';
import Button from '../Button';
import { IconTabs } from '../admin/shells';
import AssetAccessEditor from '../access/AssetAccessEditor';
import ApiKeyDetailsFields, { type ApiKeyDetailsProps } from './ApiKeyDetailsFields';
import ApiKeyLimitsFields from './ApiKeyLimitsFields';
import { validateKeyForm, type KeyFormTab } from '../../lib/apiKeyForm';

interface Props extends ApiKeyDetailsProps {
  title: string;
  error: string | null;
  saving: boolean;
  saveDisabled: boolean;
  assetAccess: ComponentProps<typeof AssetAccessEditor>;
  onClose: () => void;
  onSave: () => void;
}

export default function ApiKeyEditorDialog(props: Props) {
  const { form, selfService, editing, policy, saving, creatingServiceAccount, error } = props;
  const [tab, setTab] = useState<KeyFormTab>('details');
  const id = useId();
  const busy = saving || creatingServiceAccount;
  const teamName = props.teams.find((team) => team.team_id === form.team_id)?.team_alias || form.team_id;
  const items = [{ id: 'details' as const, label: 'Details', icon: UserRound }, { id: 'access' as const, label: 'Access', icon: ShieldCheck }, { id: 'limits' as const, label: 'Limits', icon: Gauge }];
  const close = () => { if (!busy) props.onClose(); };
  return <Modal open title={props.title} focused icon={<KeyRound className="h-5 w-5" />} description="Choose an owner, access, and limits." onClose={close} navigation={<IconTabs id={id} label="API key settings" items={items} active={tab} onChange={setTab} />} footer={
    <div className="space-y-3">
      {error && <div id={`${id}-error`} role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="min-w-0 text-xs leading-5 text-gray-500"><span className="block max-w-56 truncate font-medium text-gray-700">{teamName || 'Choose a team'}</span>{form.asset_access_mode === 'restrict' && !selfService ? 'Restricted access' : 'Team access'} · {form.max_budget ? `$${form.max_budget} budget` : 'No added key budget'}</p>
        <div className="flex gap-2">
          <Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button>
          <Button loading={saving} disabled={props.saveDisabled || busy} onClick={() => {
            const issue = validateKeyForm(form, selfService, policy);
            if (issue) setTab(issue.tab);
            props.onSave();
          }}>{saving ? 'Saving…' : editing ? 'Save changes' : 'Create key'}</Button>
        </div>
      </div>
    </div>
  }>
    {items.map((item) => <div key={item.id} role="tabpanel" id={`${id}-panel-${item.id}`} aria-labelledby={`${id}-tab-${item.id}`} hidden={tab !== item.id}>
      {tab === item.id && (
      <fieldset disabled={busy} aria-describedby={error ? `${id}-error` : undefined} className="min-w-0">
        {tab === 'details' && <ApiKeyDetailsFields {...props} />}
        {tab === 'access' && (selfService ? <div className="rounded-lg border border-brand-primary/20 bg-brand-primary-soft p-4 text-sm leading-6 text-brand-primary-ink">This personal key inherits team access. The team and organization set the available models and model groups.</div> : <>
          <p className="mb-4 text-xs leading-5 text-gray-500">Inherit team access or choose a smaller set of targets and access groups.</p>
          <AssetAccessEditor {...props.assetAccess} />
        </>)}
        {tab === 'limits' && <ApiKeyLimitsFields form={form} onChange={props.onChange} selfService={selfService} editing={editing} policy={policy} />}
      </fieldset>
      )}
    </div>)}
  </Modal>;
}
