// Day 4: full patient CRUD test (real backend + demo)
const puppeteer = require('puppeteer');
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function flow(label, mode) {
  console.log(`\n=== ${label} (${mode}) ===`);
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => console.error(' JS ERROR:', e.message));

  // For backend mode, we need a fresh user (otherwise old test data interferes).
  // For demo mode, just load and bypass.
  await page.goto('http://localhost:8080/index.html', { waitUntil: 'networkidle0' });
  await sleep(700);

  if (mode === 'backend') {
    // Force the login screen with register pane
    await page.evaluate(() => {
      try { localStorage.removeItem('avris-token'); } catch(e){}
      window.DEMO_MODE = false;
      var b=document.getElementById('demoBadge');if(b)b.hidden=true;
      document.getElementById('appShell').classList.add('hidden');
      document.getElementById('loginScreen').classList.remove('hidden');
      document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'register'));
    });
    await sleep(150);
    const email = `day4_${Date.now()}@avris.tj`;
    await page.type('#regName', 'Др. Day4');
    await page.type('#regEmail', email);
    await page.type('#regPass', 'pass1234');
    await page.type('#regPass2', 'pass1234');
    await page.click('#registerForm button[type="submit"]');
    await sleep(1500);  // Wait for register + auto-fetchPatients
  }

  // Verify we're in app
  const inApp = await page.$eval('#appShell', el => !el.classList.contains('hidden'));
  console.log(`  App shown: ${inApp}`);

  // Wait for patient list to render
  await sleep(400);
  const initialCount = await page.$$eval('#patList .p-item', e => e.length);
  const firstName = await page.$eval('#patList .p-item .p-name', e => e.textContent.trim()).catch(() => null);
  console.log(`  Initial patients: ${initialCount}, first: ${firstName}`);

  // === Open + new patient form ===
  await page.click('#newPatBtn');
  await sleep(250);
  const formVisible = await page.$eval('#patFormModal', el => el.classList.contains('vis'));
  const formTitle = await page.$eval('#patFormTitle', el => el.textContent.trim());
  console.log(`  New form: visible=${formVisible}, title="${formTitle}"`);
  await page.type('#pf_full_name', 'Тестов Т.Т.');
  await page.type('#pf_ward', 'Терапия Z9');
  await page.type('#pf_age', '40');
  await page.select('#pf_gender', 'М');
  await page.type('#pf_blood', 'O+');
  await page.type('#pf_height', '175');
  await page.type('#pf_weight', '72');
  await page.type('#pf_score', '88');
  await page.type('#pf_diagnoses', 'ОРВИ, Кашель');
  await page.type('#pf_allergies', 'Аспирин');
  await page.click('#patFormSave');
  await sleep(800);
  const afterCount = await page.$$eval('#patList .p-item', e => e.length);
  const lastToast = await page.$eval('#toast', el => el.textContent.trim());
  console.log(`  After create: count=${afterCount} (expected ${initialCount + 1}), toast="${lastToast}"`);

  // === Edit existing patient ===
  // Open the first patient by clicking the row
  await page.evaluate(() => document.querySelector('#patList .p-item').click());
  await sleep(400);
  const pmOpen = await page.$eval('#patModal', el => el.classList.contains('vis'));
  console.log(`  Patient modal opened: ${pmOpen}`);
  await page.evaluate(() => document.getElementById('pmEditBtn').click());
  await sleep(300);
  const editFormVisible = await page.$eval('#patFormModal', el => el.classList.contains('vis'));
  const editTitle = await page.$eval('#patFormTitle', el => el.textContent.trim());
  const prefilled = await page.$eval('#pf_full_name', el => el.value);
  console.log(`  Edit form: visible=${editFormVisible}, title="${editTitle}", prefilled name="${prefilled}"`);
  // Modify diagnoses
  await page.click('#pf_diagnoses', { clickCount: 3 });
  await page.keyboard.press('Backspace');
  await page.type('#pf_diagnoses', 'Гипертония, Диабет II, ОБНОВЛЕНО');
  await page.click('#patFormSave');
  await sleep(800);
  const updateToast = await page.$eval('#toast', el => el.textContent.trim());
  console.log(`  After update: toast="${updateToast}"`);

  // Verify the change persisted (open the same patient again, check tags)
  await page.evaluate(() => document.querySelector('#patList .p-item').click());
  await sleep(400);
  const updatedDiag = await page.$$eval('#pmDiag .tag', els => els.map(e => e.textContent.trim()).join(' / '));
  console.log(`  Re-opened patient diag: ${updatedDiag}`);

  // For backend mode, verify by direct API
  if (mode === 'backend') {
    const apiCheck = await page.evaluate(async () => {
      const r = await fetch('http://localhost:8000/api/patients/', {
        headers: { 'Authorization': 'Bearer ' + localStorage.getItem('avris-token') },
      });
      const ps = await r.json();
      return { count: ps.length, names: ps.map(p => p.full_name), updated: ps.find(p => p.diagnoses && p.diagnoses.includes('ОБНОВЛЕНО')) };
    });
    console.log(`  API verification: count=${apiCheck.count}, updated row found=${!!apiCheck.updated}, names=[${apiCheck.names.join(', ')}]`);
  }

  await browser.close();
}

(async () => {
  await flow('Backend (real DB)', 'backend');
})();
