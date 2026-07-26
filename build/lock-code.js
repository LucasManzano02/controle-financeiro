
// ============================================================
// LOCK SCREEN — 2 fatores: senha (AES-256-GCM) + TOTP (RFC 6238)
// O segredo TOTP vive DENTRO do blob cifrado: não existe em claro no HTML.
// ============================================================
async function decryptPayload(pass) {
  const raw  = Uint8Array.from(atob(SEED_ENCRYPTED), c => c.charCodeAt(0));
  const salt = raw.slice(0, 16), iv = raw.slice(16, 28), ct = raw.slice(28);
  const mat  = await crypto.subtle.importKey('raw', new TextEncoder().encode(pass), 'PBKDF2', false, ['deriveKey']);
  const key  = await crypto.subtle.deriveKey(
    {name:'PBKDF2', salt, iterations:310000, hash:'SHA-256'},
    mat, {name:'AES-GCM', length:256}, false, ['decrypt']);
  const pt = await crypto.subtle.decrypt({name:'AES-GCM', iv}, key, ct);
  return JSON.parse(new TextDecoder().decode(pt));
}

function seedFromPayload(p) {
  const s = p.seed || {};
  if (!localStorage.getItem(LS.tx)    && s.transactions) setTx(s.transactions);
  if (!localStorage.getItem(LS.inst)  && s.installments) setInst(s.installments);
  if (!localStorage.getItem(LS.inc)   && s.income)       setInc(s.income);
  if (!localStorage.getItem(LS.bud)   && s.budgets)      setBudgets(s.budgets);
  if (!localStorage.getItem(LS.goals) && p.goals)        setGoals(p.goals);
  if (!localStorage.getItem(LS.port)  && p.portfolio)    setPortfolio(p.portfolio);
  if (!localStorage.getItem(LS.bens)  && p.bens)         setBens(p.bens);
  if (!localStorage.getItem(LS.cfg)   && p.cfg)          setSettings(p.cfg);
  // populados sempre: refreshPortfolioIfNewVersion() depende deles
  if (p.portfolio) DEFAULT_PORTFOLIO = p.portfolio;
  if (p.goals)     DEFAULT_GOALS     = p.goals;
  if (p.bens)      DEFAULT_BENS      = p.bens;
}

// ── TOTP (RFC 6238) — HMAC-SHA1 via WebCrypto, sem libs ──────
function b32dec(s) {
  const a = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  s = s.replace(/=+$/, '').replace(/\s/g, '').toUpperCase();
  let bits = '';
  for (const c of s) { const v = a.indexOf(c); if (v < 0) continue; bits += v.toString(2).padStart(5, '0'); }
  const by = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) by.push(parseInt(bits.slice(i, i + 8), 2));
  return new Uint8Array(by);
}
async function totpAt(secret, t) {
  const key = b32dec(secret);
  const ctr = Math.floor(t / 30);
  const buf = new ArrayBuffer(8), dv = new DataView(buf);
  dv.setUint32(0, Math.floor(ctr / 4294967296));
  dv.setUint32(4, ctr >>> 0);
  const ck  = await crypto.subtle.importKey('raw', key, {name:'HMAC', hash:'SHA-1'}, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', ck, buf));
  const off = sig[19] & 15;
  const code = ((sig[off] & 127) << 24 | (sig[off+1] & 255) << 16 | (sig[off+2] & 255) << 8 | (sig[off+3] & 255)) % 1000000;
  return String(code).padStart(6, '0');
}
async function totpValid(secret, input) {
  input = String(input).replace(/\D/g, '');
  if (input.length !== 6) return false;
  const now = Math.floor(Date.now() / 1000);
  for (let w = -1; w <= 1; w++) {           // ±30s de tolerância de relógio
    if (await totpAt(secret, now + w * 30) === input) return true;
  }
  return false;
}

function unlockIfNeeded() {
  const hydrated = [LS.tx, LS.port, LS.goals, LS.bens].every(k => localStorage.getItem(k));
  // Carteira nova (import mensal da XP) exige desbloquear para decifrar as posições
  const portOk = Number(localStorage.getItem('caixa_port_version')) === PORT_VERSION;
  if ((hydrated && portOk) || !SEED_ENCRYPTED) return Promise.resolve();
  return new Promise(resolve => {
    const ov = document.createElement('div');
    ov.id = 'lock-screen';
    ov.style.cssText = 'position:fixed;inset:0;z-index:99999;background:var(--bg-primary);display:flex;align-items:center;justify-content:center';
    document.body.appendChild(ov);

    const card = (inner) =>
      '<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:16px;padding:36px;max-width:360px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.12);text-align:center">' + inner + '</div>';

    function finish(payload) { seedFromPayload(payload); ov.remove(); resolve(); }

    // ── Passo 2: código TOTP ────────────────────────────────
    function stepTotp(payload) {
      ov.innerHTML = card(
        '<div style="font-size:34px;margin-bottom:10px">📱</div>' +
        '<div style="font-size:17px;font-weight:600;margin-bottom:6px">Verificação em 2 etapas</div>' +
        '<div style="font-size:12px;color:var(--text-tertiary);margin-bottom:20px">Digite o código de 6 dígitos do seu app autenticador.</div>' +
        '<input type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" id="lock-code" class="form-control" placeholder="000000" ' +
        'style="text-align:center;letter-spacing:8px;font-size:22px;font-variant-numeric:tabular-nums;margin-bottom:10px">' +
        '<button class="btn btn-primary" id="lock-ok" style="width:100%;justify-content:center">Entrar</button>' +
        '<div id="lock-err" style="font-size:11px;color:var(--accent-red);margin-top:10px;min-height:14px"></div>');
      const inp = ov.querySelector('#lock-code'), btn = ov.querySelector('#lock-ok');
      inp.focus();
      const submit = async () => {
        btn.disabled = true; btn.textContent = 'Verificando…';
        if (await totpValid(payload.totp, inp.value)) { finish(payload); return; }
        btn.disabled = false; btn.textContent = 'Entrar';
        ov.querySelector('#lock-err').textContent = 'Código inválido ou expirado.';
        inp.select();
      };
      btn.onclick = submit;
      inp.onkeydown = e => { if (e.key === 'Enter') submit(); };
    }

    // ── Passo 1: senha ──────────────────────────────────────
    function stepPass() {
      ov.innerHTML = card(
        '<div style="font-size:34px;margin-bottom:10px">🔒</div>' +
        '<div style="font-size:17px;font-weight:600;margin-bottom:6px">Caixa<span style="color:var(--accent-purple)">.</span></div>' +
        '<div style="font-size:12px;color:var(--text-tertiary);margin-bottom:20px">Dados cifrados com AES-256. Digite sua senha.</div>' +
        '<input type="password" id="lock-pass" class="form-control" placeholder="Senha" autocomplete="current-password" style="text-align:center;margin-bottom:10px">' +
        '<button class="btn btn-primary" id="lock-btn" style="width:100%;justify-content:center">Continuar</button>' +
        '<div id="lock-err" style="font-size:11px;color:var(--accent-red);margin-top:10px;min-height:14px"></div>');
      const inp = ov.querySelector('#lock-pass'), btn = ov.querySelector('#lock-btn');
      inp.focus();
      const submit = async () => {
        btn.disabled = true; btn.textContent = 'Verificando…';
        try {
          const payload = await decryptPayload(inp.value);
          if (payload.totp) stepTotp(payload);   // exige 2º fator
          else finish(payload);                  // compat: payload sem TOTP
        } catch (e) {
          btn.disabled = false; btn.textContent = 'Continuar';
          ov.querySelector('#lock-err').textContent = 'Senha incorreta.';
          inp.select();
        }
      };
      btn.onclick = submit;
      inp.onkeydown = e => { if (e.key === 'Enter') submit(); };
    }

    stepPass();
  });
}

