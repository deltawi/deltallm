import type { ModelFormValues } from '../modelFormShared';

const tokenFields = [
  ['input_cost_per_token', 'Input text / token'],
  ['output_cost_per_token', 'Output text / token'],
  ['input_cost_per_token_cache_hit', 'Cached input text / token'],
  ['input_cost_per_audio_token', 'Input audio / token'],
  ['output_cost_per_audio_token', 'Output audio / token'],
  ['input_cost_per_audio_token_cache_hit', 'Cached input audio / token'],
] as const;

export default function RealtimeModelFields({ form, onChange }: {
  form: ModelFormValues;
  onChange: (form: ModelFormValues) => void;
}) {
  const duration = form.realtime_profile === 'transcription' && form.realtime_usage_type === 'duration';
  const prices = duration ? [['input_cost_per_second', 'Input audio / second']] as const : tokenFields;
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5 space-y-4">
      <h3 className="font-medium text-gray-900">Realtime audio</h3>
      <p className="text-sm text-gray-600">OpenAI WebSocket sessions with manual turn control. Enable Realtime in gateway settings before connecting.</p>
      <label className="block text-sm">Session type
        <select aria-label="Realtime session type" className="mt-1 block w-full rounded-lg border p-2" value={form.realtime_profile}
          onChange={e => onChange({ ...form, realtime_profile: e.target.value as ModelFormValues['realtime_profile'], realtime_usage_type: 'tokens' })}>
          <option value="realtime">Conversation and generated speech</option>
          <option value="transcription">Live transcription</option>
        </select>
      </label>
      {form.realtime_profile === 'transcription' && <label className="block text-sm">Provider usage format
        <select aria-label="Realtime usage format" className="mt-1 block w-full rounded-lg border p-2" value={form.realtime_usage_type}
          onChange={e => onChange({ ...form, realtime_usage_type: e.target.value as ModelFormValues['realtime_usage_type'] })}>
          <option value="tokens">Text and audio tokens</option>
          <option value="duration">Audio duration</option>
        </select>
      </label>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {prices.filter(([field]) => form.realtime_profile !== 'transcription' || field !== 'output_cost_per_audio_token').map(([field, label]) => (
          <label key={field} className="block text-sm text-gray-700">{label} ($)
            <input className="mt-1 block w-full rounded-lg border p-2" type="number" min="0" step="any" value={form[field]}
              onChange={e => onChange({ ...form, [field]: e.target.value })} />
          </label>
        ))}
      </div>
      <p className="text-xs text-gray-500">Enter rates for every billable dimension. An omitted cache rate uses the full input rate. Check the provider rate card for this exact model.</p>
    </section>
  );
}
