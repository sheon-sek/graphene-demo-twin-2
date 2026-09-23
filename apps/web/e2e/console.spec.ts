import { expect, test, type Page } from '@playwright/test';

const FLOORS = ['Ground', 'Level 1', 'Level 2', 'Roof'];
/** Floor captures land beside the Playwright artifacts, which CI uploads. */
const CAPTURES = 'test-results/floors';
/**
 * Draw calls with every asset and layer on screen; the budget that keeps 50 fps reachable. The
 * frame rate itself is measured by hand on a mid-range laptop with `?bench` (see README.md).
 */
const DRAW_CALL_BUDGET = 80;

interface PlacedAsset {
  path: string;
  room: string | null;
  x: number | null;
}

async function open(page: Page) {
  // `?e2e` makes camera moves and floor separation instant so captures are deterministic.
  await page.goto('/?e2e');
  await expect(page.locator('.stream.live')).toBeVisible({ timeout: 30_000 });
  await page.waitForFunction(() => (window.__twin?.perf.drawCalls ?? 0) > 0, null, {
    timeout: 30_000,
  });
}

async function settle(page: Page) {
  await page.waitForFunction(() => window.__twin?.settled(), null, { timeout: 10_000 });
  // Two frames so the moved camera and instance matrices are on screen.
  await page.evaluate(
    () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
  );
}

async function placedAssets(page: Page): Promise<PlacedAsset[]> {
  const design = await (await page.request.get('/api/plant-design')).json();
  return (design.assets as PlacedAsset[]).filter((a) => a.room !== null && a.x !== null);
}

async function clickInScene(page: Page, node: string) {
  const at = await page.evaluate((n) => window.__twin!.screenOf(n), node);
  expect(at, `${node} on screen`).not.toBeNull();
  await page.mouse.click(at!.x, at!.y);
}

async function capture(page: Page, name: string) {
  const body = await page.screenshot({ path: `${CAPTURES}/${name}.png` });
  await test.info().attach(name, { body, contentType: 'image/png' });
}

test('captures each floor of the building', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: 'Site', exact: true }).click();
  await settle(page);
  await capture(page, 'site');
  const perf = await page.evaluate(() => window.__twin!.perf);
  console.log(`site, every asset and layer on screen: ${JSON.stringify(perf)}`);
  expect(perf.drawCalls).toBeLessThanOrEqual(DRAW_CALL_BUDGET);

  for (const floor of FLOORS) {
    await page.getByRole('group', { name: 'Floors' }).getByRole('button', { name: floor }).click();
    await settle(page);
    await capture(page, floor.replace(' ', '-'));
  }
});

test('an asset picked in 3D is selected in the tree and the inspector', async ({ page }) => {
  await open(page);
  await page.evaluate(() => window.__twin!.locate('Chiller/R_C2'));
  await page.keyboard.press('Escape');
  await settle(page);
  await clickInScene(page, 'Chiller/R_C2');
  await expect(page.locator('.inspector h2')).toHaveText('Chiller/R_C2');
  const row = page.getByRole('treeitem', { name: 'R_C2' });
  await expect(row).toHaveAttribute('aria-selected', 'true');
  await expect(row).toBeInViewport();
});

test('an asset chosen in the tree is selected and framed in 3D', async ({ page }) => {
  await open(page);
  await page.getByRole('treeitem', { name: 'UPS', exact: true }).click();
  const asset = page.locator('.tree-row.asset').first();
  const name = await asset.getAttribute('data-path');
  await asset.click();
  await settle(page);
  await expect(page.locator('.inspector h2')).toHaveText(name!);
  const at = await page.evaluate((n) => window.__twin!.screenOf(n), name!);
  const stage = (await page.getByTestId('stage').boundingBox())!;
  expect(Math.abs(at!.x - (stage.x + stage.width / 2))).toBeLessThan(40);
  expect(Math.abs(at!.y - (stage.y + stage.height / 2))).toBeLessThan(60);
});

test('every placed asset is pickable in 3D', async ({ page }) => {
  // One camera move and a click or two per asset: minutes on a software renderer.
  test.setTimeout(900_000);
  await open(page);
  const missed: string[] = [];
  const behind: string[] = [];
  for (const { path } of await placedAssets(page)) {
    await page.evaluate((n) => window.__twin!.locate(n), path);
    await page.keyboard.press('Escape');
    await settle(page);
    const at = await page.evaluate((n) => window.__twin!.screenOf(n), path);
    if (!at) {
      missed.push(`${path}: off screen`);
      continue;
    }
    // Clicking the selected asset again selects the one behind it. Assets the Plant Design
    // puts on the same spot stay there, so reaching one can take a few clicks.
    let picked: string | null = null;
    for (let click = 0; click < 6 && picked !== path; click++) {
      await page.mouse.click(at.x, at.y);
      picked = await page.locator('.inspector h2').getAttribute('title');
      if (click > 0 && picked === path) behind.push(path);
    }
    if (picked !== path) missed.push(`${path}: picked ${picked}`);
  }
  console.log(`${behind.length} assets needed more than one click: ${behind.join(', ')}`);
  expect(missed).toEqual([]);
});

test('double-click flies to the asset under the pointer, not the one behind it', async ({
  page,
}) => {
  // Three leak cable sensors share one spot in the Ground water plant room.
  const spot = 'Water Leak Detection System/Ground/1A';
  await open(page);
  await page.evaluate((n) => window.__twin!.locate(n), spot);
  await page.keyboard.press('Escape');
  await settle(page);
  const at = (await page.evaluate((n) => window.__twin!.screenOf(n), spot))!;
  await page.mouse.click(at.x, at.y);
  const front = await page.locator('.inspector h2').getAttribute('title');
  await page.mouse.click(at.x, at.y);
  const behind = await page.locator('.inspector h2').getAttribute('title');
  expect(behind, 'the spot holds overlapping assets').not.toBe(front);

  await page.keyboard.press('Escape');
  await page.mouse.dblclick(at.x, at.y);
  await settle(page);
  await expect(page.locator('.inspector h2')).toHaveAttribute('title', front!);
});

test('benchmark mode reports the sustained frame rate with everything on screen', async ({
  page,
}) => {
  await page.goto('/?e2e&bench&warmup=1&window=2');
  const report = page.getByTestId('bench');
  await expect(report).toHaveAttribute('data-phase', 'done', { timeout: 60_000 });
  console.log(await report.textContent());
  const median = Number(await report.getAttribute('data-median-fps'));
  const p5 = Number(await report.getAttribute('data-p5-fps'));
  expect(p5).toBeGreaterThan(0);
  expect(median).toBeGreaterThanOrEqual(p5);
  const perf = await page.evaluate(() => window.__twin!.perf);
  expect(perf.drawCalls).toBeLessThanOrEqual(DRAW_CALL_BUDGET);
});

test('search locates an Unexported Asset by its Plant View path', async ({ page }) => {
  await open(page);
  const box = page.getByRole('searchbox');
  await box.fill('Chiller_System/Chillers/CH-004');
  await box.press('Enter');
  await expect(page.locator('.inspector h2')).toHaveText('CH-004');
  await expect(page.locator('.inspector .tag.unexported')).toBeVisible();
  await expect(page.getByRole('treeitem', { name: 'CH-004' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('the Points tab streams live values with quality, time and sparkline', async ({ page }) => {
  await open(page);
  await page.getByRole('searchbox').fill('Temperature and Humidity');
  await page.getByRole('option').first().click();
  const temp = page.getByRole('row', { name: 'Temp', exact: true });
  await expect(temp.locator('.quality')).toHaveText('good');
  const before = await temp.locator('.time').textContent();
  await expect(temp.locator('.time')).not.toHaveText(before!, { timeout: 5_000 });
  await expect
    .poll(async () => (await temp.locator('svg path').getAttribute('d'))?.includes('L'))
    .toBe(true);
});

test('a fault is previewed, injected on the chosen asset, marked and cleared', async ({ page }) => {
  await open(page);
  const box = page.getByRole('searchbox');
  await box.fill('CRAC/L1_CRAC3');
  await box.press('Enter');
  await expect(page.locator('.inspector h2')).toHaveText('CRAC/L1_CRAC3');

  await page.getByRole('tab', { name: 'Faults' }).click();
  await page.getByRole('radio', { name: /Compressor trip/ }).click();
  await page.getByRole('button', { name: 'Preview 15 min' }).click();
  const preview = page.getByRole('region', { name: 'Fault Preview' });
  const affected = preview.getByRole('list', { name: 'Affected assets' }).getByRole('listitem');
  await expect(affected.first()).toContainText('L1_CRAC3');
  await expect(affected.nth(1)).toContainText('DH03');
  await expect(preview.getByRole('list', { name: 'Alarm changes' })).toContainText(
    'System Failure_Trip',
  );

  await page.getByRole('button', { name: 'Inject' }).click();
  const active = page.getByRole('region', { name: 'Active faults', exact: true });
  await expect(active).toContainText('Compressor trip · L1_CRAC3');
  await expect(page.getByRole('region', { name: 'Event Log' })).toContainText('fault.inject');
  await expect(page.getByRole('region', { name: 'Alarms' })).toContainText(
    'L1_CRAC3 · System Failure_Trip',
  );
  // The chosen CRAC, never the first one of its type (v1 #7).
  const faults = await (await page.request.get('/api/faults')).json();
  expect(faults.faults.map((f: { target: string }) => f.target)).toEqual(['CRAC/L1_CRAC3']);
  await settle(page);
  await capture(page, 'fault-injected');

  await active.getByRole('button', { name: 'Clear Compressor trip on CRAC/L1_CRAC3' }).click();
  await expect(active).toContainText('None');
  await expect(page.getByRole('region', { name: 'Event Log' })).toContainText('fault.clear');
});

test('Operator Commands and Reset go through the Event Log', async ({ page }) => {
  // Tests share one twin: start from an empty Event Log.
  await page.request.post('/api/reset', { data: { confirm: true } });
  await open(page);
  const box = page.getByRole('searchbox');
  await box.fill('CRAC/L1_CRAC1');
  await box.press('Enter');
  await page.getByRole('tab', { name: 'Control' }).click();
  const mode = page.getByRole('group', { name: 'Hand / auto' });
  await mode.getByRole('button', { name: 'hand' }).click();
  await expect(mode.getByRole('button', { name: 'hand' })).toHaveAttribute('aria-pressed', 'true');
  const run = page.getByRole('group', { name: 'Start / stop (hand)' });
  await run.getByRole('button', { name: 'Stop' }).click();
  await page.getByRole('tab', { name: 'Points' }).click();
  await expect(page.getByRole('row', { name: 'On_Off', exact: true }).locator('.value')).toHaveText(
    '0',
  );
  const log = page.getByRole('region', { name: 'Event Log' });
  await expect(log.locator('li')).toHaveCount(2);

  await page.getByRole('button', { name: 'Reset…' }).click();
  await expect(page.getByRole('alertdialog')).toContainText('2 events');
  await page.getByRole('button', { name: 'Confirm reset' }).click();
  await expect(log.locator('li')).toHaveCount(0);
  await expect(page.getByRole('row', { name: 'On_Off', exact: true }).locator('.value')).toHaveText(
    '1',
  );
});
