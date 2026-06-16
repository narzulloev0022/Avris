const { test, expect } = require('@playwright/test');

test('homepage loads', async ({ page }) => {
  await page.goto('https://theavris.ai');
  await expect(page).toHaveTitle(/Avris/);
});

test('login page visible', async ({ page }) => {
  await page.goto('https://theavris.ai');
  // exact match on the submit button — plain text=Войти matches 5 nodes (OAuth/link)
  await expect(page.getByRole('button', { name: 'Войти', exact: true })).toBeVisible();
});

test('styles.css loads', async ({ page }) => {
  const response = await page.goto('https://theavris.ai/styles.css');
  expect(response.status()).toBe(200);
});
