// build.js — gera o caixa-v9.html publicável a partir do backup em claro.
//
//   node build/build.js
//
// Lê caixa-v9-local-backup.html (dados em claro), cifra tudo com AES-256-GCM
// e injeta a tela de bloqueio de 2 fatores (senha + TOTP).
//
// Senha e segredo TOTP vêm de caixa-secrets.json (gitignored) — este arquivo
// não contém segredo nenhum e pode ser versionado. O segredo TOTP é SEMPRE
// reaproveitado; gerar um novo invalidaria o app autenticador já configurado.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const DIR     = path.resolve(__dirname, '..');
const BACKUP  = path.join(DIR, 'caixa-v9-local-backup.html');
const OUT     = path.join(DIR, 'caixa-v9.html');
const SECRETS = path.join(DIR, 'caixa-secrets.json');
const LOCKJS  = path.join(__dirname, 'lock-code.js');

if (!fs.existsSync(SECRETS)) {
  console.error('ERRO: caixa-secrets.json não encontrado. Ele guarda a senha e o segredo TOTP.');
  process.exit(1);
}
const sec = JSON.parse(fs.readFileSync(SECRETS, 'utf8'));
const PASSWORD = sec.password;
const TOTP_SECRET = sec.totpSecret;
if (!PASSWORD || !TOTP_SECRET) {
  console.error('ERRO: caixa-secrets.json precisa de "password" e "totpSecret".');
  process.exit(1);
}

// ── lê o backup em claro ─────────────────────────────────────
// Normaliza para LF: o importar_xp.py roda em Windows e um CRLF solto
// quebraria os marcadores de texto usados nas injeções abaixo.
let html = fs.readFileSync(BACKUP, 'utf8').replace(/\r\n/g, '\n');

function extract(re, label) {
  const m = html.match(re);
  if (!m) { console.error('FALHOU extrair:', label); process.exit(1); }
  return m;
}
const mSeed  = extract(/const SEED_DATA\s*=\s*(\{[\s\S]*?\})\s*;/, 'SEED_DATA');
const mPort  = extract(/const DEFAULT_PORTFOLIO = \[[\s\S]*?\n\];/, 'DEFAULT_PORTFOLIO');
const mGoals = extract(/const DEFAULT_GOALS = \[[\s\S]*?\n\];/, 'DEFAULT_GOALS');
const mBens  = extract(/const DEFAULT_BENS = \[[\s\S]*?\n\];/, 'DEFAULT_BENS');

const seed  = JSON.parse(mSeed[1]);
const port  = new Function(mPort[0].replace('const DEFAULT_PORTFOLIO =', 'return'))();
const goals = new Function(mGoals[0].replace('const DEFAULT_GOALS =', 'return'))();
const bens  = new Function(mBens[0].replace('const DEFAULT_BENS =', 'return'))();

const payload = {
  seed, portfolio: port, goals, bens,
  cfg: { income: 5120, selic: 10.5, ipca: 4.5, cdi: 10.5 },
  totp: TOTP_SECRET,
};
const totalCarteira = port.reduce((s, p) =>
  s + ((p.qty && p.curPrice) ? p.qty * p.curPrice : (p.applied || 0) * (1 + (p.rent || 0) / 100)), 0);
console.log('Payload — meses:', Object.keys(seed.transactions || {}).length,
            '| posições:', port.length,
            '| carteira: R$', totalCarteira.toLocaleString('pt-BR', {minimumFractionDigits: 2}));

// ── cifra (AES-256-GCM, PBKDF2 310k) ─────────────────────────
const salt = crypto.randomBytes(16);
const iv   = crypto.randomBytes(12);
const key  = crypto.pbkdf2Sync(PASSWORD, salt, 310000, 32, 'sha256');
const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
const ct = Buffer.concat([cipher.update(JSON.stringify(payload), 'utf8'), cipher.final(), cipher.getAuthTag()]);
const blob = Buffer.concat([salt, iv, ct]).toString('base64');
console.log('Blob cifrado:', (blob.length / 1024).toFixed(0) + 'KB');

// ── substitui blocos sensíveis ───────────────────────────────
html = html.replace(mSeed[0], "const SEED_DATA = {};\nconst SEED_ENCRYPTED = '" + blob + "';");
html = html.replace(mPort[0],  'let DEFAULT_PORTFOLIO = [];');
html = html.replace(mGoals[0], 'let DEFAULT_GOALS = [];');
html = html.replace(mBens[0],  'let DEFAULT_BENS = [];');
html = html.replace('{income:5120,selic:10.5,ipca:4.5,cdi:10.5}', '{income:0,selic:10.5,ipca:4.5,cdi:10.5}');

// ── injeta a tela de bloqueio antes do INIT ──────────────────
const lockCode = fs.readFileSync(LOCKJS, 'utf8');
const INIT_MARKER = '// ============================================================\n// INIT';
if (!html.includes(INIT_MARKER)) { console.error('FALHOU: marcador INIT não encontrado'); process.exit(1); }
html = html.replace(INIT_MARKER, lockCode + '\n' + INIT_MARKER);

// ── init assíncrono com gate de desbloqueio ──────────────────
const oldInit = "document.addEventListener('DOMContentLoaded', () => {\n  seedDataIfEmpty();";
const newInit = "document.addEventListener('DOMContentLoaded', async () => {\n  await unlockIfNeeded();\n  seedDataIfEmpty();";
if (!html.includes(oldInit)) { console.error('FALHOU: bloco DOMContentLoaded não encontrado'); process.exit(1); }
html = html.replace(oldInit, newInit);

fs.writeFileSync(OUT, html, 'utf8');
console.log('Escrito:', path.basename(OUT));

// ── validações ───────────────────────────────────────────────
const js = html.match(/<script(?![^>]*src)[^>]*>([\s\S]*?)<\/script>/)[1];
try { new Function(js); console.log('✓ Sintaxe OK'); }
catch (e) { console.error('✗ ERRO DE SINTAXE:', e.message); process.exit(1); }

const outsideBlob = html.replace(/SEED_ENCRYPTED = '[^']*'/, '');
for (const [rotulo, agulha] of [['segredo TOTP', TOTP_SECRET], ['senha', PASSWORD]]) {
  if (outsideBlob.includes(agulha)) { console.error(`✗ ALERTA: ${rotulo} em claro no HTML!`); process.exit(1); }
}
// nenhum produto da carteira pode aparecer fora do blob
const vazou = port.map(p => p.product).filter(n => n && outsideBlob.includes(n));
if (vazou.length) { console.error('✗ ALERTA: posições em claro no HTML:', vazou); process.exit(1); }
console.log('✓ Nada sensível em claro fora do blob cifrado');

// ── roundtrip: decifra e confere ─────────────────────────────
(async () => {
  const { webcrypto } = crypto;
  const raw = Buffer.from(blob, 'base64');
  const s2 = raw.subarray(0, 16), i2 = raw.subarray(16, 28), c2 = raw.subarray(28);
  const mat = await webcrypto.subtle.importKey('raw', new TextEncoder().encode(PASSWORD), 'PBKDF2', false, ['deriveKey']);
  const k = await webcrypto.subtle.deriveKey({name:'PBKDF2', salt: s2, iterations: 310000, hash:'SHA-256'}, mat, {name:'AES-GCM', length:256}, false, ['decrypt']);
  const pt = await webcrypto.subtle.decrypt({name:'AES-GCM', iv: i2}, k, c2);
  const back = JSON.parse(new TextDecoder().decode(pt));
  if (back.totp !== TOTP_SECRET || back.portfolio.length !== port.length) {
    console.error('✗ Roundtrip divergente'); process.exit(1);
  }
  console.log('✓ Roundtrip OK — segredo TOTP preservado, ' + back.portfolio.length + ' posições');
})();
