// Day 3 integration: STT/LLM/save endpoint wiring
const puppeteer = require('puppeteer');
const path = require('path');

const FRONTEND = 'http://localhost:8080/index.html';
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => console.error('JS ERROR:', e.message));

  // 1. Register a fresh doctor
  await page.goto(FRONTEND, { waitUntil: 'networkidle0' });
  await sleep(300);
  await page.evaluate(() => document.querySelector('[data-pane-go="register"]').click());
  await sleep(150);
  await page.type('#regName', 'Др. Тест Day3');
  await page.type('#regSpec', 'Терапевт');
  const email = `day3_${Date.now()}@avris.tj`;
  await page.type('#regEmail', email);
  await page.type('#regPass', 'pass1234');
  await page.type('#regPass2', 'pass1234');
  await page.click('#registerForm button[type="submit"]');
  await sleep(900);
  const onApp = await page.$eval('#appShell', el => !el.classList.contains('hidden'));
  console.log(`Registered ${email} → app: ${onApp}`);

  // 2. Navigate to consultation
  await page.evaluate(() => document.querySelector('.nav-link[data-screen="consultation"]').click());
  await sleep(300);

  // 3. Inject fake transcript and call generateSoap (expect 503, error toast)
  await page.evaluate(() => {
    document.getElementById('transcriptText').textContent = 'Пациент жалуется на головную боль три дня. АД 140/90. Назначено эналаприл.';
  });
  const btnInfo = await page.evaluate(() => {
    var b = document.getElementById('soapGenBtn');
    return { exists: !!b, hasClick: !!(b && b.onclick), text: b && b.textContent };
  });
  console.log('soapGenBtn:', btnInfo);

  // Click + wait for response
  const llmRes = await page.evaluate(async () => {
    var resp = await fetch('http://localhost:8000/api/llm/generate-soap', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + localStorage.getItem('avris-token'),
      },
      body: JSON.stringify({transcript: 'Пациент жалуется на головную боль', language: 'ru'}),
    });
    return { status: resp.status, body: await resp.text() };
  });
  console.log('Direct /generate-soap:', llmRes.status, llmRes.body.slice(0, 100));

  // Click button → triggers generateSoap → expect skeleton on cards then error toast
  await page.evaluate(() => document.getElementById('soapGenBtn').click());
  await sleep(1500);
  const toastText = await page.evaluate(() => document.getElementById('toast') && document.getElementById('toast').textContent);
  console.log('After soapGen click — toast:', toastText);

  // 4. saveConsult — POST /api/consultations
  await page.evaluate(() => {
    document.getElementById('soapS').value = 'Жалобы на головную боль';
    document.getElementById('soapO').value = 'АД 140/90';
    document.getElementById('soapA').value = 'Гипертония 2 ст';
    document.getElementById('soapP').value = 'Эналаприл 10мг';
  });
  await page.evaluate(() => document.getElementById('stopRec').click());
  await sleep(700);
  const toastAfterSave = await page.evaluate(() => document.getElementById('toast').textContent);
  console.log('After save click — toast:', toastAfterSave);

  // 5. Verify consultation persisted via list endpoint
  const consultList = await page.evaluate(async () => {
    var resp = await fetch('http://localhost:8000/api/consultations/', {
      headers: {'Authorization': 'Bearer ' + localStorage.getItem('avris-token')},
    });
    return { status: resp.status, body: await resp.json() };
  });
  console.log('Consultations list status:', consultList.status, 'count:', consultList.body.length);
  if (consultList.body[0]) {
    var c = consultList.body[0];
    console.log('  first:', { id: c.id, soap_a: c.soap_a, transcript: c.transcript && c.transcript.slice(0,40) });
  }

  // 6. Test STT 503 (no audio, but check endpoint reachable)
  const sttRes = await page.evaluate(async () => {
    var fd = new FormData();
    var blob = new Blob([new Uint8Array(100)], {type: 'audio/webm'});
    fd.append('file', blob, 'test.webm');
    fd.append('language', 'ru');
    var resp = await fetch('http://localhost:8000/api/stt/transcribe', {
      method: 'POST',
      headers: {'Authorization': 'Bearer ' + localStorage.getItem('avris-token')},
      body: fd,
    });
    return { status: resp.status, body: await resp.text() };
  });
  console.log('STT no-key:', sttRes.status, sttRes.body.slice(0, 80));

  await browser.close();
})();
