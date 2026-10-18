import { useMemo, useState } from 'react';
import { FilePlus2, Plus, X } from 'lucide-react';
import type { PromptLabel, PromptVersion, PromptVersionCreateInput } from '../../lib/api';
import {
  buildTemplateBody,
  buildVariablesSchema,
  detectBodyPlaceholders,
  parseTemplateBody,
  PRODUCTION_LABEL,
  STAGING_LABEL,
  versionVariables,
  type PromptBodyModel,
} from '../../lib/promptVersions';
import Button from '../Button';
import Modal from '../Modal';

const ROLES = ['system', 'developer', 'user', 'assistant'];
const EMPTY_MODEL: PromptBodyModel = { kind: 'messages', messages: [{ role: 'system', content: '', extra: {} }], rest: {} };
const fieldClass = 'w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-brand-primary';

interface PromptVersionEditorProps {
  base: PromptVersion | null;
  nextVersion: number;
  labels: PromptLabel[];
  saving: boolean;
  onClose: () => void;
  onSave: (payload: PromptVersionCreateInput, pointLabel: string | null) => void;
}

function parseObject(text: string, field: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text.trim() || '{}');
  } catch {
    throw new Error(`${field} is not valid JSON.`);
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${field} must be a JSON object.`);
  return parsed as Record<string, unknown>;
}

function labelChoices(labels: PromptLabel[]): string[] {
  const names = labels.map((label) => label.label);
  const choices = names.filter((name) => name !== PRODUCTION_LABEL);
  if (!names.includes(PRODUCTION_LABEL)) choices.unshift(PRODUCTION_LABEL);
  if (!names.includes(STAGING_LABEL)) choices.push(STAGING_LABEL);
  return choices;
}

export default function PromptVersionEditor({ base, nextVersion, labels, saving, onClose, onSave }: PromptVersionEditorProps) {
  const initialModel = base ? parseTemplateBody(base.template_body) : EMPTY_MODEL;
  const [model, setModel] = useState<PromptBodyModel>(initialModel);
  const [jsonMode, setJsonMode] = useState(initialModel.kind === 'raw');
  const [jsonText, setJsonText] = useState(() => JSON.stringify(base?.template_body ?? {}, null, 2));
  const [modelHints, setModelHints] = useState(() => JSON.stringify(base?.model_hints ?? {}, null, 2));
  const [routePreferences, setRoutePreferences] = useState(() => JSON.stringify(base?.route_preferences ?? {}, null, 2));
  const choices = labelChoices(labels);
  const hasProduction = labels.some((label) => label.label === PRODUCTION_LABEL);
  const [pointLabel, setPointLabel] = useState(() => (!hasProduction ? PRODUCTION_LABEL : labels.some((label) => label.label === STAGING_LABEL) ? STAGING_LABEL : ''));
  const [error, setError] = useState<string | null>(null);

  const currentBody = useMemo(() => {
    if (!jsonMode) return buildTemplateBody(model);
    try {
      return parseObject(jsonText, 'The prompt');
    } catch {
      return null;
    }
  }, [jsonMode, jsonText, model]);
  const detected = currentBody ? detectBodyPlaceholders(currentBody) : [];
  const baseNames = base ? versionVariables(base).map((item) => item.name) : [];
  const dropped = baseNames.filter((name) => !detected.includes(name));

  const setMessage = (index: number, patch: { role?: string; content?: string }) => {
    if (model.kind !== 'messages') return;
    setModel({ ...model, messages: model.messages.map((message, position) => (position === index ? { ...message, ...patch } : message)) });
  };

  const toggleJson = () => {
    setError(null);
    if (!jsonMode) {
      setJsonText(JSON.stringify(buildTemplateBody(model), null, 2));
      setJsonMode(true);
      return;
    }
    try {
      const parsed = parseTemplateBody(parseObject(jsonText, 'The prompt'));
      if (parsed.kind === 'raw') throw new Error('This prompt body has a custom shape. Keep editing it as JSON.');
      setModel(parsed);
      setJsonMode(false);
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : 'Could not read the JSON.');
    }
  };

  const save = () => {
    if (saving) return;
    try {
      const body = jsonMode ? parseObject(jsonText, 'The prompt') : buildTemplateBody(model);
      const parsed = parseTemplateBody(body);
      if (parsed.kind === 'messages') {
        const emptyIndex = parsed.messages.findIndex((message) => !message.content.trim());
        if (emptyIndex !== -1) throw new Error(parsed.messages.length === 1 ? 'Write the prompt before you save.' : `Message ${emptyIndex + 1} is empty. Write it or remove it.`);
      }
      if ((parsed.kind === 'text' && !parsed.text.trim()) || (parsed.kind === 'raw' && Object.keys(parsed.body).length === 0)) {
        throw new Error('Write the prompt before you save.');
      }
      onSave({
        template_body: body,
        variables_schema: buildVariablesSchema(detectBodyPlaceholders(body), base?.variables_schema),
        model_hints: parseObject(modelHints, 'Model hints'),
        route_preferences: parseObject(routePreferences, 'Route preferences'),
      }, pointLabel || null);
      setError(null);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Could not save the version.');
    }
  };

  return (
    <Modal
      open
      wide
      onClose={saving ? () => undefined : onClose}
      title={`New version · v${nextVersion}`}
      description={base ? `Starts from v${base.version}. Saved versions cannot change.` : 'The first version of this prompt. Saved versions cannot change.'}
      icon={<FilePlus2 className="h-5 w-5" />}
      footer={(
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <label htmlFor="prompt-editor-point-label" className="text-xs text-gray-600">After saving, point</label>
            <select id="prompt-editor-point-label" value={pointLabel} onChange={(event) => setPointLabel(event.target.value)} className="control-field-md rounded-lg border border-gray-300 bg-white px-2 text-xs">
              <option value="">no label</option>
              {choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
            </select>
            <span className="text-xs text-gray-600">at v{nextVersion}</span>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
            <Button size="sm" loading={saving} onClick={save}>{saving ? 'Saving…' : `Save v${nextVersion}`}</Button>
          </div>
        </div>
      )}
    >
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-gray-500">Write <code className="rounded bg-gray-100 px-1">{'{name}'}</code> where a value goes. Use <code className="rounded bg-gray-100 px-1">{'{{'}</code> for a literal brace.</p>
          <Button variant="ghost" size="sm" onClick={toggleJson} aria-pressed={jsonMode}>{jsonMode ? 'Edit as messages' : 'Edit as JSON'}</Button>
        </div>

        {jsonMode ? (
          <div>
            <label htmlFor="prompt-editor-json" className="mb-1 block text-sm font-medium text-gray-700">Template body (JSON)</label>
            <textarea id="prompt-editor-json" rows={14} value={jsonText} onChange={(event) => setJsonText(event.target.value)} className={fieldClass} />
          </div>
        ) : model.kind === 'text' ? (
          <div>
            <label htmlFor="prompt-editor-text" className="mb-1 block text-sm font-medium text-gray-700">Prompt text</label>
            <textarea id="prompt-editor-text" rows={10} value={model.text} onChange={(event) => setModel({ ...model, text: event.target.value })} className={fieldClass} />
          </div>
        ) : model.kind === 'messages' ? (
          <div className="space-y-3">
            {model.messages.map((message, index) => (
              <div key={index} className="rounded-lg border border-gray-200">
                <div className="flex items-center justify-between gap-2 border-b border-gray-100 bg-gray-50 px-3 py-1.5">
                  <label className="flex items-center gap-2 text-xs text-gray-600">
                    Message {index + 1}
                    <select value={message.role} onChange={(event) => setMessage(index, { role: event.target.value })} className="rounded border border-gray-300 bg-white px-1.5 py-0.5 text-xs" aria-label={`Message ${index + 1} role`}>
                      {[...new Set([...ROLES, message.role])].map((role) => <option key={role} value={role}>{role}</option>)}
                    </select>
                  </label>
                  {model.messages.length > 1 ? (
                    <button type="button" aria-label={`Remove message ${index + 1}`} onClick={() => setModel({ ...model, messages: model.messages.filter((_, position) => position !== index) })} className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                </div>
                <textarea
                  aria-label={`Message ${index + 1} content`}
                  rows={index === 0 ? 8 : 4}
                  value={message.content}
                  placeholder={index === 0 ? 'You are a support assistant for {product_name}.' : undefined}
                  onChange={(event) => setMessage(index, { content: event.target.value })}
                  className="block w-full rounded-b-lg border-0 px-3 py-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-inset focus:ring-brand-primary"
                />
              </div>
            ))}
            <Button variant="ghost" size="sm" onClick={() => setModel({ ...model, messages: [...model.messages, { role: 'user', content: '', extra: {} }] })}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add message
            </Button>
          </div>
        ) : null}

        <div>
          <p className="mb-1.5 text-xs font-medium text-gray-700">Variables found in the prompt</p>
          <div className="flex flex-wrap gap-1.5" aria-live="polite">
            {detected.length === 0 ? <span className="text-xs text-gray-500">None</span> : detected.map((name) => (
              <code key={name} className="rounded bg-brand-primary-soft px-1.5 py-0.5 text-xs font-medium text-brand-primary-ink">
                {name}{base && !baseNames.includes(name) ? ' · new' : ''}
              </code>
            ))}
          </div>
          {dropped.length ? <p className="mt-1.5 text-xs text-gray-500">No longer used: {dropped.join(', ')}.</p> : null}
          <p className="mt-1.5 text-xs text-gray-500">Every variable found becomes required. Callers must send a value for each one.</p>
        </div>

        <details className="rounded-lg border border-gray-200 px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium text-gray-700">Model hints and route preferences</summary>
          <div className="mt-3 grid gap-3 lg:grid-cols-2">
            <div>
              <label htmlFor="prompt-editor-model-hints" className="mb-1 block text-xs font-medium text-gray-700">Model hints (JSON)</label>
              <textarea id="prompt-editor-model-hints" rows={4} value={modelHints} onChange={(event) => setModelHints(event.target.value)} className={fieldClass} />
            </div>
            <div>
              <label htmlFor="prompt-editor-route-preferences" className="mb-1 block text-xs font-medium text-gray-700">Route preferences (JSON)</label>
              <textarea id="prompt-editor-route-preferences" rows={4} value={routePreferences} onChange={(event) => setRoutePreferences(event.target.value)} className={fieldClass} />
              <p className="mt-1 text-xs text-gray-500">Leave empty unless only some deployments may serve this prompt.</p>
            </div>
          </div>
        </details>

        {error ? <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}
      </div>
    </Modal>
  );
}
