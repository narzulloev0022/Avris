// Day 7 voice-first night round e2e
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const OUT = path.resolve(__dirname, '..', 'screenshots');

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => console.error(' JS ERROR:', e.message));

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
  const email = `day7_${Date.now()}@avris.tj`;
  await page.type('#regName', 'Др. Day7');
  await page.type('#regEmail', email);
  await page.type('#regPass', 'pass1234');
  await page.type('#regPass2', 'pass1234');
  await page.click('#registerForm button[type="submit"]');
  await sleep(1500);

  console.log('\n=== 2. Navigate to Ночной обход ===');
  await page.evaluate(() => document.querySelector('.nav-link[data-screen="nightRound"]').click());
  await sleep(400);
  const heroState = await page.evaluate(() => ({
    idleVisible: !document.getElementById('nrVsIdle').hidden,
    cta: document.querySelector('#nrVsIdle .nr-voice-cta').textContent.trim(),
    micExists: !!document.getElementById('nrVoiceStart'),
  }));
  console.log(`  Hero idle: visible=${heroState.idleVisible}, mic=${heroState.micExists}`);
  console.log(`  CTA: "${heroState.cta}"`);
  await page.screenshot({ path: path.join(OUT, 'day7-01-idle.png') });

  console.log('\n=== 3. Click big mic → start typewriter recording ===');
  await page.click('#nrVoiceStart');
  await sleep(900);
  const recState = await page.evaluate(() => ({
    recVisible: !document.getElementById('nrVsRec').hidden,
    transcriptLen: document.getElementById('nrVoiceTranscript').textContent.length,
    micRecClass: document.getElementById('nrVoiceStop').classList.contains('recording'),
  }));
  console.log(`  Recording: visible=${recState.recVisible}, transcriptLen=${recState.transcriptLen}, pulsing=${recState.micRecClass}`);
  await page.screenshot({ path: path.join(OUT, 'day7-02-recording.png') });

  // Let it finish typewriter (auto-stops at end)
  console.log('\n=== 4. Wait for typewriter to finish + parser to fire ===');
  await sleep(7000); // typewriter at 30ms/char × ~150 chars + buffer
  const afterStop = await page.evaluate(() => ({
    recVisible: !document.getElementById('nrVsRec').hidden,
    modalVisible: document.getElementById('nrModal').classList.contains('vis'),
    modalTitle: document.getElementById('nrModalTitle').textContent.trim(),
    examValue: document.getElementById('nrExam').value,
    pulse: document.getElementById('nrvPulse').textContent.trim(),
    bp: document.getElementById('nrvBp').textContent.trim(),
    temp: document.getElementById('nrvTemp').textContent.trim(),
    spo2: document.getElementById('nrvSpo2').textContent.trim(),
    transcriptHTML: document.getElementById('nrModalTranscript').innerHTML,
  }));
  console.log(`  After stop: rec hidden=${!afterStop.recVisible}, modal open=${afterStop.modalVisible}`);
  console.log(`  Modal title: "${afterStop.modalTitle}"`);
  console.log(`  Vitals: pulse=${afterStop.pulse}, bp=${afterStop.bp}, temp=${afterStop.temp}, spo2=${afterStop.spo2}`);
  console.log(`  Notes: "${afterStop.examValue.slice(0, 80)}..."`);
  const marks = (afterStop.transcriptHTML.match(/<mark>/g) || []).length;
  console.log(`  Evidence Link highlights: ${marks} marks`);
  await page.screenshot({ path: path.join(OUT, 'day7-03-modal.png') });

  console.log('\n=== 5. Save round → toast + ward checked ===');
  await page.click('#nrModalSave');
  await sleep(1000);
  const afterSave = await page.evaluate(() => {
    var toastEl = document.getElementById('toast');
    var firstWard = document.querySelector('#nrGrid .nr-card.checked');
    return {
      toast: toastEl.textContent.trim(),
      modalClosed: !document.getElementById('nrModal').classList.contains('vis'),
      checkedCard: !!firstWard,
      checkedText: firstWard ? firstWard.querySelector('.nr-card-checked-row').textContent.trim() : null,
    };
  });
  console.log(`  Toast: "${afterSave.toast}"`);
  console.log(`  Modal closed: ${afterSave.modalClosed}`);
  console.log(`  Ward A1 marked checked: ${afterSave.checkedCard} ("${afterSave.checkedText}")`);
  await page.screenshot({ path: path.join(OUT, 'day7-04-saved.png') });

  // Verify backend persistence
  const apiCheck = await page.evaluate(async () => {
    var r = await fetch('http://localhost:8000/api/night-rounds/', {
      headers: {'Authorization': 'Bearer ' + localStorage.getItem('avris-token')},
    });
    var rows = await r.json();
    return { count: rows.length, first: rows[0] };
  });
  console.log(`  Backend rounds count: ${apiCheck.count}, ward=${apiCheck.first && apiCheck.first.ward}, vitals=${JSON.stringify(apiCheck.first && apiCheck.first.vitals)}`);

  console.log('\n=== 6. Demo mode multi-patient parsing ===');
  await page.evaluate(() => {
    window.DEMO_MODE = true;
    document.getElementById('appShell').classList.remove('hidden');
    document.getElementById('loginScreen').classList.add('hidden');
  });
  // Manually inject multi-patient transcript
  const parsedMulti = await page.evaluate(() => {
    var multiTr = "Палата А1, Иванова. Пульс 78, давление 130 на 85. Состояние стабильное. " +
                  "Палата B3, Омаров. Пульс 92, давление 128 на 80, температура 38.1, сатурация 94. Состояние тяжёлое.";
    var rounds = parseNightRoundTranscript(multiTr);
    return rounds.map(r => ({
      ward: r.ward,
      patient: r.patient && r.patient.name,
      vitals: r.vitals,
      status: r.status,
    }));
  });
  console.log(`  Multi-parser:`, JSON.stringify(parsedMulti, null, 2));

  await browser.close();
})();
