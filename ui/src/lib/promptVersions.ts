import type { PromptBinding, PromptLabel, PromptVersion } from './api';

export const PRODUCTION_LABEL = 'production';
export const STAGING_LABEL = 'staging';

export interface PromptMessage {
  role: string;
  content: string;
  /** Other message fields (for example `name`) that the editor keeps unchanged. */
  extra: Record<string, unknown>;
}

/**
 * Editable view of a version's `template_body`. `rest` holds the body's other
 * top-level keys so a new version keeps them unchanged.
 */
export type PromptBodyModel =
  | { kind: 'messages'; messages: PromptMessage[]; rest: Record<string, unknown> }
  | { kind: 'text'; text: string; rest: Record<string, unknown> }
  | { kind: 'raw'; body: Record<string, unknown> };

export type PromptVariableType = 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array';

export interface PromptVariable {
  name: string;
  type: PromptVariableType;
  required: boolean;
}

export type DiffSegment = { kind: 'same' | 'added' | 'removed'; text: string };

export type PromptReference = { label: string } | { version: number };

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Returns the variable names a template string reads, in order of first use.
 * The backend renders with Python `str.format_map`, so `{{` and `}}` are
 * literal braces and `{name.attr}`, `{name[0]}`, `{name!r}` and `{name:>8}` all read `name`.
 */
export function detectPlaceholders(template: string): string[] {
  const names: string[] = [];
  let index = 0;
  while (index < template.length) {
    const char = template[index];
    if (char === '{') {
      if (template[index + 1] === '{') {
        index += 2;
        continue;
      }
      const end = template.indexOf('}', index + 1);
      if (end === -1) break;
      const field = template.slice(index + 1, end);
      const root = field.split(/[.[!:]/, 1)[0].trim();
      if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(root) && !names.includes(root)) names.push(root);
      index = end + 1;
      continue;
    }
    index += 1;
  }
  return names;
}

function collectStrings(value: unknown, into: string[]): void {
  if (typeof value === 'string') into.push(value);
  else if (Array.isArray(value)) value.forEach((item) => collectStrings(item, into));
  else if (isRecord(value)) Object.values(value).forEach((item) => collectStrings(item, into));
}

export function detectBodyPlaceholders(body: unknown): string[] {
  const strings: string[] = [];
  collectStrings(body, strings);
  const names: string[] = [];
  for (const text of strings) {
    for (const name of detectPlaceholders(text)) if (!names.includes(name)) names.push(name);
  }
  return names;
}

export function parseTemplateBody(body: unknown): PromptBodyModel {
  if (!isRecord(body)) return { kind: 'raw', body: {} };
  const { messages, text, ...rest } = body;
  if (Array.isArray(messages) && messages.length > 0 && text === undefined) {
    const parsed: PromptMessage[] = [];
    for (const item of messages) {
      if (!isRecord(item) || typeof item.role !== 'string' || typeof item.content !== 'string') {
        return { kind: 'raw', body };
      }
      const { role, content, ...extra } = item;
      parsed.push({ role: role as string, content: content as string, extra });
    }
    return { kind: 'messages', messages: parsed, rest };
  }
  if (typeof text === 'string' && messages === undefined) return { kind: 'text', text, rest };
  return { kind: 'raw', body };
}

export function buildTemplateBody(model: PromptBodyModel): Record<string, unknown> {
  if (model.kind === 'raw') return model.body;
  if (model.kind === 'text') return { ...model.rest, text: model.text };
  return {
    ...model.rest,
    messages: model.messages.map(({ role, content, extra }) => ({ ...extra, role, content })),
  };
}

/** Text used to compare two versions. One block per message keeps role changes visible. */
export function bodyCompareText(body: unknown): string {
  const model = parseTemplateBody(body);
  if (model.kind === 'text') return model.text;
  if (model.kind === 'messages') {
    if (model.messages.length === 1 && model.messages[0].role === 'system') return model.messages[0].content;
    return model.messages.map((message) => `[${message.role}]\n${message.content}`).join('\n\n');
  }
  return JSON.stringify(model.body, null, 2);
}

const VARIABLE_TYPES: PromptVariableType[] = ['string', 'number', 'integer', 'boolean', 'object', 'array'];

/** Variables a version declares in its schema, plus any placeholder the schema does not list. */
export function versionVariables(version: Pick<PromptVersion, 'template_body' | 'variables_schema'>): PromptVariable[] {
  const schema = isRecord(version.variables_schema) ? version.variables_schema : {};
  const properties = isRecord(schema.properties) ? schema.properties : {};
  const required = Array.isArray(schema.required) ? schema.required.filter((item): item is string => typeof item === 'string') : [];
  const names: string[] = [...required];
  for (const name of Object.keys(properties)) if (!names.includes(name)) names.push(name);
  for (const name of detectBodyPlaceholders(version.template_body)) if (!names.includes(name)) names.push(name);
  return names.map((name) => {
    const property = properties[name];
    const rawType = isRecord(property) ? property.type : undefined;
    const type = VARIABLE_TYPES.includes(rawType as PromptVariableType) ? rawType as PromptVariableType : 'string';
    return { name, type, required: required.includes(name) };
  });
}

/**
 * Builds the variables schema for a new version: every placeholder is required.
 * Keeps the base schema's property definitions (type, enum, description) for
 * names that are still used and adds new names as strings.
 */
export function buildVariablesSchema(names: string[], baseSchema: Record<string, unknown> | null | undefined): Record<string, unknown> {
  const base = isRecord(baseSchema) ? baseSchema : {};
  const baseProperties = isRecord(base.properties) ? base.properties : {};
  const properties = Object.fromEntries(names.map((name) => [name, isRecord(baseProperties[name]) ? baseProperties[name] : { type: 'string' }]));
  return {
    type: 'object',
    required: names,
    properties,
    additionalProperties: typeof base.additionalProperties === 'boolean' ? base.additionalProperties : true,
  };
}

/** Converts the test form's text inputs to typed variable values for a dry-run render. */
export function coerceVariableValues(variables: PromptVariable[], values: Record<string, string>): { values: Record<string, unknown>; errors: string[] } {
  const result: Record<string, unknown> = {};
  const errors: string[] = [];
  for (const variable of variables) {
    const raw = values[variable.name] ?? '';
    if (raw === '' && variable.type !== 'string') continue;
    if (variable.type === 'string') result[variable.name] = raw;
    else if (variable.type === 'number' || variable.type === 'integer') {
      const parsed = Number(raw);
      if (raw.trim() === '' || Number.isNaN(parsed) || (variable.type === 'integer' && !Number.isInteger(parsed))) {
        errors.push(`${variable.name} must be ${variable.type === 'integer' ? 'a whole number' : 'a number'}.`);
      } else result[variable.name] = parsed;
    } else if (variable.type === 'boolean') {
      if (raw !== 'true' && raw !== 'false') errors.push(`${variable.name} must be true or false.`);
      else result[variable.name] = raw === 'true';
    } else {
      try {
        const parsed: unknown = JSON.parse(raw);
        const ok = variable.type === 'array' ? Array.isArray(parsed) : isRecord(parsed);
        if (ok) result[variable.name] = parsed;
        else errors.push(`${variable.name} must be a JSON ${variable.type}.`);
      } catch {
        errors.push(`${variable.name} must be valid JSON.`);
      }
    }
  }
  return { values: result, errors };
}

/** Labels in display order: production, then staging, then the others by name. */
export function sortLabels(labels: PromptLabel[]): PromptLabel[] {
  const rank = (label: string) => (label === PRODUCTION_LABEL ? 0 : label === STAGING_LABEL ? 1 : 2);
  return [...labels].sort((a, b) => rank(a.label) - rank(b.label) || a.label.localeCompare(b.label));
}

export function labelsForVersion(labels: PromptLabel[], version: number): string[] {
  return sortLabels(labels).filter((label) => label.version === version).map((label) => label.label);
}

export function bindingLabel(binding: Pick<PromptBinding, 'label'>): string {
  return binding.label?.trim() || PRODUCTION_LABEL;
}

export function bindingsForLabel(bindings: PromptBinding[], label: string): PromptBinding[] {
  return bindings.filter((binding) => bindingLabel(binding) === label);
}

export function latestVersion(versions: PromptVersion[]): PromptVersion | null {
  return versions.reduce<PromptVersion | null>((latest, item) => (!latest || item.version > latest.version ? item : latest), null);
}

export function resolveReference(reference: PromptReference, versions: PromptVersion[], labels: PromptLabel[]): PromptVersion | null {
  const number = 'label' in reference
    ? labels.find((item) => item.label === reference.label)?.version
    : reference.version;
  return versions.find((item) => item.version === number) ?? null;
}

export function referenceValue(reference: PromptReference): string {
  return 'label' in reference ? `label:${reference.label}` : `version:${reference.version}`;
}

export function parseReferenceValue(value: string): PromptReference {
  if (value.startsWith('label:')) return { label: value.slice('label:'.length) };
  return { version: Number(value.slice('version:'.length)) };
}

/** Label names may not be empty or contain a slash, because the delete route puts them in the path. */
export function labelNameError(name: string, existing: string[]): string | null {
  const value = name.trim();
  if (!value) return 'Enter a label name.';
  if (value.includes('/')) return 'A label name cannot contain "/".';
  if (existing.includes(value)) return `${value} already exists. Change its version instead.`;
  return null;
}

function tokenize(text: string): string[] {
  return text.match(/\s+|[^\s]+/g) ?? [];
}

function lcsOps<T>(a: T[], b: T[]): Array<{ kind: DiffSegment['kind']; item: T }> {
  const n = a.length;
  const m = b.length;
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i * width + j] = a[i] === b[j]
        ? table[(i + 1) * width + j + 1] + 1
        : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const ops: Array<{ kind: DiffSegment['kind']; item: T }> = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ kind: 'same', item: a[i] });
      i += 1;
      j += 1;
    } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) {
      ops.push({ kind: 'removed', item: a[i] });
      i += 1;
    } else {
      ops.push({ kind: 'added', item: b[j] });
      j += 1;
    }
  }
  while (i < n) ops.push({ kind: 'removed', item: a[i++] });
  while (j < m) ops.push({ kind: 'added', item: b[j++] });
  return ops;
}

function pushSegment(segments: DiffSegment[], kind: DiffSegment['kind'], text: string): void {
  if (!text) return;
  const last = segments[segments.length - 1];
  if (last && last.kind === kind) last.text += text;
  else segments.push({ kind, text });
}

// Bounds the LCS table so a very long prompt cannot freeze the page.
const MAX_LCS_CELLS = 400_000;

function wordDiff(before: string, after: string, segments: DiffSegment[]): void {
  const a = tokenize(before);
  const b = tokenize(after);
  if (a.length * b.length > MAX_LCS_CELLS) {
    pushSegment(segments, 'removed', before);
    pushSegment(segments, 'added', after);
    return;
  }
  for (const op of lcsOps(a, b)) pushSegment(segments, op.kind, op.item);
}

/** Line diff first, then a word diff inside each changed block. */
export function diffText(before: string, after: string): DiffSegment[] {
  const segments: DiffSegment[] = [];
  if (before === after) {
    pushSegment(segments, 'same', after);
    return segments;
  }
  const a = before.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const b = after.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  if (a.length * b.length > MAX_LCS_CELLS) {
    pushSegment(segments, 'removed', before);
    pushSegment(segments, 'added', after);
    return segments;
  }
  let removed = '';
  let added = '';
  const flush = () => {
    if (removed && added) wordDiff(removed, added, segments);
    else {
      pushSegment(segments, 'removed', removed);
      pushSegment(segments, 'added', added);
    }
    removed = '';
    added = '';
  };
  for (const op of lcsOps(a, b)) {
    if (op.kind === 'same') {
      flush();
      pushSegment(segments, 'same', op.item);
    } else if (op.kind === 'removed') removed += op.item;
    else added += op.item;
  }
  flush();
  return segments;
}

export type TextSpan = { text: string; variable: boolean };

/** Splits text into plain runs and `{placeholder}` runs, using the same rules as detectPlaceholders. */
export function placeholderSpans(text: string): TextSpan[] {
  const spans: TextSpan[] = [];
  let plain = '';
  let index = 0;
  while (index < text.length) {
    if (text[index] === '{' && text[index + 1] === '{') {
      plain += '{{';
      index += 2;
      continue;
    }
    if (text[index] === '{') {
      const end = text.indexOf('}', index + 1);
      const root = end === -1 ? '' : text.slice(index + 1, end).split(/[.[!:]/, 1)[0].trim();
      if (end !== -1 && /^[A-Za-z_][A-Za-z0-9_]*$/.test(root)) {
        if (plain) spans.push({ text: plain, variable: false });
        plain = '';
        spans.push({ text: text.slice(index, end + 1), variable: true });
        index = end + 1;
        continue;
      }
    }
    plain += text[index];
    index += 1;
  }
  if (plain) spans.push({ text: plain, variable: false });
  return spans;
}
