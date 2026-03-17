import type { ComponentType } from 'react';
import { Routes, Route, Link, useParams } from 'react-router-dom';
import { mockupComponents } from './.generated/mockup-components';

type MockupEntry = { folder: string; name: string; previewPath: string; module: Record<string, unknown> };

function PreviewPage() {
  const { folder, name } = useParams<{ folder?: string; name?: string }>();
  const resolvedName = name || '';
  const resolvedFolder = folder || '';
  const key = resolvedFolder ? `${resolvedFolder}/${resolvedName}` : resolvedName;
  const entry = (mockupComponents as unknown as MockupEntry[]).find(
    (e) => (e.folder ? `${e.folder}/${e.name}` : e.name) === key
  );
  if (!entry) {
    return (
      <div className="p-8">
        <p className="text-red-500 font-medium">Component not found: <code>{key}</code></p>
        <a href="/__mockup/" className="text-blue-600 text-sm mt-2 inline-block hover:underline">← Back to index</a>
      </div>
    );
  }
  const mod = entry.module as Record<string, ComponentType>;
  const Component = mod[resolvedName] || (mod['default'] as ComponentType) || (Object.values(mod)[0] as ComponentType);
  if (!Component) {
    return <div className="p-8 text-red-500">No export found in {key}</div>;
  }
  return <Component />;
}

export default function App() {
  return (
    <Routes>
      <Route
        path="/"
        element={
          <div className="min-h-screen bg-gray-50 p-8">
            <h1 className="text-2xl font-bold text-gray-900 mb-2">Mockup Sandbox</h1>
            <p className="text-sm text-gray-500 mb-6">Components in <code className="bg-gray-100 px-1 rounded">src/components/mockups/</code></p>
            {(mockupComponents as unknown as MockupEntry[]).length === 0 ? (
              <p className="text-gray-400 italic">No components found yet.</p>
            ) : (
              <ul className="space-y-2">
                {(mockupComponents as unknown as MockupEntry[]).map((e) => (
                  <li key={e.previewPath}>
                    <Link
                      to={e.previewPath}
                      className="text-blue-600 hover:underline text-sm"
                    >
                      {e.folder ? `${e.folder}/` : ''}{e.name}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        }
      />
      <Route path="/preview/:name" element={<PreviewPage />} />
      <Route path="/preview/:folder/:name" element={<PreviewPage />} />
    </Routes>
  );
}
