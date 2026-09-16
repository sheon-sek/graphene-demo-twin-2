import {test,expect} from '@playwright/test';

test('overview renders runtime header', async ({page})=>{
  await page.goto('/');
  await expect(page.getByText('Graphene')).toBeVisible();
  await expect(page.getByText('Facility condition at a glance')).toBeVisible();
});

test('runtime controls are reachable', async ({page})=>{
  await page.goto('/');
  await page.getByRole('button',{name:'Runtime'}).click();
  await expect(page.getByRole('button',{name:'Start'})).toBeVisible();
});
