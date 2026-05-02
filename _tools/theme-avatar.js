// Test system theme + avatar upload
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const OUT = path.resolve(__dirname, '..', 'screenshots');

function readBackendCode(email, label, logFile) {
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

  // Force light system pref initially
  const cdpClient = await page.target().createCDPSession();
  await cdpClient.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });

  console.log('\n=== 1. System theme on login ===');
  await page.goto('http://localhost:8080/index.html', { waitUntil: 'networkidle0' });
  await sleep(700);
  await page.evaluate(() => {
    try { localStorage.removeItem('avris-token'); localStorage.removeItem('avris-theme'); } catch(e){}
    window.DEMO_MODE = false;
    var b=document.getElementById('demoBadge');if(b)b.hidden=true;
    document.getElementById('appShell').classList.add('hidden');
    document.getElementById('profileSetup').classList.add('hidden');
    document.getElementById('loginScreen').classList.remove('hidden');
  });
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(500);
  const initial = await page.evaluate(() => ({
    pref: window.themePref,
    body: document.body.dataset.theme,
    loginBtns: Array.from(document.querySelectorAll('.login-theme-btn')).map(b => ({ th: b.dataset.th, active: b.classList.contains('active') })),
  }));
  console.log(`  Default pref="${initial.pref}", body="${initial.body}"`);
  console.log(`  Login buttons:`, JSON.stringify(initial.loginBtns));

  console.log('\n=== 2. Switch system → dark via emulated prefers-color-scheme ===');
  await cdpClient.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });
  await sleep(300);
  const afterMedia = await page.evaluate(() => ({
    pref: window.themePref,
    body: document.body.dataset.theme,
  }));
  console.log(`  pref="${afterMedia.pref}", body="${afterMedia.body}" (expected dark via system)`);

  console.log('\n=== 3. Click Light → manual override ===');
  await page.click('.login-theme-btn[data-th="light"]');
  await sleep(200);
  const afterLight = await page.evaluate(() => ({
    pref: window.themePref,
    body: document.body.dataset.theme,
    storage: localStorage.getItem('avris-theme'),
  }));
  console.log(`  pref="${afterLight.pref}", body="${afterLight.body}", localStorage="${afterLight.storage}"`);

  console.log('\n=== 4. Register + verify + reach profileSetup with avatar uploader ===');
  await page.evaluate(() => {
    document.querySelectorAll('.login-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === 'register'));
  });
  await sleep(150);
  const email = `theme_${Date.now()}@avris.tj`;
  await page.type('#regEmail', email);
  await page.type('#regPass', 'pass1234');
  await page.type('#regPass2', 'pass1234');
  await page.click('#registerForm button[type="submit"]');
  await sleep(1500);
  await sleep(300);
  const code = readBackendCode(email, 'Verification code', '/private/tmp/claude-501/-Users-shahzod/558e2355-3846-4f02-9c14-08187847669d/tasks/bqeuy05u6.output');
  console.log(`  Code: ${code}`);
  for (let i = 0; i < 6; i++) await page.type(`#verifyOtpRow .otp-cell[data-otp-i="${i}"]`, code[i]);
  await sleep(1500);

  const profileState = await page.evaluate(() => ({
    profileVisible: !document.getElementById('profileSetup').classList.contains('hidden'),
    avatarHTML: document.getElementById('pfAvatarPreview').innerHTML.slice(0, 30),
    hint: document.querySelector('#pfAvatarWrap .avatar-edit-hint')?.textContent.trim(),
  }));
  console.log(`  Profile visible=${profileState.profileVisible}, avatar="${profileState.avatarHTML}", hint="${profileState.hint}"`);
  await page.screenshot({ path: path.join(OUT, 'theme-avatar-01-profile.png') });

  console.log('\n=== 5. Upload avatar via input → /api/auth/avatar ===');
  // Build a tiny in-memory PNG
  const tinyPngB64 = 'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAAEElEQVR42mNkYPhfz0AswAYAEgABZcaegwAAAABJRU5ErkJggg==';
  const tmpFile = '/tmp/avris-test-avatar.png';
  fs.writeFileSync(tmpFile, Buffer.from(tinyPngB64, 'base64'));
  const respPromise = page.waitForResponse(r => r.url().includes('/api/auth/avatar') && r.request().method() === 'POST', { timeout: 5000 }).catch(() => null);
  const fileInput = await page.$('#pfAvatarInput');
  await fileInput.uploadFile(tmpFile);
  const resp = await respPromise;
  if (resp) {
    const body = await resp.text().catch(() => '');
    console.log(`  POST /avatar: status=${resp.status()}, body: ${body.slice(0, 250)}`);
  } else {
    console.log(`  POST /avatar: NO RESPONSE captured`);
  }
  await sleep(800);
  const afterUpload = await page.evaluate(() => ({
    hasImg: !!document.querySelector('#pfAvatarPreview img'),
    src: document.querySelector('#pfAvatarPreview img')?.src.slice(0, 50),
  }));
  console.log(`  After upload: img tag=${afterUpload.hasImg}, src starts with: "${afterUpload.src}"`);
  await page.screenshot({ path: path.join(OUT, 'theme-avatar-02-uploaded.png') });

  console.log('\n=== 6. Save profile → app dashboard with avatar in sidebar ===');
  await page.type('#pfLastName', 'Тестов');
  await page.type('#pfFirstName', 'Иван');
  await page.select('#pfSpecialty', 'cardiologist');
  await page.evaluate(() => document.querySelector('#profileForm').dispatchEvent(new Event('submit', { cancelable: true })));
  await sleep(1500);
  const sidebarInfo = await page.evaluate(() => {
    var av = document.querySelector('.sidebar-user .avatar');
    return {
      hasImg: !!(av && av.querySelector('img')),
      bg: av ? getComputedStyle(av).backgroundColor : null,
      size: av ? `${av.offsetWidth}x${av.offsetHeight}` : null,
    };
  });
  console.log(`  Sidebar avatar: img=${sidebarInfo.hasImg}, size=${sidebarInfo.size}`);
  await page.screenshot({ path: path.join(OUT, 'theme-avatar-03-dashboard.png') });

  console.log('\n=== 7. Settings → Profile shows 100px avatar ===');
  await page.evaluate(() => document.querySelector('.nav-link[data-screen="settings"]').click());
  await sleep(300);
  const settingsAvatar = await page.evaluate(() => {
    var av = document.getElementById('setProfileAvatar');
    return {
      size: av ? `${av.offsetWidth}x${av.offsetHeight}` : null,
      hasImg: !!(av && av.querySelector('img')),
    };
  });
  console.log(`  Settings avatar: size=${settingsAvatar.size}, img=${settingsAvatar.hasImg}`);

  console.log('\n=== 8. Settings → Theme: 3 buttons including system ===');
  await page.evaluate(() => {
    var btn = document.querySelector('.set-nav-item[data-sp="setThemePane"]');
    if (btn) btn.click();
  });
  await sleep(250);
  const themeButtons = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('#setThemeToggle .set-theme-btn')).map(b => ({
      th: b.dataset.th,
      active: b.classList.contains('active'),
    }));
  });
  console.log(`  Theme buttons:`, JSON.stringify(themeButtons));
  await page.screenshot({ path: path.join(OUT, 'theme-avatar-04-settings-theme.png') });

  console.log('\n=== 9. Click System → persists, then prefers-color-scheme switches body ===');
  await page.click('#setThemeToggle .set-theme-btn[data-th="system"]');
  await sleep(400);
  await cdpClient.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
  await sleep(300);
  const sysLight = await page.evaluate(() => ({ pref: window.themePref, body: document.body.dataset.theme }));
  console.log(`  After system + media=light: pref="${sysLight.pref}", body="${sysLight.body}"`);
  await cdpClient.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });
  await sleep(300);
  const sysDark = await page.evaluate(() => ({ pref: window.themePref, body: document.body.dataset.theme }));
  console.log(`  After system + media=dark: pref="${sysDark.pref}", body="${sysDark.body}"`);

  console.log('\n=== 10. Topbar toggle (single icon) breaks out of system into manual ===');
  await page.click('#themeToggle');
  await sleep(300);
  const afterToggle = await page.evaluate(() => ({ pref: window.themePref, body: document.body.dataset.theme }));
  console.log(`  After topbar toggle: pref="${afterToggle.pref}", body="${afterToggle.body}" (should be light or dark, NOT system)`);

  await browser.close();
  fs.unlinkSync(tmpFile);
})();
