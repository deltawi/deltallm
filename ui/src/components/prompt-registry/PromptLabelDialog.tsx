import { useState } from 'react';
import { Tag } from 'lucide-react';
import type { PromptBinding, PromptLabel, PromptVersion } from '../../lib/api';
import { bindingsForLabel, labelNameError, PRODUCTION_LABEL, versionVariables } from '../../lib/promptVersions';
import Button from '../Button';
import Modal from '../Modal';
import PromptBodyView from './PromptBodyView';
import { bindingTargetText, type BindingTargetOptions } from './promptBindingTargets';

const NEW_LABEL = '__new__';

export interface PromptLabelDialogStart {
  /** Existing label to move, or null to add a new label. */
  label: string | null;
  version: number;
  /** Open on the confirm step, for example from "Promote to production". */
  confirm?: boolean;
}

export interface PromptLabelMove {
  label: string;
  version: number;
  approvedBy: string;
}

interface PromptLabelDialogProps {
  start: PromptLabelDialogStart;
  labels: PromptLabel[];
  versions: PromptVersion[];
  bindings: PromptBinding[];
  showUsage: boolean;
  targetOptions: BindingTargetOptions;
  saving: boolean;
  onClose: () => void;
  onConfirm: (move: PromptLabelMove) => void;
  onRemove: (label: string) => void;
}

function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function isProductionName(label: string): boolean {
  return ['production', 'prod'].includes(label.trim().toLowerCase());
}

export default function PromptLabelDialog({ start, labels, versions, bindings, showUsage, targetOptions, saving, onClose, onConfirm, onRemove }: PromptLabelDialogProps) {
  const [choice, setChoice] = useState(start.label ?? NEW_LABEL);
  const [newName, setNewName] = useState(start.label === null && !labels.some((item) => item.label === PRODUCTION_LABEL) ? PRODUCTION_LABEL : '');
  const [version, setVersion] = useState(start.version);
  const [step, setStep] = useState<'choose' | 'confirm'>(start.confirm && start.label ? 'confirm' : 'choose');
  const [approvedBy, setApprovedBy] = useState('');

  const isNew = choice === NEW_LABEL;
  const labelName = isNew ? newName.trim() : choice;
  const nameError = isNew ? labelNameError(newName, labels.map((item) => item.label)) : null;
  const current = isNew ? null : labels.find((item) => item.label === choice) ?? null;
  const fromVersion = current ? versions.find((item) => item.version === current.version) ?? null : null;
  const toVersion = versions.find((item) => item.version === version) ?? null;
  const unchanged = current?.version === version;
  const users = bindingsForLabel(bindings, labelName);
  const fromNames = fromVersion ? versionVariables(fromVersion).filter((item) => item.required).map((item) => item.name) : [];
  const newRequired = toVersion && fromVersion ? versionVariables(toVersion).filter((item) => item.required && !fromNames.includes(item.name)).map((item) => item.name) : [];
  const submit = () => {
    if (saving || !toVersion || nameError || unchanged) return;
    onConfirm({ label: labelName, version, approvedBy: approvedBy.trim() });
  };

  const title = step === 'confirm' && current
    ? `Move ${current.label} from v${current.version} to v${version}?`
    : isNew ? 'Add a label' : `Change the version of ${choice}`;

  const footer = step === 'confirm' ? (
    <div className="flex flex-wrap justify-end gap-2">
      {!start.confirm ? <Button variant="outline" size="sm" className="mr-auto" onClick={() => setStep('choose')} disabled={saving}>Back</Button> : null}
      <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button size="sm" loading={saving} onClick={submit}>{saving ? 'Moving…' : `Move ${labelName} to v${version}`}</Button>
    </div>
  ) : (
    <div className="flex flex-wrap justify-end gap-2">
      {current ? <Button variant="danger" size="sm" className="mr-auto" onClick={() => onRemove(current.label)} disabled={saving}>Remove label</Button> : null}
      <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
      {isNew ? (
        <Button size="sm" loading={saving} disabled={Boolean(nameError) || !toVersion} onClick={submit}>{saving ? 'Adding…' : 'Add label'}</Button>
      ) : (
        <Button size="sm" disabled={unchanged || !toVersion} onClick={() => setStep('confirm')}>Review change</Button>
      )}
    </div>
  );

  return (
    <Modal open wide={step === 'confirm'} onClose={saving ? () => undefined : onClose} title={title} icon={<Tag className="h-5 w-5" />} footer={footer}>
      {step === 'choose' ? (
        <div className="space-y-4">
          <p className="text-sm text-gray-600">A label is a stable name for a version. Bindings and API calls ask for a label, and get the version it points to.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="prompt-label-choice" className="mb-1 block text-sm font-medium text-gray-700">Label</label>
              <select id="prompt-label-choice" value={choice} onChange={(event) => setChoice(event.target.value)} className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm">
                {labels.map((item) => <option key={item.prompt_label_id} value={item.label}>{item.label} (now v{item.version})</option>)}
                <option value={NEW_LABEL}>New label…</option>
              </select>
            </div>
            <div>
              <label htmlFor="prompt-label-version" className="mb-1 block text-sm font-medium text-gray-700">Version</label>
              <select id="prompt-label-version" value={version} onChange={(event) => setVersion(Number(event.target.value))} className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm">
                {versions.map((item) => <option key={item.prompt_version_id} value={item.version}>v{item.version}</option>)}
              </select>
            </div>
          </div>
          {isNew ? (
            <div>
              <label htmlFor="prompt-label-name" className="mb-1 block text-sm font-medium text-gray-700">New label name</label>
              <input id="prompt-label-name" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="staging" aria-invalid={Boolean(nameError)} aria-describedby="prompt-label-name-hint" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary" />
              <p id="prompt-label-name-hint" className={`mt-1 text-xs ${nameError && newName ? 'text-red-600' : 'text-gray-500'}`}>
                {nameError && newName ? nameError : 'API calls that do not name a label use production.'}
              </p>
            </div>
          ) : null}
          {unchanged ? <p className="text-xs text-gray-500">{choice} already points to v{version}. Pick another version.</p> : null}
        </div>
      ) : (
        <div className="space-y-4">
          {toVersion ? <PromptBodyView body={toVersion.template_body} compareBody={fromVersion ? fromVersion.template_body : undefined} /> : null}
          {showUsage ? (
            <p className={`rounded-lg px-3 py-2 text-sm ${users.length ? 'border border-amber-200 bg-amber-50 text-amber-900' : 'bg-gray-50 text-gray-600'}`}>
              {users.length
                ? sentenceCase(`${users.map((binding) => bindingTargetText(binding, targetOptions)).join(', ')} ${users.length === 1 ? 'gets' : 'get'} v${version} on the next request.`)
                : `No bindings use ${labelName}. API calls that ask for it get v${version}.`}
            </p>
          ) : null}
          {newRequired.length ? (
            <p className="text-sm text-gray-700">
              v{version} also requires <code className="rounded bg-brand-primary-soft px-1 text-brand-primary-ink">{newRequired.join(', ')}</code>. Requests that do not send {newRequired.length === 1 ? 'it' : 'them'} fail to render.
            </p>
          ) : null}
          {isProductionName(labelName) ? (
            <div>
              <label htmlFor="prompt-label-approved-by" className="mb-1 block text-sm font-medium text-gray-700">Approved by (optional)</label>
              <input id="prompt-label-approved-by" value={approvedBy} onChange={(event) => setApprovedBy(event.target.value)} placeholder="name@company.com" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary" />
              <p className="mt-1 text-xs text-gray-500">The audit log records this name with the change.</p>
            </div>
          ) : null}
        </div>
      )}
    </Modal>
  );
}
