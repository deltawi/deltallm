import { apiFetch, withQuery } from './transport';
import type { Paginated } from './pagination';

export interface PromptTemplate {
  prompt_template_id: string;
  template_key: string;
  name: string;
  description: string | null;
  owner_scope: string | null;
  metadata: Record<string, unknown> | null;
  version_count: number;
  label_count: number;
  binding_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PromptVersion {
  prompt_version_id: string;
  prompt_template_id: string;
  template_key: string;
  version: number;
  status: string;
  template_body: Record<string, unknown>;
  variables_schema: Record<string, unknown> | null;
  model_hints: Record<string, unknown> | null;
  route_preferences: Record<string, unknown> | null;
  published_at?: string | null;
  published_by?: string | null;
  archived_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PromptLabel {
  prompt_label_id: string;
  prompt_template_id: string;
  template_key: string;
  label: string;
  prompt_version_id: string;
  version: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PromptBinding {
  prompt_binding_id: string;
  scope_type: 'key' | 'team' | 'org' | 'group';
  scope_id: string;
  prompt_template_id: string;
  template_key: string;
  label: string;
  priority: number;
  enabled: boolean;
  metadata: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PromptResolutionCandidate {
  template_key?: string;
  [key: string]: unknown;
}

export const promptRegistry = {
  listTemplates: (params?: { search?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    apiFetch<Paginated<PromptTemplate>>(withQuery('/ui/api/prompt-registry/templates', params), { signal }),
  getTemplate: (templateKey: string, signal?: AbortSignal) =>
    apiFetch<{ template: PromptTemplate; versions: PromptVersion[]; labels: PromptLabel[]; bindings: PromptBinding[] }>(
      `/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}`, { signal }
    ),
  createTemplate: (payload: object) =>
    apiFetch<PromptTemplate>('/ui/api/prompt-registry/templates', { method: 'POST', json: payload }),
  updateTemplate: (templateKey: string, payload: object) =>
    apiFetch<PromptTemplate>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}`, { method: 'PUT', json: payload }),
  deleteTemplate: (templateKey: string) =>
    apiFetch<{ deleted: boolean }>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}`, { method: 'DELETE' }),
  createVersion: (templateKey: string, payload: object) =>
    apiFetch<PromptVersion>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/versions`, { method: 'POST', json: payload }),
  publishVersion: (templateKey: string, version: number) =>
    apiFetch<PromptVersion>(
      `/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/versions/${encodeURIComponent(String(version))}/publish`,
      { method: 'POST' }
    ),
  listLabels: (templateKey: string) =>
    apiFetch<PromptLabel[]>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/labels`),
  assignLabel: (templateKey: string, payload: object) =>
    apiFetch<PromptLabel>(`/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/labels`, { method: 'POST', json: payload }),
  deleteLabel: (templateKey: string, label: string) =>
    apiFetch<{ deleted: boolean }>(
      `/ui/api/prompt-registry/templates/${encodeURIComponent(templateKey)}/labels/${encodeURIComponent(label)}`,
      { method: 'DELETE' }
    ),
  listBindings: (params?: { scope_type?: string; scope_id?: string; template_key?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    apiFetch<Paginated<PromptBinding>>(withQuery('/ui/api/prompt-registry/bindings', params), { signal }),
  upsertBinding: (payload: object, signal?: AbortSignal) =>
    apiFetch<PromptBinding>('/ui/api/prompt-registry/bindings', { method: 'POST', json: payload, signal }),
  deleteBinding: (bindingId: string, signal?: AbortSignal) =>
    apiFetch<{ deleted: boolean }>(`/ui/api/prompt-registry/bindings/${encodeURIComponent(bindingId)}`, { method: 'DELETE', signal }),
  dryRunRender: (payload: object) =>
    apiFetch<Record<string, unknown>>('/ui/api/prompt-registry/render', { method: 'POST', json: payload }),
  previewResolution: (payload: object, signal?: AbortSignal) =>
    apiFetch<{
      winner: PromptResolutionCandidate | null;
      candidates: PromptResolutionCandidate[];
    }>('/ui/api/prompt-registry/preview-resolution', { method: 'POST', json: payload, signal }),
};
