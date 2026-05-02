// Capture login OAuth row to verify button order, icons, and i18n
const puppeteer = require('puppeteer');
const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const OUT = path.resolve(__dirname, '..', 'screenshots');

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  for (const theme of ['light', 'dark']) {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });
    await page.evaluateOnNewDocument(t => { try { localStorage.setItem('avris-theme', t); } catch(e){} }, theme);
    await page.evaluateOnNewDocument(() => { try { localStorage.removeItem('avris-token'); } catch(e){} });
    await page.goto('http://localhost:8080/index.html', { waitUntil: 'networkidle0' });
    await sleep(700);
    // Force login pane visible (DEMO_MODE auto-jumps to app)
    await page.evaluate(() => {
      try { localStorage.removeItem('avris-token'); } catch(e){}
      document.getElementById('appShell').classList.add('hidden');
      document.getElementById('loginScreen').classList.remove('hidden');
      document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'signin'));
    });
    await sleep(150);
    await page.screenshot({ path: path.join(OUT, `oauth-${theme}-signin.png`), fullPage: false });

    // Order check
    const order = await page.$$eval('.login-oauth-row .oauth-btn', els => els.map(e => e.dataset.provider));
    const labels = await page.$$eval('.login-oauth-row .oauth-btn span', els => els.map(e => e.textContent.trim()));
    console.log(`${theme}: order=[${order.join(', ')}] labels=[${labels.join(' | ')}]`);

    // i18n
    for (const code of ['ru', 'tj', 'en']) {
      await page.evaluate(c => {
        var btns = Array.from(document.querySelectorAll('#langSelLogin button, #langSel button'));
        var b = btns.find(x => x.textContent.trim().toLowerCase() === c);
        if (b) b.click();
      }, code);
      await sleep(180);
      const trLabels = await page.$$eval('.login-oauth-row .oauth-btn span', els => els.map(e => e.textContent.trim()));
      console.log(`  [${code}] ${trLabels.join(' | ')}`);
    }
    await page.close();
  }
  await browser.close();
})();
