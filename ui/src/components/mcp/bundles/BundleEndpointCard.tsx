import { PanelCard } from '../../admin/shells';
import { CopyButton } from '../../batch-jobs/BatchBits';
import { bundleEndpointUrl, bundleToolSnippet } from '../../../lib/mcpBundles';

export default function BundleEndpointCard({ bundleKey }: { bundleKey: string }) {
  const key = bundleKey.trim().toLowerCase();
  const url = bundleEndpointUrl(key);
  const snippet = bundleToolSnippet(key);
  return (
    <PanelCard>
      <h2 className="text-sm font-semibold text-gray-900">Endpoint</h2>
      <div className="mt-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-gray-600">MCP client URL</span>
          <CopyButton value={url} label="Copy MCP client URL" />
        </div>
        <code className="mt-1 block break-all rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-800" data-testid="bundle-endpoint-url">{url}</code>
        <p className="mt-1 text-[11px] text-gray-400">Send the API key in the Authorization header.</p>
      </div>
      <div className="mt-4">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-gray-600">Chat completions and responses</span>
          <CopyButton value={snippet} label="Copy tools snippet" />
        </div>
        <pre className="mt-1 overflow-x-auto rounded-lg bg-gray-900 px-3 py-2 text-xs text-gray-100" data-testid="bundle-snippet">{snippet}</pre>
      </div>
    </PanelCard>
  );
}
