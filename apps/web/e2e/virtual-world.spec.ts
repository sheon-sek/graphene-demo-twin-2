import {expect,test} from '@playwright/test';

test('virtual world renders semantic scene controls and camera presets',async({page})=>{
  test.setTimeout(45_000);
  const pageErrors:string[]=[];
  page.on('pageerror',error=>pageErrors.push(error.message));

  await page.goto('/');
  await page.getByRole('button',{name:'Virtual World'}).click();

  const canvas=page.locator('.worldCanvas canvas');
  await expect(canvas).toBeVisible();
  await expect(page.locator('.worldToolbar')).toBeVisible();
  await expect(page.locator('.worldHud')).toContainText('assets');
  await expect(page.getByRole('button',{name:'Overview',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Cooling',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Environment',exact:true})).toHaveAttribute('aria-pressed','false');

  const size=await canvas.evaluate(el=>({width:(el as HTMLCanvasElement).width,height:(el as HTMLCanvasElement).height}));
  expect(size.width).toBeGreaterThan(400);
  expect(size.height).toBeGreaterThan(300);
  await page.waitForTimeout(600);
  await page.locator('.canvasCard').screenshot({path:'test-results/virtual-world-overview.png'});

  await page.getByRole('button',{name:'Cooling',exact:true}).click();
  await page.waitForTimeout(800);
  await page.locator('.canvasCard').screenshot({path:'test-results/virtual-world-cooling.png'});

  const environment=page.getByRole('button',{name:'Environment',exact:true});
  await environment.click();
  await expect(environment).toHaveAttribute('aria-pressed','true');
  expect(pageErrors).toEqual([]);
});
