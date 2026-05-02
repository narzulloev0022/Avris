// Day 6 — PDF export + history tab
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const OUT = path.resolve(__dirname, '..', 'screenshots');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox'],
    acceptDownloads: true,
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => console.error(' JS ERROR:', e.message));

  const downloadDir = path.resolve(__dirname, '..', '_downloads');
  if (!fs.existsSync(downloadDir)) fs.mkdirSync(downloadDir, { recursive: true });
  const client = await page.target().createCDPSession();
  await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: downloadDir });

  console.log('\n=== 1. Register + login ===');
  await page.goto('http://localhost:8080/index.html', { waitUntil: 'networkidle0' });
  await sleep(700);
  await page.evaluate(() => {
    try { localStorage.removeItem('avris-token'); } catch(e){}
    window.DEMO_MODE = false;
    var b=document.getElementById('demoBadge');if(b)b.hidden=true;
    document.getElementById('appShell').classList.add('hidden');
    document.getElementById('loginScreen').classList.remove('hidden');
    document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'register'));
  });
  await sleep(150);
  const email = `day6_${Date.now()}@avris.tj`;
  await page.type('#regName', 'Др. Day6');
  await page.type('#regEmail', email);
  await page.type('#regPass', 'pass1234');
  await page.type('#regPass2', 'pass1234');
  await page.click('#registerForm button[type="submit"]');
  await sleep(1500);

  // Pick first patient (Иванова) — the seeded demo
  console.log('\n=== 2. Save 2 consultations for first patient ===');
  await page.evaluate(() => document.querySelector('.nav-link[data-screen="consultation"]').click());
  await sleep(400);
  // First consultation: fill SOAP and save
  for (let i = 1; i <= 2; i++) {
    await page.evaluate((n) => {
      document.getElementById('transcriptText').textContent = 'Транскрипт осмотра №' + n + '. Жалобы на головную боль.';
      document.getElementById('soapS').value = 'Жалобы №' + n + ' на головную боль и давление.';
      document.getElementById('soapO').value = 'АД 14' + n + '/9' + n + ', ЧСС 8' + n;
      document.getElementById('soapA').value = 'Гипертония ' + n + ' ст';
      document.getElementById('soapP').value = 'Эналаприл ' + n + '0мг';
    }, i);
    await page.evaluate(() => document.getElementById('stopRec').click());
    await sleep(700);
    // Need to set _serverId on the patient if missing — fetchPatients wired it on login
    const cid = await page.evaluate(() => window.currentConsultationId);
    console.log(`  Saved consultation #${i} → id=${cid}`);
    // Clear for next round
    await page.evaluate(() => {
      ['soapS','soapO','soapA','soapP'].forEach(id => document.getElementById(id).value = '');
      document.getElementById('transcriptText').textContent = '';
    });
  }

  console.log('\n=== 3. Export consultation PDF ===');
  // Refill so currentConsultationId is the LAST saved one. We just cleared after save, so re-trigger:
  await page.evaluate(() => {
    document.getElementById('transcriptText').textContent = 'Финальный транскрипт для PDF';
    document.getElementById('soapS').value = 'Жалобы для PDF';
    document.getElementById('soapO').value = 'АД 132/85';
    document.getElementById('soapA').value = 'Стабилизация';
    document.getElementById('soapP').value = 'Продолжить лечение';
  });
  // Click export — should save AND download
  // Clear download dir
  fs.readdirSync(downloadDir).forEach(f => fs.unlinkSync(path.join(downloadDir, f)));
  await page.evaluate(() => document.getElementById('exportPdfBtn').click());
  await sleep(2000);
  const files = fs.readdirSync(downloadDir);
  const pdf = files.find(f => f.endsWith('.pdf'));
  let pdfSize = 0;
  if (pdf) {
    pdfSize = fs.statSync(path.join(downloadDir, pdf)).size;
  }
  console.log(`  Downloaded: ${pdf || 'NONE'} (${pdfSize} bytes)`);
  fs.copyFileSync(path.join(downloadDir, pdf), path.join(OUT, 'day6-consult.pdf'));

  console.log('\n=== 4. Switch to История tab — should show 3 consultations ===');
  await page.evaluate(() => {
    var btn = Array.from(document.querySelectorAll('.consult-tab')).find(b => b.dataset.ct === 'cTabHist');
    if (btn) btn.click();
  });
  await sleep(800);
  const histInfo = await page.evaluate(() => {
    var dyn = document.getElementById('histDynamic');
    var items = dyn ? dyn.querySelectorAll('.hist-item') : [];
    return {
      visible: !!dyn && dyn.children.length > 0,
      count: items.length,
      firstSummary: items[0] ? items[0].querySelector('.hist-item-summary').textContent.trim().slice(0,60) : null,
      firstLang: items[0] ? items[0].querySelector('.hist-item-lang').textContent.trim() : null,
    };
  });
  console.log(`  History items: count=${histInfo.count}, first lang="${histInfo.firstLang}", first summary="${histInfo.firstSummary}"`);
  await page.screenshot({ path: path.join(OUT, 'day6-history.png') });

  console.log('\n=== 5. Expand first hist item ===');
  await page.evaluate(() => document.querySelector('#histDynamic .hist-item').click());
  await sleep(200);
  const expanded = await page.evaluate(() => document.querySelector('#histDynamic .hist-item').classList.contains('expanded'));
  const soapVisible = await page.evaluate(() => {
    var soap = document.querySelector('#histDynamic .hist-item.expanded .hist-item-soap');
    return soap && getComputedStyle(soap).display !== 'none';
  });
  console.log(`  Expanded: ${expanded}, soap block visible: ${soapVisible}`);
  await page.screenshot({ path: path.join(OUT, 'day6-history-expanded.png') });

  console.log('\n=== 6. Lab order PDF flow ===');
  // Create a lab order, receive results, then download PDF
  await page.evaluate(() => {
    var btn = Array.from(document.querySelectorAll('.consult-tab')).find(b => b.dataset.ct === 'cTabExam');
    if (btn) btn.click();
  });
  await sleep(200);
  await page.evaluate(() => document.getElementById('labOrderBtn').click());
  await sleep(900);
  const orderId = await page.evaluate(() => window.currentLabOrder && window.currentLabOrder.id);
  console.log(`  Lab order id: ${orderId}`);
  await page.evaluate(() => document.getElementById('labModalCancel').click());
  await sleep(150);
  // Submit results via direct API call
  await page.evaluate(async (oid) => {
    await fetch('http://localhost:8000/api/lab-orders/' + oid + '/results', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ results: { lab_oak: { value: '118', unit: 'г/л', range: '120-160' } } })
    });
  }, orderId);
  await sleep(300);
  // Switch to Анализы tab
  await page.evaluate(() => {
    var btn = Array.from(document.querySelectorAll('.consult-tab')).find(b => b.dataset.ct === 'cTabLabs');
    if (btn) btn.click();
  });
  await sleep(800);
  // Click PDF button on the dynamic card
  fs.readdirSync(downloadDir).forEach(f => fs.unlinkSync(path.join(downloadDir, f)));
  const pdfBtnPresent = await page.evaluate(() => {
    var b = document.querySelector('#labsDynamic [data-pdf-id]');
    if (b) { b.click(); return true; }
    return false;
  });
  console.log(`  PDF button found: ${pdfBtnPresent}`);
  await sleep(2000);
  const files2 = fs.readdirSync(downloadDir);
  const pdf2 = files2.find(f => f.endsWith('.pdf'));
  let pdf2Size = 0;
  if (pdf2) pdf2Size = fs.statSync(path.join(downloadDir, pdf2)).size;
  console.log(`  Downloaded lab PDF: ${pdf2 || 'NONE'} (${pdf2Size} bytes)`);
  if (pdf2) fs.copyFileSync(path.join(downloadDir, pdf2), path.join(OUT, 'day6-lab.pdf'));

  console.log('\n=== 7. Demo mode: history mock ===');
  await page.evaluate(() => {
    try { localStorage.removeItem('avris-token'); } catch(e){}
    window.DEMO_MODE = true;
  });
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(700);
  // We're now in demo via reload (backend still up but no token + DEMO_MODE flag set).
  // bootstrap will probe /me without token → /api/health succeeds → showLogin. So manually force.
  await page.evaluate(() => {
    window.DEMO_MODE = true;
    document.getElementById('appShell').classList.remove('hidden');
    document.getElementById('loginScreen').classList.add('hidden');
    var b = document.getElementById('demoBadge'); if (b) b.hidden = false;
  });
  await sleep(150);
  await page.evaluate(() => document.querySelector('.nav-link[data-screen="consultation"]').click());
  await sleep(300);
  await page.evaluate(() => {
    var btn = Array.from(document.querySelectorAll('.consult-tab')).find(b => b.dataset.ct === 'cTabHist');
    if (btn) btn.click();
  });
  await sleep(500);
  const demoInfo = await page.evaluate(() => ({
    count: document.querySelectorAll('#histDynamic .hist-item').length,
  }));
  console.log(`  Demo history items: ${demoInfo.count}`);

  // Demo PDF export → toast
  await page.evaluate(() => {
    var btn = Array.from(document.querySelectorAll('.consult-tab')).find(b => b.dataset.ct === 'cTabExam');
    if (btn) btn.click();
  });
  await sleep(150);
  await page.evaluate(() => document.getElementById('exportPdfBtn').click());
  await sleep(400);
  const demoToast = await page.$eval('#toast', el => el.textContent.trim());
  console.log(`  Demo PDF toast: "${demoToast}"`);

  await browser.close();
})();
