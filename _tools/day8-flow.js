// Day 8 — register → email verify → profile setup
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const OUT = path.resolve(__dirname, '..', 'screenshots');

function readBackendCode(email, label) {
  const logFile = '/private/tmp/claude-501/-Users-shahzod/558e2355-3846-4f02-9c14-08187847669d/tasks/b5jc0mxf8.output';
  if (!fs.existsSync(logFile)) return null;
  const lines = fs.readFileSync(logFile, 'utf8').split('\n');
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = lines[i].match(new RegExp(`${label} for ${email.replace(/[.@]/g,'\\$&')}: (\\d{6})`));
    if (m) return m[1];
  }
  return null;
}

(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => console.error(' JS ERROR:', e.message));

  console.log('\n=== 1. Register (no JWT, requires verify) ===');
  await page.goto('http://localhost:8080/index.html', { waitUntil: 'networkidle0' });
  await sleep(700);
  await page.evaluate(() => {
    try { localStorage.removeItem('avris-token'); } catch(e){}
    window.DEMO_MODE = false;
    var b = document.getElementById('demoBadge'); if (b) b.hidden = true;
    document.getElementById('appShell').classList.add('hidden');
    document.getElementById('profileSetup').classList.add('hidden');
    document.getElementById('loginScreen').classList.remove('hidden');
    document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'register'));
  });
  await sleep(150);
  const email = `day8_${Date.now()}@avris.tj`;
  await page.type('#regEmail', email);
  await page.type('#regPass', 'pass1234');
  await page.type('#regPass2', 'pass1234');
  await page.click('#registerForm button[type="submit"]');
  await sleep(1500);

  const verifyState = await page.evaluate(() => ({
    activePane: document.querySelector('.login-pane.active')?.dataset.pane,
    emailLabel: document.getElementById('verifyEmailLabel').textContent.trim(),
    cellCount: document.querySelectorAll('#verifyOtpRow .otp-cell').length,
  }));
  console.log(`  After register: pane="${verifyState.activePane}", email="${verifyState.emailLabel}", OTP cells=${verifyState.cellCount}`);
  await page.screenshot({ path: path.join(OUT, 'day8-01-verify.png') });

  console.log('\n=== 2. Type wrong code → error ===');
  for (let i = 0; i < 6; i++) await page.type(`#verifyOtpRow .otp-cell[data-otp-i="${i}"]`, '0');
  await sleep(800);
  const errAfterWrong = await page.evaluate(() => document.getElementById('loginError').textContent.trim());
  console.log(`  Wrong code error: "${errAfterWrong}"`);

  // Read real code from backend log
  await sleep(300);
  const code = readBackendCode(email, 'Verification code');
  console.log(`  Backend code: ${code}`);

  console.log('\n=== 3. Type correct code → JWT + profile setup screen ===');
  // Clear cells
  await page.evaluate(() => document.getElementById('verifyOtpRow').clearOtp());
  await sleep(150);
  for (let i = 0; i < 6; i++) await page.type(`#verifyOtpRow .otp-cell[data-otp-i="${i}"]`, code[i]);
  await sleep(1500);

  const afterVerify = await page.evaluate(() => ({
    profileVisible: !document.getElementById('profileSetup').classList.contains('hidden'),
    appHidden: document.getElementById('appShell').classList.contains('hidden'),
    tokenSet: !!localStorage.getItem('avris-token'),
  }));
  console.log(`  After verify: profile setup visible=${afterVerify.profileVisible}, app hidden=${afterVerify.appHidden}, token=${afterVerify.tokenSet}`);
  await page.screenshot({ path: path.join(OUT, 'day8-02-profile-setup.png') });

  console.log('\n=== 4. Submit profile form → app dashboard ===');
  await page.type('#pfLastName', 'Нарзуллоев');
  await page.type('#pfFirstName', 'Шахзод');
  await page.type('#pfPatronymic', 'Олимович');
  await page.evaluate(() => { document.getElementById('pfDob').value = '1985-03-12'; });
  await page.type('#pfPhone', '+992900000000');
  await page.select('#pfSpecialty', 'cardiologist');
  await page.type('#pfHospital', 'Городская больница №1');
  await page.type('#pfDepartment', 'Кардиология');
  await page.select('#pfPosition', 'head');
  await page.type('#pfExperience', '15');
  // Capture network response from PUT /profile
  const profileResp = page.waitForResponse(r => r.url().includes('/api/auth/profile') && r.request().method() === 'PUT', { timeout: 5000 }).catch(() => null);
  await page.evaluate(() => document.querySelector('#profileForm').dispatchEvent(new Event('submit', { cancelable: true })));
  const respObj = await profileResp;
  if (respObj) {
    const bodyText = await respObj.text().catch(() => '');
    console.log(`  PUT /profile status: ${respObj.status()}, body: ${bodyText.slice(0, 250)}`);
  } else {
    console.log(`  PUT /profile: NO RESPONSE captured`);
  }
  await sleep(1500);

  const afterProfile = await page.evaluate(() => ({
    appVisible: !document.getElementById('appShell').classList.contains('hidden'),
    profileHidden: document.getElementById('profileSetup').classList.contains('hidden'),
    sidebarName: document.querySelector('.sidebar-user strong')?.textContent.trim(),
    sidebarRole: document.querySelector('.sidebar-user span')?.textContent.trim(),
  }));
  console.log(`  After save: app=${afterProfile.appVisible}, profile hidden=${afterProfile.profileHidden}`);
  console.log(`  Sidebar: "${afterProfile.sidebarName}" / "${afterProfile.sidebarRole}"`);
  await page.screenshot({ path: path.join(OUT, 'day8-03-dashboard.png') });

  console.log('\n=== 5. Login flow with verified user ===');
  await page.evaluate(() => {
    localStorage.removeItem('avris-token');
    document.getElementById('appShell').classList.add('hidden');
    document.getElementById('loginScreen').classList.remove('hidden');
    document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'signin'));
  });
  await sleep(200);
  await page.type('#signinEmail', email);
  await page.type('#signinPassword', 'pass1234');
  await page.click('#signinForm button[type="submit"]');
  await sleep(1500);
  const afterLogin = await page.evaluate(() => ({
    app: !document.getElementById('appShell').classList.contains('hidden'),
    sidebar: document.querySelector('.sidebar-user strong')?.textContent.trim(),
  }));
  console.log(`  After login: app=${afterLogin.app}, sidebar="${afterLogin.sidebar}"`);

  console.log('\n=== 6. Settings Profile editable form ===');
  await page.evaluate(() => document.querySelector('.nav-link[data-screen="settings"]').click());
  await sleep(300);
  // setProfile is default first tab — already active
  const setProfileState = await page.evaluate(() => ({
    spLastName: document.getElementById('spLastName').value,
    spFirstName: document.getElementById('spFirstName').value,
    spSpecialty: document.getElementById('spSpecialty').value,
    spEmail: document.getElementById('spEmail').value,
    avatar: document.getElementById('setProfileAvatar').textContent.trim(),
  }));
  console.log(`  Settings profile prefilled: lastName="${setProfileState.spLastName}", specialty="${setProfileState.spSpecialty}", email="${setProfileState.spEmail}", avatar="${setProfileState.avatar}"`);
  await page.screenshot({ path: path.join(OUT, 'day8-04-settings.png') });

  console.log('\n=== 7. Forgot password OTP flow ===');
  await page.evaluate(() => {
    localStorage.removeItem('avris-token');
    document.getElementById('appShell').classList.add('hidden');
    document.getElementById('loginScreen').classList.remove('hidden');
    document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'forgot'));
  });
  await sleep(200);
  await page.type('#forgotEmail', email);
  await page.click('#forgotForm button[type="submit"]');
  await sleep(1200);
  const afterForgot = await page.evaluate(() => ({
    pane: document.querySelector('.login-pane.active')?.dataset.pane,
    label: document.getElementById('resetEmailLabel').textContent.trim(),
  }));
  console.log(`  After forgot: pane="${afterForgot.pane}", email label="${afterForgot.label}"`);
  // Verify unknown email returns 404 with friendly message
  await page.evaluate(() => {
    document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'forgot'));
    document.getElementById('forgotEmail').value = '';
  });
  await page.type('#forgotEmail', 'nobody999@example.com');
  await page.click('#forgotForm button[type="submit"]');
  await sleep(1000);
  const notFoundMsg = await page.evaluate(() => document.getElementById('loginError').textContent.trim());
  console.log(`  Unknown email: "${notFoundMsg}"`);

  await browser.close();
})();
