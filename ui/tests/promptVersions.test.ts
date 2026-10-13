import assert from 'node:assert/strict';
import test from 'node:test';

import type { PromptLabel } from '../src/lib/api';
import {
  buildTemplateBody,
  buildVariablesSchema,
  coerceVariableValues,
  detectBodyPlaceholders,
  detectPlaceholders,
  diffText,
  labelNameError,
  parseReferenceValue,
  parseTemplateBody,
  placeholderSpans,
  referenceValue,
  resolveReference,
  sortLabels,
  versionVariables,
} from '../src/lib/promptVersions';

const label = (name: string, version: number): PromptLabel => ({
  prompt_label_id: `l-${name}`, prompt_template_id: 't', template_key: 'k', label: name, prompt_version_id: `v${version}`, version,
});

test('placeholder detection follows Python format rules', () => {
  assert.deepEqual(detectPlaceholders('Hi {name}, {name} again'), ['name']);
  assert.deepEqual(detectPlaceholders('Literal {{braces}} and {{ {real} }}'), ['real']);
  assert.deepEqual(detectPlaceholders('{user.name} {items[0]} {price:.2f} {tone!r}'), ['user', 'items', 'price', 'tone']);
  assert.deepEqual(detectPlaceholders('{} {0} {1st} {not closed'), []);
  assert.deepEqual(detectBodyPlaceholders({ messages: [{ role: 'system', content: '{a}' }, { role: 'user', content: '{b} {a}' }] }), ['a', 'b']);
});

test('placeholder spans mark only real placeholders', () => {
  assert.deepEqual(placeholderSpans('Use {{x}} or {y}.'), [
    { text: 'Use {{x}} or ', variable: false },
    { text: '{y}', variable: true },
    { text: '.', variable: false },
  ]);
});

test('template bodies round-trip and keep fields the editor does not show', () => {
  const messages = { messages: [{ role: 'system', content: 'Hi {name}', name: 'core' }], response_format: { type: 'text' } };
  const parsed = parseTemplateBody(messages);
  assert.equal(parsed.kind, 'messages');
  assert.deepEqual(buildTemplateBody(parsed), messages);

  const text = { text: 'hello {name}', note: 'kept' };
  assert.deepEqual(buildTemplateBody(parseTemplateBody(text)), text);

  const custom = { messages: [{ role: 'user', content: [{ type: 'text', text: 'parts' }] }] };
  const raw = parseTemplateBody(custom);
  assert.equal(raw.kind, 'raw');
  assert.deepEqual(buildTemplateBody(raw), custom);
});

test('variables schema keeps existing definitions and requires every placeholder', () => {
  const schema = buildVariablesSchema(['amount', 'language'], {
    type: 'object',
    required: ['amount', 'old'],
    properties: { amount: { type: 'number', description: 'Total' }, old: { type: 'string' } },
    additionalProperties: false,
  });
  assert.deepEqual(schema, {
    type: 'object',
    required: ['amount', 'language'],
    properties: { amount: { type: 'number', description: 'Total' }, language: { type: 'string' } },
    additionalProperties: false,
  });
});

test('version variables merge the schema with placeholders the schema misses', () => {
  const variables = versionVariables({
    template_body: { text: '{a} {b} {c}' },
    variables_schema: { required: ['a'], properties: { a: { type: 'integer' }, b: { type: 'weird' } } },
  });
  assert.deepEqual(variables, [
    { name: 'a', type: 'integer', required: true },
    { name: 'b', type: 'string', required: false },
    { name: 'c', type: 'string', required: false },
  ]);
});

test('test values are converted to the declared types', () => {
  const variables = [
    { name: 's', type: 'string' as const, required: true },
    { name: 'n', type: 'integer' as const, required: true },
    { name: 'b', type: 'boolean' as const, required: false },
    { name: 'o', type: 'object' as const, required: false },
  ];
  assert.deepEqual(coerceVariableValues(variables, { s: '', n: '3', b: 'true', o: '{"k":1}' }), {
    values: { s: '', n: 3, b: true, o: { k: 1 } },
    errors: [],
  });
  assert.deepEqual(coerceVariableValues(variables, { s: 'x', n: '3.5', o: '[1]' }).errors, [
    'n must be a whole number.',
    'o must be a JSON object.',
  ]);
});

test('diff marks changed words inside a changed line', () => {
  const segments = diffText('Keep replies short.\nBe kind.', 'Answer in {language}. Keep replies short.\nBe kind.');
  assert.deepEqual(segments, [
    { kind: 'added', text: 'Answer in {language}. ' },
    { kind: 'same', text: 'Keep replies short.\nBe kind.' },
  ]);
  assert.deepEqual(diffText('a b c', 'a x c'), [
    { kind: 'same', text: 'a ' },
    { kind: 'removed', text: 'b' },
    { kind: 'added', text: 'x' },
    { kind: 'same', text: ' c' },
  ]);
  assert.deepEqual(diffText('same', 'same'), [{ kind: 'same', text: 'same' }]);
});

test('very large diffs fall back to whole-block replacement', () => {
  const before = Array.from({ length: 1000 }, (_, index) => `line ${index}`).join('\n');
  const after = Array.from({ length: 1000 }, (_, index) => `row ${index}`).join('\n');
  assert.deepEqual(diffText(before, after), [{ kind: 'removed', text: before }, { kind: 'added', text: after }]);
});

test('labels sort production first and references resolve', () => {
  const labels = [label('canary', 1), label('staging', 3), label('production', 2)];
  assert.deepEqual(sortLabels(labels).map((item) => item.label), ['production', 'staging', 'canary']);
  const versions = [1, 2, 3].map((version) => ({ version, prompt_version_id: `v${version}` }) as never);
  assert.equal(resolveReference({ label: 'staging' }, versions, labels), versions[2]);
  assert.equal(resolveReference({ label: 'missing' }, versions, labels), null);
  assert.deepEqual(parseReferenceValue(referenceValue({ label: 'a:b' })), { label: 'a:b' });
  assert.deepEqual(parseReferenceValue(referenceValue({ version: 7 })), { version: 7 });
});

test('label names are checked before they reach the API', () => {
  assert.equal(labelNameError('  ', []), 'Enter a label name.');
  assert.equal(labelNameError('a/b', []), 'A label name cannot contain "/".');
  assert.equal(labelNameError('staging', ['staging']), 'staging already exists. Change its version instead.');
  assert.equal(labelNameError('canary', ['staging']), null);
});
