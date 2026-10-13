import { useEffect, useRef, useState } from 'react';
import { CheckCircle2 } from 'lucide-react';
import { promptRegistry, type PromptLabel, type PromptRenderResult, type PromptVersion } from '../../lib/api';
import { coerceVariableValues, resolveReference, versionVariables, type PromptReference } from '../../lib/promptVersions';
import Button from '../Button';
import PromptBodyView from './PromptBodyView';
import PromptReferenceSelect from './PromptReferenceSelect';

interface PromptTestTabProps {
  templateKey: string;
  versions: PromptVersion[];
  labels: PromptLabel[];
  reference: PromptReference;
  initialValues: Record<string, string>;
  onReferenceChange: (reference: PromptReference) => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'The test render failed.';
}

function toFieldValues(values: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(Object.entries(values).map(([name, value]) => [name, typeof value === 'string' ? value : JSON.stringify(value)]));
}

export default function PromptTestTab({ templateKey, versions, labels, reference, initialValues, onReferenceChange }: PromptTestTabProps) {
  const [values, setValues] = useState<Record<string, string>>(initialValues);
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState('{}');
  const [result, setResult] = useState<PromptRenderResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  const version = resolveReference(reference, versions, labels);
  const variables = version ? versionVariables(version) : [];

  const changeReference = (next: PromptReference) => {
    controller.current?.abort();
    setRunning(false);
    setResult(null);
    setError(null);
    onReferenceChange(next);
  };

  const switchMode = () => {
    if (jsonMode) {
      try {
        const parsed: unknown = JSON.parse(jsonText);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Variables must be a JSON object.');
        setValues((current) => ({ ...current, ...toFieldValues(parsed as Record<string, unknown>) }));
        setJsonMode(false);
        setError(null);
      } catch (parseError) {
        setError(errorMessage(parseError));
      }
      return;
    }
    const coerced = coerceVariableValues(variables, values);
    setJsonText(JSON.stringify(coerced.values, null, 2));
    setJsonMode(true);
  };

  const run = async () => {
    if (running || !version) return;
    let variablePayload: Record<string, unknown>;
    if (jsonMode) {
      try {
        const parsed: unknown = JSON.parse(jsonText);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Variables must be a JSON object.');
        variablePayload = parsed as Record<string, unknown>;
      } catch (parseError) {
        setError(errorMessage(parseError));
        return;
      }
    } else {
      const coerced = coerceVariableValues(variables, values);
      if (coerced.errors.length) {
        setError(coerced.errors.join(' '));
        return;
      }
      variablePayload = coerced.values;
    }
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setRunning(true);
    setError(null);
    try {
      const target = 'label' in reference ? { label: reference.label } : { version: reference.version };
      const response = await promptRegistry.dryRunRender({ template_key: templateKey, variables: variablePayload, ...target }, current.signal);
      if (current.signal.aborted) return;
      setResult(response);
    } catch (renderError) {
      if (current.signal.aborted) return;
      setResult(null);
      setError(errorMessage(renderError));
    } finally {
      if (!current.signal.aborted) setRunning(false);
    }
  };

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
      <section className="min-w-0 rounded-xl border border-gray-200 bg-white shadow-sm" aria-label="Test inputs">
        <div className="border-b border-gray-100 px-4 py-3"><h2 className="text-sm font-semibold text-gray-900">Inputs</h2></div>
        <form className="space-y-3 p-4" onSubmit={(event) => { event.preventDefault(); void run(); }}>
          <div>
            <label htmlFor="prompt-test-reference" className="mb-1 block text-xs font-medium text-gray-700">Label or version</label>
            <PromptReferenceSelect id="prompt-test-reference" label="Label or version to test" value={reference} labels={labels} versions={versions} onChange={changeReference} className="w-full" />
          </div>
          {jsonMode ? (
            <div>
              <label htmlFor="prompt-test-json" className="mb-1 block text-xs font-medium text-gray-700">Variables (JSON object)</label>
              <textarea id="prompt-test-json" value={jsonText} onChange={(event) => setJsonText(event.target.value)} rows={8} className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-brand-primary" />
            </div>
          ) : variables.length === 0 ? (
            <p className="text-xs text-gray-500">This version uses no variables.</p>
          ) : variables.map((variable) => (
            <div key={variable.name}>
              <label htmlFor={`prompt-test-var-${variable.name}`} className="mb-1 flex items-center gap-2 text-xs font-medium text-gray-700">
                <code className="rounded bg-brand-primary-soft px-1.5 py-0.5 text-brand-primary-ink">{variable.name}</code>
                <span className="font-normal text-gray-500">{variable.type}{variable.required ? '' : ', optional'}</span>
              </label>
              {variable.type === 'boolean' ? (
                <select id={`prompt-test-var-${variable.name}`} value={values[variable.name] ?? ''} onChange={(event) => setValues({ ...values, [variable.name]: event.target.value })} className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm">
                  <option value="">Not set</option>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <input
                  id={`prompt-test-var-${variable.name}`}
                  value={values[variable.name] ?? ''}
                  onChange={(event) => setValues({ ...values, [variable.name]: event.target.value })}
                  placeholder={variable.type === 'object' || variable.type === 'array' ? `JSON ${variable.type}` : undefined}
                  inputMode={variable.type === 'number' || variable.type === 'integer' ? 'decimal' : undefined}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                />
              )}
            </div>
          ))}
          <div className="flex flex-wrap gap-2 pt-1">
            <Button type="submit" size="sm" loading={running} disabled={!version}>{running ? 'Running…' : 'Run test'}</Button>
            <Button type="button" variant="ghost" size="sm" onClick={switchMode}>{jsonMode ? 'Use fields' : 'Edit as JSON'}</Button>
          </div>
        </form>
      </section>

      <section className="min-w-0 rounded-xl border border-gray-200 bg-white shadow-sm" aria-label="Test result" aria-live="polite">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-gray-900">Result</h2>
          {result ? (
            <Button variant="ghost" size="sm" aria-pressed={showRaw} onClick={() => setShowRaw((current) => !current)}>{showRaw ? 'Show messages' : 'Show raw response'}</Button>
          ) : null}
        </div>
        <div className="space-y-3 p-4">
          {error ? <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}
          {result && !error ? (
            <>
              <p className="flex items-center gap-1.5 text-sm text-emerald-700">
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                Rendered v{result.version}{result.label ? ` through the ${result.label} label` : ''}.
              </p>
              {showRaw ? (
                <pre className="max-h-[32rem] overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-3 font-mono text-xs">{JSON.stringify(result, null, 2)}</pre>
              ) : (
                <PromptBodyView highlightVariables={false} body={{ messages: (Array.isArray(result.messages) ? result.messages : []).map((message) => ({ ...message, content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content) })) }} />
              )}
            </>
          ) : null}
          {!result && !error ? <p className="text-sm text-gray-500">Run the test to see the exact messages that a request gets from this prompt.</p> : null}
        </div>
      </section>
    </div>
  );
}
