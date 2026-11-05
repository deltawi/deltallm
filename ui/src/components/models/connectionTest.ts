import type { ModelConnectionTestPayload } from '../../lib/api';
import type { ModelFormValues } from '../modelFormShared';
import type { RequestShapingForm } from './requestShaping';

export interface ConnectionTestContext {
  /** Set when editing: blank fields then fall back to the saved model's settings. */
  deploymentId?: string | null;
  allowInlineCredentials: boolean;
  supportsCustomAuth: boolean;
}

function trimmed(value: string): string | null {
  return value.trim() || null;
}

/** Why the form cannot be tested yet, or null when it can. */
export function connectionTestBlocker(form: ModelFormValues, context: ConnectionTestContext): string | null {
  if (!form.provider.trim()) return 'Choose a provider first.';
  const named = !context.allowInlineCredentials || form.credential_source === 'named';
  if (named && !form.keep_existing_named_credential && !form.named_credential_id.trim()) {
    return 'Choose a named credential first.';
  }
  return null;
}

export function buildConnectionTestPayload(
  form: ModelFormValues,
  shaping: RequestShapingForm,
  context: ConnectionTestContext,
): ModelConnectionTestPayload {
  const inline = context.allowInlineCredentials && form.credential_source === 'inline';
  const customAuth = inline && context.supportsCustomAuth;
  // A key marked for clearing must not be tested with the stored one.
  const useSaved = Boolean(context.deploymentId) && !(inline && form.clear_inline_api_key);
  return {
    provider: form.provider.trim(),
    mode: form.mode,
    model: trimmed(form.model),
    named_credential_id: !inline && !form.keep_existing_named_credential ? trimmed(form.named_credential_id) : null,
    api_key: inline ? trimmed(form.api_key) : null,
    api_base: inline ? trimmed(form.api_base) : null,
    api_version: inline ? trimmed(form.api_version) : null,
    auth_header_name: customAuth ? trimmed(form.auth_header_name) : null,
    auth_header_format: customAuth ? trimmed(form.auth_header_format) : null,
    outbound_proxy: trimmed(shaping.outboundProxy),
    deployment_id: useSaved ? context.deploymentId || null : null,
  };
}

/** A result belongs to the exact settings it tested; any edit hides it. */
export function connectionTestKey(payload: ModelConnectionTestPayload): string {
  return JSON.stringify(payload);
}
