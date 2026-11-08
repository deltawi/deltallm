import { spawn } from 'node:child_process';
import { mkdir, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const uiRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = path.join(uiRoot, 'node_modules', '.tmp', 'ui-unit-tests');
// Every tests/*.test.ts or *.test.tsx file runs, so a new test file cannot be
// left out by mistake.
const testSources = (await readdir(path.join(uiRoot, 'tests')))
  .filter((name) => /\.test\.tsx?$/.test(name))
  .sort()
  .map((name) => `tests/${name}`);

if (testSources.length === 0) {
  throw new Error('No UI unit tests found under tests/');
}

await rm(outputDir, { recursive: true, force: true });
await mkdir(outputDir, { recursive: true });

await build({
  entryPoints: testSources.map((source) => path.join(uiRoot, source)),
  outdir: outputDir,
  entryNames: '[name]',
  bundle: true,
  external: [
    'jsdom',
    'lucide-react',
    'react',
    'react-dom',
    'react-dom/client',
    'react-dom/server',
    'react-router-dom',
    'recharts',
  ],
  jsx: 'automatic',
  loader: { '.svg': 'dataurl' },
  platform: 'node',
  format: 'esm',
});

const compiledTests = testSources.map((source) => path.join(
  outputDir,
  `${path.basename(source, path.extname(source))}.js`,
));

const child = spawn(process.execPath, ['--test', ...compiledTests], {
  cwd: uiRoot,
  stdio: 'inherit',
});

child.once('error', (error) => {
  console.error(error);
  process.exitCode = 1;
});

child.once('exit', (code, signal) => {
  if (signal) {
    console.error(`Unit test process terminated by ${signal}`);
    process.exitCode = 1;
    return;
  }
  process.exitCode = code ?? 1;
});
