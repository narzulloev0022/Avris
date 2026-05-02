// Day 5: full lab orders flow — doctor creates order → lab tech uploads → doctor sees results
const puppeteer = require('puppeteer');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const path = require('path');
const OUT = path.resolve(__dirname, '..', 'screenshots');

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });

  // === Doctor session ===
  console.log('\n=== 1. Doctor registers + creates lab order ===');
  const doc = await browser.newPage();
  await doc.setViewport({ width: 1440, height: 900 });
  doc.on('pageerror', e => console.error(' DOC JS ERROR:', e.message));
  await doc.goto('http://localhost:8080/index.html', { waitUntil: 'networkidle0' });
  await sleep(700);

  // Force register pane (backend is up so demo doesn't trigger after a fresh login)
  await doc.evaluate(() => {
    try { localStorage.removeItem('avris-token'); } catch(e){}
    window.DEMO_MODE = false;
    var b=document.getElementById('demoBadge');if(b)b.hidden=true;
    document.getElementById('appShell').classList.add('hidden');
    document.getElementById('loginScreen').classList.remove('hidden');
    document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'register'));
  });
  await sleep(150);
  const email = `day5_${Date.now()}@avris.tj`;
  await doc.type('#regName', 'Др. Day5');
  await doc.type('#regEmail', email);
  await doc.type('#regPass', 'pass1234');
  await doc.type('#regPass2', 'pass1234');
  await doc.click('#registerForm button[type="submit"]');
  await sleep(1500);

  const inApp = await doc.$eval('#appShell', el => !el.classList.contains('hidden'));
  console.log(`  Doctor logged in: ${inApp}`);

  // Go to consultation, click "Направить на анализ"
  await doc.evaluate(() => document.querySelector('.nav-link[data-screen="consultation"]').click());
  await sleep(400);
  await doc.evaluate(() => document.getElementById('labOrderBtn').click());
  await sleep(800); // wait for POST /api/lab-orders + QR rebuild

  const orderInfo = await doc.evaluate(() => ({
    modalOpen: document.getElementById('labModal').classList.contains('vis'),
    currentOrder: window.currentLabOrder,
    qrRectCount: document.querySelectorAll('#labQrSvg rect').length,
  }));
  console.log(`  Lab modal open: ${orderInfo.modalOpen}, QR rects: ${orderInfo.qrRectCount}`);
  console.log(`  Created order id=${orderInfo.currentOrder && orderInfo.currentOrder.id}, qr_token=${orderInfo.currentOrder && orderInfo.currentOrder.qr_token && orderInfo.currentOrder.qr_token.slice(0,8)}...`);
  const qrToken = orderInfo.currentOrder && orderInfo.currentOrder.qr_token;
  const orderId = orderInfo.currentOrder && orderInfo.currentOrder.id;
  if (!qrToken) { console.error(' FAIL: no qr_token from POST'); await browser.close(); return; }
  await doc.screenshot({ path: path.join(OUT, 'day5-01-doctor-qr.png') });

  // Close modal, go to Анализы tab — should show pending order
  await doc.evaluate(() => document.getElementById('labModalCancel').click());
  await sleep(200);
  await doc.evaluate(() => {
    var btn = Array.from(document.querySelectorAll('.consult-tab')).find(b => b.dataset.ct === 'cTabLabs');
    if (btn) btn.click();
  });
  await sleep(700);
  const labsViewPending = await doc.evaluate(() => {
    var dyn = document.getElementById('labsDynamic');
    return {
      dynVisible: dyn && !dyn.hidden,
      dynText: dyn ? dyn.textContent.replace(/\s+/g, ' ').trim().slice(0, 200) : null,
      pendingFound: dyn ? dyn.textContent.includes('Ожидание') : false,
    };
  });
  console.log(`  Анализы tab pending: dyn visible=${labsViewPending.dynVisible}, has 'Ожидание'=${labsViewPending.pendingFound}`);
  await doc.screenshot({ path: path.join(OUT, 'day5-02-doctor-pending.png') });

  // === Lab tech session ===
  console.log('\n=== 2. Lab tech opens /lab and submits results ===');
  const lab = await browser.newPage();
  await lab.setViewport({ width: 1024, height: 800 });
  lab.on('pageerror', e => console.error(' LAB JS ERROR:', e.message));
  await lab.goto(`http://localhost:8000/lab?token=${qrToken}`, { waitUntil: 'networkidle0' });
  await sleep(800); // auto-load via ?token=

  const labInfo = await lab.evaluate(() => ({
    orderSecVisible: !document.getElementById('orderSec').hidden,
    patName: document.getElementById('patName').textContent.trim(),
    docName: document.getElementById('docName').textContent.trim(),
    statusText: document.getElementById('statusPill').textContent.trim(),
    rowCount: document.querySelectorAll('#testsList .row').length,
  }));
  console.log(`  Lab loaded: visible=${labInfo.orderSecVisible}, patient="${labInfo.patName}", doctor="${labInfo.docName}", status="${labInfo.statusText}", tests=${labInfo.rowCount}`);
  await lab.screenshot({ path: path.join(OUT, 'day5-03-lab-loaded.png') });

  // Fill values
  await lab.evaluate(() => {
    var rows = document.querySelectorAll('#testsList .row');
    var data = [
      { value: '118', unit: 'г/л', range: '120-160' },
      { value: '6.8', unit: 'ммоль/л', range: '3.3-5.5' },
      { value: '7.2', unit: '%', range: '4-6' },
    ];
    rows.forEach((r, i) => {
      if (!data[i]) return;
      r.querySelector('[data-f="value"]').value = data[i].value;
      r.querySelector('[data-f="unit"]').value = data[i].unit;
      r.querySelector('[data-f="range"]').value = data[i].range;
    });
  });
  await lab.click('#submitBtn');
  await sleep(900);
  const submittedToast = await lab.$eval('#toast', el => el.textContent.trim());
  const newStatus = await lab.$eval('#statusPill', el => el.textContent.trim());
  console.log(`  After submit: toast="${submittedToast}", status="${newStatus}"`);
  await lab.screenshot({ path: path.join(OUT, 'day5-04-lab-sent.png') });

  // === Doctor refreshes view, should see received ===
  console.log('\n=== 3. Doctor sees received results ===');
  // Switch tab to force re-fetch
  await doc.evaluate(() => {
    var btn = Array.from(document.querySelectorAll('.consult-tab')).find(b => b.dataset.ct === 'cTabExam');
    if (btn) btn.click();
  });
  await sleep(150);
  await doc.evaluate(() => {
    var btn = Array.from(document.querySelectorAll('.consult-tab')).find(b => b.dataset.ct === 'cTabLabs');
    if (btn) btn.click();
  });
  await sleep(800);
  const labsViewReceived = await doc.evaluate(() => {
    var dyn = document.getElementById('labsDynamic');
    return {
      receivedFound: dyn ? dyn.textContent.includes('Получено') : false,
      cardCount: dyn ? dyn.querySelectorAll('.labs-card').length : 0,
      hasAiComment: dyn ? !!dyn.querySelector('.lab-ai-comment') : false,
      hasTable: dyn ? !!dyn.querySelector('.labs-table tbody tr') : false,
      tableRows: dyn ? dyn.querySelectorAll('.labs-table tbody tr').length : 0,
    };
  });
  console.log(`  Анализы tab received: cards=${labsViewReceived.cardCount}, has 'Получено'=${labsViewReceived.receivedFound}, AI comment=${labsViewReceived.hasAiComment}, table rows=${labsViewReceived.tableRows}`);
  await doc.screenshot({ path: path.join(OUT, 'day5-05-doctor-received.png') });

  await browser.close();
})();
