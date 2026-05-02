// Day 3.5 demo-mode integration test (backend MUST be offline)
const puppeteer = require('puppeteer');
const path = require('path');
const FRONTEND = 'http://localhost:8080/index.html';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const OUT = path.resolve(__dirname, '..', 'screenshots');

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => console.error('JS ERROR:', e.message));

  // 1. Initial load with backend OFFLINE → expect auto-demo
  await page.goto(FRONTEND, { waitUntil: 'networkidle0' });
  await sleep(900);
  const onApp = await page.$eval('#appShell', el => !el.classList.contains('hidden'));
  const demoMode = await page.evaluate(() => !!window.DEMO_MODE);
  const badgeVisible = await page.$eval('#demoBadge', el => !el.hidden);
  console.log(`Auto-demo: app=${onApp}, DEMO_MODE=${demoMode}, badge visible=${badgeVisible}`);
  await page.screenshot({ path: path.join(OUT, 'demo-01-dashboard.png') });

  // 2. OAuth click → expect toast, no redirect
  await page.evaluate(() => localStorage.removeItem('avris-token'));
  await page.evaluate(() => {
    document.getElementById('loginScreen').classList.remove('hidden');
    document.getElementById('appShell').classList.add('hidden');
  });
  // We need to be on login pane to click OAuth. But DEMO_MODE was set. Let's just call generateSoap etc directly.
  // Actually re-route: click logout to go to login? but we said DEMO_MODE persists. Let's check OAuth via direct click on login pane.
  // Reload to get fresh state
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(900);
  const onAppAgain = await page.$eval('#appShell', el => !el.classList.contains('hidden'));
  console.log(`After reload, still in app (auto-demo): ${onAppAgain}`);

  // 3. Test generateSoap mock → fills SOAP from DEMO_SOAP[lang]
  await page.evaluate(() => document.querySelector('.nav-link[data-screen="consultation"]').click());
  await sleep(300);
  await page.evaluate(() => {
    document.getElementById('transcriptText').textContent = 'Пациент жалуется на головную боль три дня. АД 140/90.';
  });
  await page.evaluate(() => document.getElementById('soapGenBtn').click());
  // Wait for the 900ms timeout + render
  await sleep(1300);
  const soapFilled = await page.evaluate(() => ({
    s: document.getElementById('soapS').value.slice(0, 50),
    o: document.getElementById('soapO').value.slice(0, 50),
    a: document.getElementById('soapA').value.slice(0, 50),
    p: document.getElementById('soapP').value.slice(0, 50),
  }));
  console.log('SOAP after generate (demo):', soapFilled);
  const toastSoap = await page.evaluate(() => document.getElementById('toast').textContent);
  console.log('Toast after generate:', toastSoap);
  await page.screenshot({ path: path.join(OUT, 'demo-02-soap-filled.png') });

  // 4. Test save → demo toast
  await page.evaluate(() => document.getElementById('stopRec').click());
  await sleep(400);
  const toastSave = await page.evaluate(() => document.getElementById('toast').textContent);
  console.log('Toast after save:', toastSave);

  // 5. Test recording (typewriter fallback)
  await page.evaluate(() => document.getElementById('clearRec').click());
  await sleep(150);
  await page.evaluate(() => document.getElementById('micBtn').click());
  await sleep(800);
  const recOn = await page.evaluate(() => document.getElementById('micBtn').classList.contains('rec'));
  const transcriptDuringRec = await page.evaluate(() => document.getElementById('transcriptText').textContent.length);
  console.log(`Recording active: ${recOn}, transcript chars after 800ms: ${transcriptDuringRec}`);
  await page.evaluate(() => document.getElementById('micBtn').click());
  await sleep(200);
  const recOff = await page.evaluate(() => !document.getElementById('micBtn').classList.contains('rec'));
  console.log(`Recording stopped: ${recOff}`);

  // 6. Test logout → goes back to login (still demo)
  await page.evaluate(() => document.querySelector('.nav-link[data-screen="settings"]').click());
  await sleep(250);
  await page.evaluate(() => document.querySelector('.set-nav-item[data-sp="setAbout"]').click());
  await sleep(200);
  await page.evaluate(() => document.getElementById('logoutBtn').click());
  await sleep(200);
  await page.evaluate(() => document.getElementById('cOk').click());
  await sleep(500);
  const onLogin = await page.$eval('#loginScreen', el => !el.classList.contains('hidden'));
  console.log(`After logout: login shown=${onLogin}, DEMO_MODE still=${await page.evaluate(() => !!window.DEMO_MODE)}`);
  await page.screenshot({ path: path.join(OUT, 'demo-03-login-with-badge.png') });

  // 7. Click OAuth → expect toast (no redirect)
  await page.evaluate(() => document.querySelector('.oauth-btn[data-provider="google"]').click());
  await sleep(400);
  const toastOauth = await page.evaluate(() => document.getElementById('toast').textContent);
  const stillOnLogin = await page.$eval('#loginScreen', el => !el.classList.contains('hidden'));
  console.log(`OAuth click: toast="${toastOauth}", still on login=${stillOnLogin}`);

  // 8. Sign in with any creds → enter app
  await page.type('#signinEmail', 'whatever@avris.tj');
  await page.type('#signinPassword', 'irrelevant');
  await page.click('#signinForm button[type="submit"]');
  await sleep(500);
  const onAppFinal = await page.$eval('#appShell', el => !el.classList.contains('hidden'));
  console.log(`After demo signin: app=${onAppFinal}`);

  await browser.close();
})();
