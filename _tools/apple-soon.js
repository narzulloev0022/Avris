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
    await page.evaluate(() => {
      try { localStorage.removeItem('avris-token'); } catch(e){}
      window.DEMO_MODE = false;
      var b=document.getElementById('demoBadge');if(b)b.hidden=true;
      document.getElementById('appShell').classList.add('hidden');
      document.getElementById('profileSetup').classList.add('hidden');
      document.getElementById('loginScreen').classList.remove('hidden');
      document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'signin'));
    });
    await sleep(150);
    const info = await page.evaluate(() => {
      var btn = document.querySelector('.oauth-btn[data-provider="apple"]');
      var badge = btn?.querySelector('.oauth-soon-badge');
      var cs = btn ? getComputedStyle(btn) : null;
      var bcs = badge ? getComputedStyle(badge) : null;
      var rect = btn?.getBoundingClientRect();
      var brect = badge?.getBoundingClientRect();
      return {
        opacity: cs?.opacity,
        comingSoon: btn?.dataset.comingSoon,
        badgeText: badge?.textContent.trim(),
        badgeBg: bcs?.backgroundColor,
        badgePos: bcs?.position,
        badgeFontSize: bcs?.fontSize,
        badgeRight: brect && rect ? rect.right - brect.right : null,
        badgeTopOffset: brect && rect ? brect.top - rect.top : null,
      };
    });
    console.log(`[${theme}]`, JSON.stringify(info));
    await page.screenshot({ path: path.join(OUT, `apple-soon-${theme}.png`) });
    // Click → toast
    await page.click('.oauth-btn[data-provider="apple"]');
    await sleep(400);
    const toast = await page.$eval('#toast', el => el.textContent.trim());
    console.log(`[${theme}] toast: "${toast}"`);
    // i18n
    for (const c of ['ru','tj','en']) {
      await page.evaluate(code => {
        var btns = Array.from(document.querySelectorAll('#langSelLogin button, #langSel button'));
        var b = btns.find(x => x.textContent.trim().toLowerCase() === code);
        if (b) b.click();
      }, c);
      await sleep(200);
      const t = await page.evaluate(() => ({
        badge: document.querySelector('.oauth-btn[data-provider="apple"] .oauth-soon-badge')?.textContent.trim(),
        appleLabel: document.querySelector('.oauth-btn[data-provider="apple"] span[data-i18n="oauth_apple"]')?.textContent.trim(),
      }));
      console.log(`  [${theme}/${c}] badge="${t.badge}" label="${t.appleLabel}"`);
    }
    await page.close();
  }
  await browser.close();
})();
