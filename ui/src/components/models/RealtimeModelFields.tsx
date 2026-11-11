import { useId } from 'react';
import type { ModelFormValues } from '../modelFormShared';
import PriceInput from './form/PriceInput';
import { FieldLabel } from './form/formPrimitives';
import { fieldClass } from './form/formStyles';

const tokenFields = [
  ['input_cost_per_token', 'Input text'],
  ['output_cost_per_token', 'Output text'],
  ['input_cost_per_token_cache_hit', 'Cached input text'],
  ['input_cost_per_audio_token', 'Input audio'],
  ['output_cost_per_audio_token', 'Output audio'],
  ['input_cost_per_audio_token_cache_hit', 'Cached input audio'],
] as const;

export default function RealtimeModelFields({ form, onChange }: {
  form: ModelFormValues;
  onChange: (form: ModelFormValues) => void;
}) {
  const id = useId();
  const duration = form.realtime_profile === 'transcription' && form.realtime_usage_type === 'duration';
  const prices = tokenFields.filter(([field]) => form.realtime_profile !== 'transcription' || field !== 'output_cost_per_audio_token');
  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500">
        OpenAI WebSocket sessions with manual turn control. Turn on Realtime in gateway settings before you connect.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <FieldLabel htmlFor={`${id}-profile`} label="Session type" />
          <select
            id={`${id}-profile`}
            aria-label="Realtime session type"
            className={fieldClass()}
            value={form.realtime_profile}
            onChange={(event) => onChange({ ...form, realtime_profile: event.target.value as ModelFormValues['realtime_profile'], realtime_usage_type: 'tokens' })}
          >
            <option value="realtime">Conversation and generated speech</option>
            <option value="transcription">Live transcription</option>
          </select>
        </div>
        {form.realtime_profile === 'transcription' ? (
          <div>
            <FieldLabel htmlFor={`${id}-usage`} label="Provider usage format" />
            <select
              id={`${id}-usage`}
              aria-label="Realtime usage format"
              className={fieldClass()}
              value={form.realtime_usage_type}
              onChange={(event) => onChange({ ...form, realtime_usage_type: event.target.value as ModelFormValues['realtime_usage_type'] })}
            >
              <option value="tokens">Text and audio tokens</option>
              <option value="duration">Audio duration</option>
            </select>
          </div>
        ) : null}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {duration ? (
          <PriceInput
            id={`${id}-input_cost_per_second`}
            label="Input audio per second"
            value={form.input_cost_per_second}
            onChange={(value) => onChange({ ...form, input_cost_per_second: value })}
          />
        ) : prices.map(([field, label]) => (
          <PriceInput
            key={field}
            id={`${id}-${field}`}
            label={`${label} per 1M tokens`}
            perMillion
            value={form[field]}
            onChange={(value) => onChange({ ...form, [field]: value })}
          />
        ))}
      </div>
      <p className="text-[11px] text-gray-400">
        Enter a price for each billable part. An empty cache price uses the full input price. Check the provider price list for this model.
      </p>
    </div>
  );
}
