import assert from 'node:assert/strict';
import test from 'node:test';
import { EMPTY_FORM, type ModelFormValues } from '../src/components/modelFormShared';
import { applyKnownMetadata, type AutofilledValues } from '../src/components/models/metadataAutofill';

const GPT_4 = { input_cost_per_token: 0.00003, output_cost_per_token: 0.00006, max_tokens: 8192, max_output_tokens: 8192 };
const GPT_4O = {
  input_cost_per_token: 0.0000025,
  output_cost_per_token: 0.00001,
  input_cost_per_token_cache_hit: 0.00000125,
  max_tokens: 128000,
  max_output_tokens: 16384,
};

function typeModel(values: string[], catalog: Record<string, Record<string, number>>, start: ModelFormValues = EMPTY_FORM) {
  let form = start;
  let autofilled: AutofilledValues = {};
  for (const value of values) {
    const result = applyKnownMetadata({ ...form, model: value }, autofilled, catalog[value] || null);
    form = result.form;
    autofilled = result.autofilled;
  }
  return { form, autofilled };
}

test('typing past a shorter catalog match ends with the typed model values', () => {
  const { form } = typeModel(['g', 'gpt-4', 'gpt-4o'], { 'gpt-4': GPT_4, 'gpt-4o': GPT_4O });
  assert.equal(form.input_cost_per_token, '0.0000025');
  assert.equal(form.output_cost_per_token, '0.00001');
  assert.equal(form.input_cost_per_token_cache_hit, '0.00000125');
  assert.equal(form.max_context_window, '128000');
  assert.equal(form.max_output_tokens, '16384');
});

test('values filled for a model are cleared when the model no longer matches', () => {
  const { form, autofilled } = typeModel(['gpt-4', 'gpt-4-'], { 'gpt-4': GPT_4 });
  assert.equal(form.input_cost_per_token, '');
  assert.equal(form.max_context_window, '');
  assert.deepEqual(autofilled, {});
});

test('a field the user edited is kept when the model changes', () => {
  const typed = typeModel(['gpt-4'], { 'gpt-4': GPT_4 });
  const form = { ...typed.form, input_cost_per_token: '0.00002' };
  const autofilled = typed.autofilled;
  const result = applyKnownMetadata({ ...form, model: 'gpt-4o' }, autofilled, GPT_4O);
  assert.equal(result.form.input_cost_per_token, '0.00002');
  assert.equal(result.form.output_cost_per_token, '0.00001');
  assert.equal(result.autofilled.input_cost_per_token, undefined);
});

test('values from a saved model are not overwritten, empty fields are filled', () => {
  const saved: ModelFormValues = { ...EMPTY_FORM, model: 'gpt-4o', input_cost_per_token: '0.000003' };
  const result = applyKnownMetadata(saved, {}, GPT_4O);
  assert.equal(result.form.input_cost_per_token, '0.000003');
  assert.equal(result.form.max_context_window, '128000');
  assert.equal(result.autofilled.input_cost_per_token, undefined);
  assert.equal(result.autofilled.max_context_window, '128000');
});

test('returns the same form object when nothing changes', () => {
  const result = applyKnownMetadata(EMPTY_FORM, {}, null);
  assert.equal(result.form, EMPTY_FORM);
});
