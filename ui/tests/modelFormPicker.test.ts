import assert from 'node:assert/strict';
import test from 'node:test';
import type { ProviderModelOption, ProviderPreset } from '../src/lib/api';
import {
  compactTokenCount,
  filterModelOptions,
  listProviders,
  matchRange,
  modelOptionFacts,
  providerModeSummary,
} from '../src/components/models/providerListing';
import {
  firstInvalidPriceField,
  isPriceText,
  perMillionFromPerToken,
  perTokenFromPerMillion,
  shiftDecimal,
} from '../src/components/models/pricingUnits';
import { modelCallSnippet, modelFormChecklist } from '../src/components/models/form/modelFormSummary';
import { validateModelForm } from '../src/components/models/form/modelFormValidation';
import { EMPTY_FORM, type ModelFormValues } from '../src/components/modelFormShared';
import { EMPTY_REQUEST_SHAPING } from '../src/components/models/requestShaping';

const preset = (provider: string, supported_modes: string[], api_base: string | null = null): ProviderPreset => (
  { provider, api_base, compat: 'openai', supported_modes }
);

const PRESETS = [
  preset('anthropic', ['chat'], 'https://api.anthropic.com/v1'),
  preset('openai', ['chat', 'embedding', 'image_generation', 'audio_speech'], 'https://api.openai.com/v1'),
  preset('vllm', ['chat', 'embedding']),
  preset('openai_compatible', ['chat', 'embedding']),
  preset('elevenlabs', ['audio_speech', 'audio_transcription']),
  preset('brand_new', ['chat']),
];

test('listProviders groups providers in display order and puts unknown providers last', () => {
  const listing = listProviders(PRESETS, '', 'chat');
  assert.deepEqual(listing.sections.map((section) => section.group.id), ['labs', 'self_hosted', 'generic', 'other']);
  assert.deepEqual(listing.options.map((item) => item.provider), ['openai', 'anthropic', 'vllm', 'openai_compatible', 'brand_new']);
  assert.deepEqual(listing.unsupported.map((item) => item.provider), ['elevenlabs']);
});

test('listProviders searches the name, the key and the API base', () => {
  assert.deepEqual(listProviders(PRESETS, 'anthro', 'chat').options.map((item) => item.provider), ['anthropic']);
  assert.deepEqual(listProviders(PRESETS, 'OPENAI.com', 'chat').options.map((item) => item.provider), ['openai']);
  assert.deepEqual(listProviders(PRESETS, 'compat', '').options.map((item) => item.provider), ['openai_compatible']);
  assert.deepEqual(listProviders(PRESETS, 'nothing', 'chat').options, []);
});

test('listProviders keeps the selected provider when it does not support the model type', () => {
  const listing = listProviders(PRESETS, '', 'embedding', 'anthropic');
  assert.ok(listing.options.some((item) => item.provider === 'anthropic'));
  assert.ok(!listing.unsupported.some((item) => item.provider === 'anthropic'));
});

test('providerModeSummary orders model types and shortens long lists', () => {
  assert.equal(providerModeSummary(['embedding', 'chat']), 'Chat, Embeddings');
  assert.equal(providerModeSummary(['rerank', 'audio_speech', 'chat', 'embedding', 'image_generation']), 'Chat, Embeddings, Images +2');
});

test('matchRange finds the search text without case', () => {
  assert.deepEqual(matchRange('Google Gemini', 'gem'), [7, 10]);
  assert.equal(matchRange('OpenAI', ''), null);
  assert.equal(matchRange('OpenAI', 'xyz'), null);
});

test('model suggestions filter by ID and label and show context and price', () => {
  const options: ProviderModelOption[] = [
    { id: 'gpt-4o', label: 'GPT-4o', provider: 'openai', source: 'catalog', supported_modes: ['chat'], known_metadata: { max_input_tokens: 128000, input_cost_per_token: 0.0000025, output_cost_per_token: 0.00001 } },
    { id: 'o3', label: 'o3 reasoning', provider: 'openai', source: 'catalog', supported_modes: ['chat'], known_metadata: null },
  ];
  assert.deepEqual(filterModelOptions(options, 'REASON').map((item) => item.id), ['o3']);
  assert.deepEqual(modelOptionFacts(options[0]), { context: '128K', price: '$2.50 / $10.00' });
  assert.deepEqual(modelOptionFacts(options[1]), { context: null, price: null });
  assert.equal(compactTokenCount(1_048_576), '1M');
  assert.equal(compactTokenCount(8192), '8.2K');
  assert.equal(compactTokenCount(0), null);
});

test('price units move the decimal point exactly', () => {
  assert.equal(perMillionFromPerToken('0.0000025'), '2.5');
  assert.equal(perMillionFromPerToken('2.5e-6'), '2.5');
  assert.equal(perMillionFromPerToken('1e-7'), '0.1');
  assert.equal(perMillionFromPerToken('0.00000000123'), '0.00123');
  assert.equal(perTokenFromPerMillion('2.50'), '0.0000025');
  assert.equal(perTokenFromPerMillion('10'), '0.00001');
  assert.equal(perTokenFromPerMillion('2.'), '0.000002');
  assert.equal(perTokenFromPerMillion('1234567'), '1.234567');
  assert.equal(perTokenFromPerMillion(''), '');
  assert.equal(perTokenFromPerMillion('abc'), 'abc');
  assert.equal(shiftDecimal('.5', 1), '5');
  // A huge exponent is not a price, and it must not build a huge string.
  assert.equal(shiftDecimal('1e9999999999', 0), null);
  assert.equal(perTokenFromPerMillion('1e9999999999'), '1e9999999999');
  assert.ok(!isPriceText('1e9999999999'));
  assert.equal(shiftDecimal('2.5e-100', 0)?.length, 103);
  for (const value of ['0.0000025', '0.00001', '0.000000075', '3', '0.1234567891']) {
    assert.equal(perTokenFromPerMillion(perMillionFromPerToken(value)), value);
  }
});

test('price text must be a number that is not negative', () => {
  assert.ok(isPriceText(''));
  assert.ok(isPriceText(' 2.5 '));
  assert.ok(isPriceText('2.5e-6'));
  assert.ok(!isPriceText('abc'));
  assert.ok(!isPriceText('-1'));
  assert.equal(firstInvalidPriceField({ ...EMPTY_FORM, output_cost_per_token: '1,5' }), 'output_cost_per_token');
  assert.equal(firstInvalidPriceField(EMPTY_FORM), null);
});

const VALID: ModelFormValues = {
  ...EMPTY_FORM,
  model_name: ' Support ',
  api_model_id: 'support',
  provider: 'openai',
  model: ' gpt-4o ',
  credential_source: 'named',
  named_credential_id: 'cred-1',
};
const CONTEXT = { namespaceRequired: false, lockApiModelId: false, allowInlineCredentials: true };

test('validateModelForm returns trimmed values for a complete form', () => {
  const result = validateModelForm(VALID, EMPTY_REQUEST_SHAPING, { ...CONTEXT, preset: PRESETS[1] });
  assert.ok(result.ok);
  assert.equal(result.values.model_name, 'Support');
  assert.equal(result.values.model, 'gpt-4o');
});

test('validateModelForm marks every missing required field', () => {
  const result = validateModelForm({ ...EMPTY_FORM, credential_source: 'inline' }, EMPTY_REQUEST_SHAPING, CONTEXT);
  assert.ok(!result.ok);
  assert.deepEqual(Object.keys(result.fieldErrors).sort(), ['api_base', 'api_model_id', 'model', 'model_name', 'provider']);
});

test('validateModelForm rejects a provider without the model type, bad prices and bad batch settings', () => {
  const unsupported = validateModelForm({ ...VALID, mode: 'embedding' }, EMPTY_REQUEST_SHAPING, { ...CONTEXT, preset: PRESETS[0] });
  assert.ok(!unsupported.ok);
  assert.match(unsupported.message, /Anthropic does not support Embedding/);
  assert.ok(unsupported.fieldErrors.provider);

  const price = validateModelForm({ ...VALID, input_cost_per_token: 'free' }, EMPTY_REQUEST_SHAPING, CONTEXT);
  assert.ok(!price.ok);
  assert.equal(price.section, 'pricing');

  const batch = validateModelForm({ ...VALID, chat_batching_mode: 'sync_microbatch', chat_batching_upstream_max_batch_size: '1' }, EMPTY_REQUEST_SHAPING, CONTEXT);
  assert.ok(!batch.ok);
  assert.equal(batch.section, 'batch');

  // A bad price in a field that the model type hides does not block Save.
  const hidden = validateModelForm({ ...VALID, mode: 'embedding', output_cost_per_token: 'free' }, EMPTY_REQUEST_SHAPING, CONTEXT);
  assert.ok(hidden.ok);
  const realtime = validateModelForm(
    { ...VALID, mode: 'realtime', realtime_profile: 'transcription', realtime_usage_type: 'duration', input_cost_per_second: 'x' },
    EMPTY_REQUEST_SHAPING,
    CONTEXT,
  );
  assert.ok(!realtime.ok);
  assert.equal(realtime.section, 'pricing');
  // Realtime sends every rate, so a hidden bad rate still blocks Save with a clear message.
  const hiddenRealtime = validateModelForm(
    { ...VALID, mode: 'realtime', realtime_profile: 'transcription', realtime_usage_type: 'duration', input_cost_per_audio_token: 'bad' },
    EMPTY_REQUEST_SHAPING,
    CONTEXT,
  );
  assert.ok(!hiddenRealtime.ok);
  assert.match(hiddenRealtime.message, /hidden Input audio price/);

  const limits = validateModelForm({ ...VALID, max_input_tokens: '0' }, EMPTY_REQUEST_SHAPING, CONTEXT);
  assert.ok(!limits.ok);
  assert.equal(limits.section, 'limits');
  assert.ok(limits.fieldErrors.max_input_tokens);
});

test('the summary shows a call example for the model type and the required steps', () => {
  assert.equal(modelCallSnippet('embedding', 'docs'), 'POST /v1/embeddings\n{\n  "model": "docs",\n  "input": "..."\n}');
  assert.match(modelCallSnippet('chat', ''), /"model": "<api-model-id>"/);
  assert.equal(modelCallSnippet('realtime', 'live', 'realtime'), 'WebSocket /v1/realtime\n?model=live');
  assert.equal(modelCallSnippet('realtime', 'live', 'transcription'), 'WebSocket /v1/realtime\n?model=live&intent=transcription');
  const checklist = modelFormChecklist({ modelName: 'A', apiModelId: 'a', provider: 'openai', model: '', credentialReady: true, hasPrice: false });
  assert.deepEqual(checklist.filter((item) => !item.done).map((item) => item.label), ['Provider model', 'Pricing']);
  assert.equal(checklist.find((item) => item.label === 'Pricing')?.optional, true);
});
