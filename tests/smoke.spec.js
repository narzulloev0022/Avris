const { test, expect } = require('@playwright/test');

test('root responds with an Avris page', async ({ page }) => {
  await page.goto('https://theavris.ai');
  await expect(page).toHaveTitle(/Avris/);
});

test('app login lives at /app', async ({ page }) => {
  await page.goto('https://theavris.ai/app');
  // exact match on the submit button — plain text=Войти matches 5 nodes (OAuth/link)
  await expect(page.getByRole('button', { name: 'Войти', exact: true })).toBeVisible();
});

test('styles.css loads', async ({ page }) => {
  const response = await page.goto('https://theavris.ai/styles.css');
  expect(response.status()).toBe(200);
});

test('waitlist api rejects garbage email', async ({ request }) => {
  const res = await request.post('https://theavris.ai/api/waitlist', {
    data: { email: 'not-an-email', role: 'doctor', lang: 'ru', website: '' },
  });
  expect(res.status()).toBe(422);
});
