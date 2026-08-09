import { spawn } from 'node:child_process';
import { mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const uiRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = path.join(uiRoot, 'node_modules', '.tmp', 'ui-unit-tests');
const testSources = [
  'tests/modelFormShared.test.ts',
  'tests/batchDetailResource.test.ts',
  'tests/dashboardRange.test.ts',
];

await rm(outputDir, { recursive: true, force: true });
await mkdir(outputDir, { recursive: true });

await build({
  entryPoints: testSources.map((source) => path.join(uiRoot, source)),
  outdir: outputDir,
  entryNames: '[name]',
  bundle: true,
  platform: 'node',
  format: 'esm',
});

const compiledTests = testSources.map((source) => path.join(
  outputDir,
  `${path.basename(source, '.ts')}.js`,
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
