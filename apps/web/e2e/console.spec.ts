import { expect, test, type Page } from '@playwright/test';

const FLOORS = ['Ground', 'Level 1', 'Level 2', 'Roof'];
/** Floor captures land beside the Playwright artifacts, which CI uploads. */
const CAPTURES = 'test-results/floors';
/** Draw calls with every asset and layer on screen; the budget that keeps 50 fps reachable. */
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
    // Clicking the selected asset again selects the one behind it.
    let picked: string | null = null;
    for (let click = 0; click < 3 && picked !== path; click++) {
      await page.mouse.click(at.x, at.y);
      picked = await page.locator('.inspector h2').getAttribute('title');
      if (click === 1 && picked === path) behind.push(path);
    }
    if (picked !== path) missed.push(`${path}: picked ${picked}`);
  }
  console.log(`${behind.length} assets needed a second click: ${behind.join(', ')}`);
  expect(missed).toEqual([]);
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
