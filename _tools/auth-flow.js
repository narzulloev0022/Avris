// Day 2 auth-flow integration test
// Tests register → dashboard, logout → login, login pane navigation
const puppeteer = require('puppeteer');
const path = require('path');

const FRONTEND = 'http://localhost:8080/index.html';
const OUT = path.resolve(__dirname, '..', 'screenshots');
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function shoot(page, name) {
  await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: false });
  console.log('  →', name);
}

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => console.error('JS ERROR:', e.message));

  // 1. Initial load → login pane visible
  await page.goto(FRONTEND, { waitUntil: 'networkidle0' });
  await sleep(500);
  const loginVisible = await page.$eval('#loginScreen', el => !el.classList.contains('hidden'));
  const oauthBtns = await page.$$eval('.oauth-btn', els => els.length);
  const orDivider = await page.$('.login-or') !== null;
  console.log(`Login pane shown: ${loginVisible}; OAuth buttons: ${oauthBtns}; "or" divider: ${orDivider}`);
  await shoot(page, 'auth-01-signin');

  // 2. Click "Forgot password" → forgot pane
  await page.click('[data-pane-go="forgot"]');
  await sleep(200);
  const forgotActive = await page.$eval('[data-pane="forgot"]', el => el.classList.contains('active'));
  console.log(`Forgot pane active: ${forgotActive}`);
  await shoot(page, 'auth-02-forgot');

  // 3. Back to signin, then click "Register"
  await page.evaluate(() => document.querySelector('.login-pane.active [data-pane-go="signin"]').click());
  await sleep(150);
  await page.evaluate(() => document.querySelector('.login-pane.active [data-pane-go="register"]').click());
  await sleep(200);
  const registerActive = await page.$eval('[data-pane="register"]', el => el.classList.contains('active'));
  console.log(`Register pane active: ${registerActive}`);
  await shoot(page, 'auth-03-register');

  // 4. Fill register form with mismatched passwords → error
  await page.type('#regName', 'Др. Тест Е2Е');
  await page.type('#regSpec', 'Терапевт');
  await page.type('#regEmail', `e2e_${Date.now()}@avris.tj`);
  await page.type('#regPass', 'pass1234');
  await page.type('#regPass2', 'different');
  await page.click('#registerForm button[type="submit"]');
  await sleep(300);
  const mismatchMsg = await page.$eval('#loginError', el => el.textContent);
  console.log(`Mismatched-pass error: "${mismatchMsg}"`);

  // 5. Fix password and submit → should land on dashboard
  await page.click('#regPass2', { clickCount: 3 });
  await page.keyboard.press('Backspace');
  await page.type('#regPass2', 'pass1234');
  await page.click('#registerForm button[type="submit"]');
  await sleep(800);
  const appShown = await page.$eval('#appShell', el => !el.classList.contains('hidden'));
  const tokenStored = await page.evaluate(() => !!localStorage.getItem('avris-token'));
  console.log(`After register: appShell shown=${appShown}, token stored=${tokenStored}`);
  await shoot(page, 'auth-04-dashboard-after-register');

  // 6. Reload page → should auto-login via /me
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(800);
  const stayedIn = await page.$eval('#appShell', el => !el.classList.contains('hidden'));
  console.log(`After reload (auto-/me): appShell shown=${stayedIn}`);

  // 7. Open Settings → About, click Logout
  await page.evaluate(() => {
    document.querySelector('.nav-link[data-screen="settings"]').click();
  });
  await sleep(250);
  await page.evaluate(() => {
    document.querySelector('.set-nav-item[data-sp="setAbout"]').click();
  });
  await sleep(300);
  const logoutInfo = await page.evaluate(() => {
    var b = document.getElementById('logoutBtn');
    return {
      exists: !!b,
      hasOnclick: !!(b && b.onclick),
      visible: !!(b && b.offsetParent !== null),
    };
  });
  console.log('logoutBtn info:', logoutInfo);
  await page.evaluate(() => document.getElementById('logoutBtn').click());
  await sleep(200);
  // confirm2() modal appears — click "OK" (cOk)
  await page.evaluate(() => document.getElementById('cOk').click());
  await sleep(500);
  const backToLogin = await page.$eval('#loginScreen', el => !el.classList.contains('hidden'));
  const tokenCleared = await page.evaluate(() => !localStorage.getItem('avris-token'));
  console.log(`After logout: login shown=${backToLogin}, token cleared=${tokenCleared}`);
  await shoot(page, 'auth-05-after-logout');

  // 8. Sign in with the same credentials we just registered
  // (Reload to start fresh state)
  await page.evaluate(() => localStorage.removeItem('avris-token'));
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(400);
  // Use last registered email — read from page log? We'll just register a new one
  const lastEmail = await page.evaluate(() => {
    return localStorage.getItem('test-email') || '';
  });

  await browser.close();
})();
