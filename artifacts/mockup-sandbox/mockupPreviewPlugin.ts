import type { Plugin, ViteDevServer } from 'vite';
import fg from 'fast-glob';
import fs from 'node:fs';
import path from 'node:path';

const MOCKUPS_DIR = 'src/components/mockups';
const GENERATED_FILE = 'src/.generated/mockup-components.ts';

interface MockupEntry {
  folder: string;
  name: string;
  importPath: string;
  previewPath: string;
}

function scanMockups(root: string): MockupEntry[] {
  const pattern = `${MOCKUPS_DIR}/**/*.tsx`;
  const files = fg.sync(pattern, { cwd: root, ignore: ['**/_*', '**/_*/**'] });
  const entries: MockupEntry[] = [];

  for (const file of files) {
    const rel = path.relative(MOCKUPS_DIR, file);
    const parts = rel.replace(/\.tsx$/, '').split('/');

    if (parts.length === 1) {
      const name = parts[0];
      if (name.startsWith('_')) continue;
      entries.push({
        folder: '',
        name,
        importPath: `../components/mockups/${name}`,
        previewPath: `/preview/${name}`,
      });
    } else {
      const folder = parts.slice(0, -1).join('/');
      const name = parts[parts.length - 1];
      if (name.startsWith('_') || folder.split('/').some((p) => p.startsWith('_'))) continue;
      entries.push({
        folder,
        name,
        importPath: `../components/mockups/${folder}/${name}`,
        previewPath: `/preview/${folder}/${name}`,
      });
    }
  }

  return entries;
}

function writeRegistry(root: string, entries: MockupEntry[]) {
  const genDir = path.join(root, 'src/.generated');
  if (!fs.existsSync(genDir)) fs.mkdirSync(genDir, { recursive: true });

  const imports = entries
    .map((e, i) => `import * as M${i} from "${e.importPath}";`)
    .join('\n');

  const registrations = entries
    .map((e, i) => `  { folder: ${JSON.stringify(e.folder)}, name: ${JSON.stringify(e.name)}, previewPath: ${JSON.stringify(e.previewPath)}, module: M${i} },`)
    .join('\n');

  const content = `// AUTO-GENERATED — do not edit\n${imports}\n\nexport const mockupComponents = [\n${registrations}\n] as const;\n`;

  fs.writeFileSync(path.join(root, GENERATED_FILE), content, 'utf-8');
}

export function mockupPreviewPlugin(): Plugin {
  let root = '';
  let server: ViteDevServer | null = null;

  function refresh() {
    try {
      const entries = scanMockups(root);
      writeRegistry(root, entries);
      if (server) {
        const mod = server.moduleGraph.getModulesByFile(
          path.join(root, GENERATED_FILE)
        );
        if (mod) {
          mod.forEach((m) => server!.moduleGraph.invalidateModule(m));
          server.ws.send({ type: 'full-reload' });
        }
      }
    } catch {
      // ignore scan errors
    }
  }

  return {
    name: 'mockup-preview-plugin',
    configResolved(config) {
      root = config.root;
    },
    buildStart() {
      const entries = scanMockups(root);
      writeRegistry(root, entries);
    },
    configureServer(s) {
      server = s;
      refresh();

      s.watcher.add(path.join(root, MOCKUPS_DIR));
      s.watcher.on('add', (f) => { if (f.includes('/mockups/')) refresh(); });
      s.watcher.on('unlink', (f) => { if (f.includes('/mockups/')) refresh(); });

      s.middlewares.use((req, _res, next) => {
        if (req.url?.startsWith('/preview/')) {
          refresh();
          req.url = '/';
        }
        next();
      });
    },
  };
}
