import { useEffect, useMemo, useState } from 'react';
import { mcpBundles, type MCPBundlePreview, type MCPBundlePreviewInput, type MCPBundleServerTools } from '../../../lib/api';
import { errorMessage } from '../../../lib/errorMessage';
import { previewMemberInputs, type BundleDraft } from '../../../lib/mcpBundles';

export const PREVIEW_DELAY_MS = 400;

export type BundlePreviewState = {
  /** Tool lists of every server that a preview returned, also after the server left the draft. */
  serverTools: Readonly<Record<string, MCPBundleServerTools>>;
  preview: MCPBundlePreview | null;
  loading: boolean;
  error: string | null;
};

/**
 * Read the preview of the draft after a short pause in typing. The preview
 * gives the tool lists of the servers, the save problems and the key result.
 */
export function useBundlePreview(draft: BundleDraft, bundleId: string | null, apiKey: string | null): BundlePreviewState {
  const [serverTools, setServerTools] = useState<Record<string, MCPBundleServerTools>>({});
  const [result, setResult] = useState<{ key: string; preview: MCPBundlePreview | null; error: string | null } | null>(null);

  const ownerMissing = !bundleId && draft.owner_scope_type !== 'global' && !draft.owner_scope_id;
  const payload = useMemo<MCPBundlePreviewInput | null>(() => {
    const members = previewMemberInputs(draft.members);
    if (ownerMissing || (members.length === 0 && !apiKey)) return null;
    const base: MCPBundlePreviewInput = bundleId
      ? { mcp_bundle_id: bundleId, members }
      : { owner_scope_type: draft.owner_scope_type, owner_scope_id: draft.owner_scope_id || null, members };
    return apiKey ? { ...base, api_key: apiKey } : base;
  }, [draft.members, draft.owner_scope_type, draft.owner_scope_id, bundleId, apiKey, ownerMissing]);
  const requestKey = payload ? JSON.stringify(payload) : null;

  useEffect(() => {
    if (!requestKey) return undefined;
    const body = JSON.parse(requestKey) as MCPBundlePreviewInput;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      mcpBundles.preview(body, controller.signal)
        .then((preview) => {
          if (controller.signal.aborted) return;
          setResult({ key: requestKey, preview, error: null });
          setServerTools((current) => {
            const next = { ...current };
            for (const server of preview.servers) next[server.mcp_server_id] = server;
            return next;
          });
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return;
          setResult({ key: requestKey, preview: null, error: errorMessage(reason, 'Could not load the preview.') });
        });
    }, PREVIEW_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [requestKey]);

  if (!requestKey) return { serverTools, preview: null, loading: false, error: null };
  const current = result?.key === requestKey ? result : null;
  // Keep the last preview on screen while the next one loads.
  return {
    serverTools,
    preview: current ? current.preview : result?.preview ?? null,
    loading: current === null,
    error: current?.error ?? null,
  };
}
