import { useId, type Ref } from 'react';
import { ExternalLink } from 'lucide-react';
import AccessGroupTokenInput, { type AccessGroupTokenInputHandle } from '../../AccessGroupTokenInput';
import type { ChatBatchingMode, ContextCapacityField, ModelFormValues } from '../../modelFormShared';
import { FieldError, FieldLabel } from './formPrimitives';
import { HELP_CLASS, fieldClass } from './formStyles';

const MODEL_BATCH_CONFIG_DOCS_URL = 'https://deltallm.readthedocs.io/en/latest/admin-ui/models/#chat-batch-execution';

const CHAT_BATCHING_MODES: { value: ChatBatchingMode; label: string }[] = [
  { value: '', label: 'Default concurrent' },
  { value: 'disabled', label: 'Disabled' },
  { value: 'concurrent', label: 'Concurrent' },
  { value: 'sync_microbatch', label: 'Sync microbatch' },
];

type Update = (patch: Partial<ModelFormValues>) => void;
type StringField = {
  [K in keyof ModelFormValues]: ModelFormValues[K] extends string ? K : never;
}[keyof ModelFormValues];

interface NumberFieldProps {
  form: ModelFormValues;
  onChange: Update;
  field: StringField;
  label: string;
  placeholder?: string;
  help?: string;
  error?: string;
  disabled?: boolean;
  min?: string;
  max?: string;
  step?: string;
}

function NumberField({ form, onChange, field, label, placeholder, help, error, disabled, min, max, step }: NumberFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div>
      <FieldLabel htmlFor={id} label={label} />
      <input
        id={id}
        type="number"
        min={min}
        max={max}
        step={step}
        value={form[field]}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(event) => onChange({ [field]: event.target.value })}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : undefined}
        className={fieldClass(Boolean(error))}
      />
      <FieldError id={errorId} message={error} />
      {help ? <p className={HELP_CLASS}>{help}</p> : null}
    </div>
  );
}

export function LimitsRoutingFields({ form, onChange, accessGroupInputRef }: {
  form: ModelFormValues;
  onChange: Update;
  accessGroupInputRef: Ref<AccessGroupTokenInputHandle>;
}) {
  const tagsId = useId();
  return (
    <>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <NumberField form={form} onChange={onChange} field="rpm" label="Requests per minute" placeholder="500" />
        <NumberField form={form} onChange={onChange} field="tpm" label="Tokens per minute" placeholder="100000" />
        <NumberField form={form} onChange={onChange} field="weight" label="Weight" placeholder="1" help="Load balancing" />
        <NumberField form={form} onChange={onChange} field="priority" label="Priority" placeholder="0" help="Higher is preferred" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <FieldLabel htmlFor={tagsId} label="Tags" />
          <input id={tagsId} value={form.tags} onChange={(event) => onChange({ tags: event.target.value })} placeholder="production, fast, us-east" className={fieldClass()} />
          <p className={HELP_CLASS}>Comma-separated, for tag-based routing.</p>
        </div>
        <div>
          <FieldLabel label="Access groups" />
          <AccessGroupTokenInput
            ref={accessGroupInputRef}
            ariaLabel="Access groups"
            value={form.access_groups}
            onChange={(accessGroups) => onChange({ access_groups: accessGroups })}
            placeholder="premium, beta, internal"
          />
          <p className={HELP_CLASS}>Choose a group or type a group key, then press comma or Enter.</p>
        </div>
      </div>
    </>
  );
}

type CapacityErrors = Partial<Record<ContextCapacityField, string>>;

export function ChatLimitsFields({ form, onChange, errors }: { form: ModelFormValues; onChange: Update; errors: CapacityErrors }) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <NumberField form={form} onChange={onChange} field="max_context_window" label="Context window" placeholder="128000" min="1" step="1" error={errors.max_context_window} />
      <NumberField form={form} onChange={onChange} field="max_input_tokens" label="Max input tokens" placeholder="128000" min="1" step="1" error={errors.max_input_tokens} />
      <NumberField form={form} onChange={onChange} field="max_output_tokens" label="Max output tokens" placeholder="4096" min="1" step="1" error={errors.max_output_tokens} />
      <NumberField form={form} onChange={onChange} field="max_tokens" label="Max tokens per request" placeholder="4096" />
      <NumberField form={form} onChange={onChange} field="stream_timeout" label="Stream timeout (s)" placeholder="120" />
    </div>
  );
}

export function EmbeddingLimitsFields({ form, onChange, errors }: { form: ModelFormValues; onChange: Update; errors: CapacityErrors }) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <NumberField form={form} onChange={onChange} field="max_context_window" label="Context window" placeholder="8192" min="1" step="1" error={errors.max_context_window} />
      <NumberField form={form} onChange={onChange} field="max_input_tokens" label="Max input tokens" placeholder="8192" min="1" step="1" error={errors.max_input_tokens} />
      <NumberField form={form} onChange={onChange} field="output_vector_size" label="Output vector size" placeholder="1536" />
    </div>
  );
}

export function BatchExecutionFields({ form, onChange }: { form: ModelFormValues; onChange: Update }) {
  const modeId = useId();
  const disabled = form.chat_batching_mode === 'disabled';
  const microbatch = form.chat_batching_mode === 'sync_microbatch';
  const supportsCapacity = form.mode === 'chat' || form.mode === 'embedding';
  return (
    <>
      {form.mode === 'chat' ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <FieldLabel
              htmlFor={modeId}
              label="Chat batch mode"
              action={(
                <a href={MODEL_BATCH_CONFIG_DOCS_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[11px] font-medium text-brand-primary-ink hover:text-brand-primary-ink-hover">
                  Docs <ExternalLink className="h-3 w-3" aria-hidden="true" />
                </a>
              )}
            />
            <select id={modeId} value={form.chat_batching_mode} onChange={(event) => onChange({ chat_batching_mode: event.target.value as ChatBatchingMode })} className={fieldClass()}>
              {CHAT_BATCHING_MODES.map((option) => <option key={option.value || 'default'} value={option.value}>{option.label}</option>)}
            </select>
            <p className={HELP_CLASS}>Default concurrent sends one upstream request for each batch item.</p>
          </div>
          <NumberField form={form} onChange={onChange} field="chat_batching_max_in_flight" label="Max in-flight" placeholder="Optional, e.g. 32" min="1" step="1" disabled={disabled} help="For each worker replica. Total capacity grows with replicas." />
          <NumberField form={form} onChange={onChange} field="chat_batching_upstream_max_batch_size" label="Upstream max size" placeholder={microbatch ? 'Required, e.g. 8' : 'Sync microbatch only'} min="1" step="1" disabled={!microbatch} help="Required for sync microbatch. Use 2 or more." />
          <NumberField form={form} onChange={onChange} field="chat_batching_max_total_input_tokens" label="Max total input tokens" placeholder="Optional, e.g. 32000" min="1" step="1" disabled={!microbatch} />
        </div>
      ) : null}
      {form.mode === 'embedding' ? (
        <NumberField
          form={form}
          onChange={onChange}
          field="upstream_max_batch_inputs"
          label="Batch worker upstream max inputs"
          placeholder="Optional, e.g. 8"
          min="1"
          step="1"
          help="Only for async embedding batch jobs, not /v1/embeddings. Leave empty or use 1 to turn off micro-batching. Start small."
        />
      ) : null}
      {supportsCapacity ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <NumberField form={form} onChange={onChange} field="batch_capacity_max_in_flight" label="Scheduler max in-flight" placeholder="Optional, e.g. 4" min="1" step="1" help="Slots this deployment gives to batch scheduling." />
          <NumberField form={form} onChange={onChange} field="batch_capacity_max_claim_work_units" label="Scheduler max claim work units" placeholder="Optional, e.g. 200" min="1" step="1" help="Work-unit limit for each scheduler claim." />
          <NumberField form={form} onChange={onChange} field="batch_capacity_capacity_fraction" label="Capacity fraction" placeholder="Optional, e.g. 0.25" min="0" max="1" step="any" help="Only when capacity comes from router limits." />
        </div>
      ) : null}
    </>
  );
}
