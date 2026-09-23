import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';

// The console is served by the twin itself: one `serve` process with the built `dist/`.
const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '../..');
const venv = resolve(repo, '.venv/bin/python');
const python = process.env.TWIN_PYTHON ?? (existsSync(venv) ? venv : 'python');
const port = Number(process.env.TWIN_HTTP_PORT ?? 18765);
const opcPort = Number(process.env.TWIN_OPC_PORT ?? 14865);

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['line']],
  outputDir: 'test-results/artifacts',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1600, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: {
      // WebGL through SwiftShader when the runner has no GPU.
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
    },
  },
  webServer: {
    command: `"${python}" -m graphene_demo_twin serve --http-port ${port} --opc-port ${opcPort} --console-dir "${resolve(here, 'dist')}"`,
    url: `http://127.0.0.1:${port}/api/status`,
    reuseExistingServer: false,
    timeout: 60_000,
    cwd: repo,
  },
});
