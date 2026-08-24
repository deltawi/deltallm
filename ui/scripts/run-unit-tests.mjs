import { spawn } from 'node:child_process';
import { mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const uiRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = path.join(uiRoot, 'node_modules', '.tmp', 'ui-unit-tests');
const testSources = [
  'tests/authorization.test.ts',
  'tests/branding.test.ts',
  'tests/brandingApi.test.ts',
  'tests/brandingComponents.test.ts',
  'tests/brandingProvider.test.ts',
  'tests/authRedirect.test.ts',
  'tests/authSession.test.ts',
  'tests/modelFormShared.test.ts',
  'tests/modelsApi.test.ts',
  'tests/namedCredentialsApi.test.ts',
  'tests/batchDetailResource.test.ts',
  'tests/organizationPolicy.test.ts',
  'tests/tierHelpers.test.ts',
  'tests/dashboardAnalytics.test.ts',
  'tests/format.test.ts',
  'tests/reportingRange.test.ts',
  'tests/reportingRangeControl.test.ts',
  'tests/reportingRequest.test.ts',
  'tests/reportingRefresh.test.ts',
  'tests/routeGroupsApi.test.ts',
  'tests/usageBreakdown.test.ts',
  'tests/useApi.test.ts',
];

await rm(outputDir, { recursive: true, force: true });
await mkdir(outputDir, { recursive: true });

await build({
  entryPoints: testSources.map((source) => path.join(uiRoot, source)),
  outdir: outputDir,
  entryNames: '[name]',
  bundle: true,
  external: ['jsdom', 'lucide-react', 'react', 'react-dom', 'react-dom/client', 'react-dom/server'],
  jsx: 'automatic',
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
