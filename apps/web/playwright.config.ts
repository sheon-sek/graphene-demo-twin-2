import {defineConfig,devices} from '@playwright/test';

export default defineConfig({
  testDir:'./e2e',
  timeout:30_000,
  fullyParallel:false,
  reporter:[['line']],
  use:{
    ...devices['Desktop Chrome'],
    baseURL:'http://127.0.0.1:8000',
    viewport:{width:1440,height:900},
    trace:'retain-on-failure',
    screenshot:'only-on-failure',
  },
  webServer:{
    command:'python -m uvicorn graphene_demo_twin.admin.app:app --host 127.0.0.1 --port 8000',
    url:'http://127.0.0.1:8000/api/health',
    reuseExistingServer:true,
    timeout:30_000,
  },
});
