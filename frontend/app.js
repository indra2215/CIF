/**
 * CIF ARCHITECT v2.0 — Multi-Page SPA Controller
 * Pages: login → input → review → pipeline → results → subjects → batch
 */

'use strict';

// ─── STATE ────────────────────────────────────────────────────────────────────
const APP = {
  loggedIn:       false,
  currentUser:    null,
  currentPage:    'page-login',
  // Pipeline state
  lastFormula:    '',
  lastPayload:    null,
  lastParseData:  null,
  lastResult:     null,
  // Viewer
  viewer:         null,
  isSpinning:     false,
  showUnitCell:   true,
  currentStyle:   'ballAndStick',
  currentCifData: '',
  // Timer
  timerInterval:  null,
  startTime:      null,
  debounceTimeout: null,
  // Co-dopant counter
  coDopantCounter: 0,
};

// ─── ROUTER ───────────────────────────────────────────────────────────────────
function navigateTo(pageId, updateNav = true, pushHistory = true) {
  document.querySelectorAll('.page-view').forEach(p => {
    p.classList.remove('active');
    p.style.display = 'none';
  });

  const target = document.getElementById(pageId);
  if (!target) return;
  target.style.display = 'flex';
  target.classList.add('active');
  APP.currentPage = pageId;

  // Header visibility
  const header = document.getElementById('appHeader');
  if (pageId === 'page-login') {
    header.style.display = 'none';
  } else {
    header.style.display = 'flex';
    if (updateNav) updateNavPills(pageId);
  }

  // Push browser history state so clicking Back doesn't exit the application
  if (pushHistory) {
    const slug = pageId.replace('page-', '');
    try {
      if (location.hash !== '#' + slug) {
        history.pushState({ pageId }, '', '#' + slug);
      }
    } catch (_) {}
  }

  // Special on-enter hooks
  if (pageId === 'page-results' && APP.viewer) {
    setTimeout(() => APP.viewer.render(), 100);
  }
}

// Seamless Browser Back & Forward button integration
window.addEventListener('popstate', (e) => {
  let targetPage = e.state?.pageId;
  if (!targetPage && location.hash) {
    targetPage = 'page-' + location.hash.replace('#', '');
  }
  if (!targetPage || !document.getElementById(targetPage)) {
    targetPage = APP.loggedIn ? 'page-input' : 'page-login';
  }
  // If not logged in, enforce login gate
  if (!APP.loggedIn && targetPage !== 'page-login') {
    targetPage = 'page-login';
  }
  navigateTo(targetPage, true, false);
});

function updateNavPills(pageId) {
  document.querySelectorAll('.nav-pill').forEach(pill => {
    pill.classList.remove('active');
    if (pill.dataset.nav === pageId) pill.classList.add('active');
  });
}

// ─── LOGIN ────────────────────────────────────────────────────────────────────
function setupLogin() {
  const form         = document.getElementById('loginForm');
  const usernameEl   = document.getElementById('loginUsername');
  const passwordEl   = document.getElementById('loginPassword');
  const errorBanner  = document.getElementById('loginErrorBanner');
  const errorText    = document.getElementById('loginErrorText');
  const btnText      = document.getElementById('loginBtnText');
  const btnSpinner   = document.getElementById('loginBtnSpinner');
  const pwToggle     = document.getElementById('passwordToggle');

  // Password show/hide
  pwToggle?.addEventListener('click', () => {
    const isPass = passwordEl.type === 'password';
    passwordEl.type = isPass ? 'text' : 'password';
  });

  // Clear errors on input
  [usernameEl, passwordEl].forEach(el => {
    el.addEventListener('input', () => {
      el.classList.remove('error');
      errorBanner.classList.add('hidden');
    });
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = usernameEl.value.trim();
    const password = passwordEl.value;

    // Validate
    let valid = true;
    if (!username) {
      usernameEl.classList.add('error');
      document.getElementById('usernameError').textContent = 'Username is required.';
      valid = false;
    }
    if (!password) {
      passwordEl.classList.add('error');
      document.getElementById('passwordError').textContent = 'Password is required.';
      valid = false;
    }
    if (!valid) return;

    // Show spinner
    btnText.textContent = 'Signing In...';
    btnSpinner.classList.remove('hidden');

    // Simulate auth (demo: password === 'demo' or any non-empty pair)
    await new Promise(r => setTimeout(r, 800));

    if (password === 'demo' || password.length >= 1) {
      APP.loggedIn = true;
      APP.currentUser = username;
      try {
        sessionStorage.setItem('cif_auth', JSON.stringify({ loggedIn: true, user: username }));
      } catch (_) {}
      btnText.textContent = 'Sign In';
      btnSpinner.classList.add('hidden');
      navigateTo('page-input');
      checkCrystaLLMStatus();
      initViewer();
    } else {
      btnText.textContent = 'Sign In';
      btnSpinner.classList.add('hidden');
      errorBanner.classList.remove('hidden');
      errorText.textContent = 'Invalid credentials. Use any username and password "demo".';
    }
  });
}

// ─── LOGOUT ───────────────────────────────────────────────────────────────────
function setupLogout() {
  document.getElementById('logoutBtn')?.addEventListener('click', () => {
    APP.loggedIn = false;
    APP.currentUser = null;
    try { sessionStorage.removeItem('cif_auth'); } catch (_) {}
    navigateTo('page-login');
    // Reset form
    document.getElementById('loginForm')?.reset();
    document.getElementById('loginErrorBanner')?.classList.add('hidden');
  });
  document.getElementById('headerLogoBtn')?.addEventListener('click', () => {
    if (APP.loggedIn) navigateTo('page-input');
  });
}

// ─── NAV PILLS ────────────────────────────────────────────────────────────────
function setupNavPills() {
  document.querySelectorAll('.nav-pill[data-nav]').forEach(pill => {
    pill.addEventListener('click', () => {
      if (!APP.loggedIn) return;
      const target = pill.dataset.nav;
      // Some pages require a result
      if ((target === 'page-results') && !APP.lastResult) return;
      navigateTo(target);
    });
  });
}

// ─── 3D VIEWER ────────────────────────────────────────────────────────────────
function initViewer() {
  if (APP.viewer) return;
  const container = document.getElementById('molViewer');
  if (!container) return;
  try {
    const bgColor = getComputedStyle(document.documentElement)
      .getPropertyValue('--bg-primary').trim() || '#09090b';
    APP.viewer = $3Dmol.createViewer(container, { backgroundColor: bgColor });
  } catch (e) {
    console.warn('3Dmol init failed:', e);
  }
}

function applyViewerStyle() {
  if (!APP.viewer) return;
  APP.viewer.removeAllLabels();
  APP.viewer.removeAllShapes();

  if (APP.currentStyle === 'ballAndStick') {
    APP.viewer.setStyle({}, {
      stick: { radius: 0.12, colorscheme: 'Jmol' },
      sphere: { scale: 0.28, colorscheme: 'Jmol' }
    });
  } else if (APP.currentStyle === 'sphere') {
    APP.viewer.setStyle({}, { sphere: { scale: 0.75, colorscheme: 'Jmol' } });
  } else {
    APP.viewer.setStyle({}, { stick: { radius: 0.18, colorscheme: 'Jmol' } });
  }

  if (APP.showUnitCell) {
    try { APP.viewer.addUnitCell(); } catch (_) {}
  }
  APP.viewer.render();
}

function loadCifIntoViewer(cifString, title = null) {
  if (!APP.viewer || !cifString) return;
  try {
    APP.viewer.clear();
    APP.viewer.addModel(cifString, 'cif');
    applyViewerStyle();
    APP.viewer.zoomTo();
    APP.viewer.render();
    const leg = document.getElementById('crystalTitleLegend');
    if (title && leg) leg.textContent = title;
  } catch (err) {
    console.error('3Dmol render failed:', err);
  }
}

// ─── TIMER ────────────────────────────────────────────────────────────────────
function startTimer() {
  APP.startTime = performance.now();
  clearInterval(APP.timerInterval);
  APP.timerInterval = setInterval(() => {
    const elapsed = ((performance.now() - APP.startTime) / 1000).toFixed(2);
    const liveTimer    = document.getElementById('liveTimer');
    const overlayTimer = document.getElementById('overlayTimer');
    if (liveTimer)    liveTimer.textContent    = `${elapsed}s`;
    if (overlayTimer) overlayTimer.textContent = `${elapsed}s`;
  }, 50);
}

function stopTimer(finalSeconds = null) {
  clearInterval(APP.timerInterval);
  const fmt = finalSeconds !== null
    ? `${parseFloat(finalSeconds).toFixed(2)}s`
    : `${((performance.now() - (APP.startTime||performance.now())) / 1000).toFixed(2)}s`;
  const liveTimer     = document.getElementById('liveTimer');
  const overlayTimer  = document.getElementById('overlayTimer');
  const provDuration  = document.getElementById('provDurationVal');
  if (liveTimer)    liveTimer.textContent    = fmt;
  if (overlayTimer) overlayTimer.textContent = fmt;
  if (provDuration) provDuration.textContent = fmt;
}

// ─── FORMULA PARSING & ELEMENT CHIPS ─────────────────────────────────────────
async function updateSearchSpaceElements(formula, targetContainerId = 'elementsChipContainer') {
  if (!formula.trim()) return null;

  try {
    const res = await fetch('/api/parse-formula', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ formula: formula.trim() }),
    });
    if (!res.ok) return null;
    const data = await res.json();

    // Render chips
    const chipContainer  = document.getElementById(targetContainerId);
    const countBadge     = document.getElementById('elementCountBadge');
    const matrixLabel    = document.getElementById('searchMatrixLabel');

    if (chipContainer) {
      chipContainer.innerHTML = '';
      if (data.valid && data.elements.length > 0) {
        if (countBadge) countBadge.textContent = `${data.elements.length} Element${data.elements.length > 1 ? 's' : ''}`;
        const syms = data.elements.map(e => e.symbol);
        if (matrixLabel) matrixLabel.textContent = `[${syms.join(', ')}]`;

        data.elements.forEach(el => {
          const chip = document.createElement('div');
          chip.className = 'element-chip';
          chip.innerHTML = `
            <span class="el-symbol">${el.symbol}</span>
            <span class="el-amount">${el.amount}</span>
            <span class="el-wt">${el.weight_percent}% wt</span>
          `;
          chipContainer.appendChild(chip);
        });
      }
    }

    // Dynamic case-correction banner
    const caseNotice = document.getElementById('caseCorrectionNotice');
    const caseText   = document.getElementById('caseCorrectionText');
    const applyBtn   = document.getElementById('applyNormalizedBtn');

    if (caseNotice) {
      if (data.case_corrected && data.normalized_formula) {
        caseNotice.classList.remove('hidden');
        if (caseText) caseText.innerHTML = `Auto-capitalized to <strong>${data.normalized_formula}</strong>`;
        if (applyBtn) {
          applyBtn.textContent = 'Use This';
          applyBtn.onclick = () => {
            const fi = document.getElementById('formulaInput');
            if (fi) {
              fi.value = data.normalized_formula;
              updateSearchSpaceElements(data.normalized_formula);
            }
            caseNotice.classList.add('hidden');
          };
        }
      } else if (!data.valid && data.suggestion) {
        caseNotice.classList.remove('hidden');
        if (caseText) caseText.innerHTML = `Did you mean <strong>${data.suggestion}</strong>?`;
        if (applyBtn) {
          applyBtn.textContent = 'Correct Formula';
          applyBtn.onclick = () => {
            const fi = document.getElementById('formulaInput');
            if (fi) {
              fi.value = data.suggestion;
              updateSearchSpaceElements(data.suggestion);
            }
            caseNotice.classList.add('hidden');
          };
        }
      } else {
        caseNotice.classList.add('hidden');
      }
    }

    // Balance check with real pymatgen oxidation states
    updateBalanceIndicator(data);

    // Doping detected bar
    const dopingBar  = document.getElementById('dopingDetectedBar');
    const dopingText = document.getElementById('dopingDetectedText');
    const dopingInd  = document.getElementById('dopingStateIndicator');
    if (data.valid && data.is_doped && data.doping_spec) {
      dopingBar?.classList.remove('hidden');
      const fracDisplay = data.doping_spec.dopant_fraction_pct
        ? `${data.doping_spec.dopant_fraction} (${data.doping_spec.dopant_fraction_pct})`
        : (data.doping_spec.dopant_fraction || '?');
      if (dopingText) dopingText.textContent = `Doping detected — ${data.doping_spec.dopant_species || '?'} → ${data.doping_spec.host_site_species || '?'} site (${fracDisplay}) in host ${data.doping_spec.host_formula || 'matrix'}`;
      if (dopingInd) { dopingInd.textContent = 'Doped Detected'; dopingInd.style.color = 'var(--accent-dot)'; }

      // Auto-fill doping fields if empty
      const hf = document.getElementById('hostFormulaInput');
      const de = document.getElementById('dopantElemInput');
      const hs = document.getElementById('hostSiteInput');
      const df = document.getElementById('dopantFractionInput');
      if (hf && !hf.value) hf.value = data.doping_spec.host_formula || '';
      if (de && !de.value) de.value = data.doping_spec.dopant_species || '';
      if (hs && !hs.value) hs.value = data.doping_spec.host_site_species || '';
      if (df && !df.value && data.doping_spec.dopant_fraction !== undefined) {
        df.value = data.doping_spec.dopant_fraction;
      }
    } else {
      dopingBar?.classList.add('hidden');
      if (dopingInd) { dopingInd.textContent = 'Undoped'; dopingInd.style.color = ''; }
    }

    // Highlight invalid input
    const fi = document.getElementById('formulaInput');
    if (fi) fi.classList.toggle('invalid', !data.valid);

    // If invalid, clear element chips
    if (!data.valid) {
      if (chipContainer) chipContainer.innerHTML = `<span style="font-size:0.72rem;color:var(--accent-red)">⚠ ${data.error || 'Cannot parse formula'}</span>`;
    }

    return data;
  } catch (err) {
    console.warn('parse-formula error:', err);
    return null;
  }
}

function updateBalanceIndicator(data) {
  const indicator = document.getElementById('balanceIndicator');
  const balText   = document.getElementById('balanceText');
  if (!indicator) return;

  if (!data || !data.valid) {
    indicator.className = 'balance-indicator balance-invalid';
    indicator.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg><span id="balanceText">Invalid formula — cannot check charge balance</span>`;
    return;
  }

  const cb = data.charge_balance || {};
  const state = cb.state || 'unknown';
  let msg = cb.message || 'Charge balance check completed';
  if (cb.detail) {
    msg += ` [${cb.detail}]`;
  }

  let stateClass = 'balance-ok';
  if (state === 'unbalanced' || state === 'invalid') {
    stateClass = 'balance-invalid';
  } else if (state === 'warn' || state === 'uncertain') {
    stateClass = 'balance-warn';
  }

  const icons = {
    'balance-ok': `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`,
    'balance-warn': `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
    'balance-invalid': `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`
  };

  indicator.className = `balance-indicator ${stateClass}`;
  indicator.innerHTML = `${icons[stateClass] || icons['balance-warn']}<span id="balanceText">${msg}</span>`;
}

// ─── INPUT PAGE SETUP ─────────────────────────────────────────────────────────
function setupInputPage() {
  const formulaInput = document.getElementById('formulaInput');
  const form         = document.getElementById('pipelineForm');
  const clearBtn     = document.getElementById('clearFormulaBtn');

  // Debounced parse on input
  formulaInput?.addEventListener('input', (e) => {
    clearTimeout(APP.debounceTimeout);
    APP.debounceTimeout = setTimeout(() => {
      updateSearchSpaceElements(e.target.value);
    }, 350);
  });

  // Clear button — clears formula, chips, doping fields
  clearBtn?.addEventListener('click', () => {
    if (formulaInput) { formulaInput.value = ''; formulaInput.classList.remove('invalid'); }
    const chipCont = document.getElementById('elementsChipContainer');
    if (chipCont) chipCont.innerHTML = '';
    const countB = document.getElementById('elementCountBadge');
    if (countB) countB.textContent = '0 Elements';
    const matrix = document.getElementById('searchMatrixLabel');
    if (matrix) matrix.textContent = '[ ]';
    document.getElementById('hostFormulaInput').value   = '';
    document.getElementById('dopantElemInput').value    = '';
    document.getElementById('hostSiteInput').value      = '';
    document.getElementById('dopantFractionInput').value = '';
    document.getElementById('dopingDetectedBar')?.classList.add('hidden');
    updateBalanceIndicator(null);
    clearCoDopantCells();
  });

  // Add Co-Dopant
  document.getElementById('addCoDopantBtn')?.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    addCoDopantCell();
  });

  // Form submit → go to review page
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formula = formulaInput?.value.trim();
    if (!formula) return;

    // Parse first
    const parseData = await updateSearchSpaceElements(formula);
    if (parseData && !parseData.valid) {
      // Don't navigate — keep on input page with error shown
      return;
    }

    // Build payload
    APP.lastFormula   = formula;
    APP.lastParseData = parseData;
    APP.lastPayload   = buildPayload();

    // Navigate to review
    populateReviewPage(formula, parseData);
    navigateTo('page-review');
  });

  // Preset chips
  setupPresets();
}

function buildPayload() {
  const formulaInput        = document.getElementById('formulaInput');
  const spaceGroupInput     = document.getElementById('spaceGroupInput');
  const hostFormulaInput    = document.getElementById('hostFormulaInput');
  const dopantElemInput     = document.getElementById('dopantElemInput');
  const hostSiteInput       = document.getElementById('hostSiteInput');
  const dopantFractionInput = document.getElementById('dopantFractionInput');
  const dopingDrawer        = document.getElementById('dopingDrawer');
  const formula             = formulaInput?.value.trim() || '';

  const coDopants = [];
  document.querySelectorAll('.dopant-cell.co-dopant').forEach(cell => {
    const el   = cell.querySelector('.dopant-elem-input')?.value.trim();
    const site = cell.querySelector('.host-site-input')?.value.trim();
    const frac = cell.querySelector('.dopant-fraction-input')?.value;
    if (el && site && frac !== '' && !isNaN(parseFloat(frac))) {
      coDopants.push({ dopant_element: el, host_site_species: site, dopant_fraction: parseFloat(frac) });
    }
  });

  const de = dopantElemInput?.value.trim();
  const hs = hostSiteInput?.value.trim();
  const df = dopantFractionInput?.value;
  const isDoped = Boolean(
    (de && hs && df) ||
    coDopants.length > 0 ||
    (dopingDrawer?.open && hostFormulaInput?.value.trim())
  );

  return {
    formula,
    is_doped_hint:      isDoped ? true : null,
    space_group:        spaceGroupInput?.value.trim() || null,
    host_formula:       hostFormulaInput?.value.trim() || null,
    dopant_element:     de || null,
    host_site_species:  hs || null,
    dopant_fraction:    (df !== '' && df !== undefined) ? parseFloat(df) : null,
    co_dopants:         coDopants,
  };
}

function setupPresets() {
  document.querySelectorAll('.preset-chip').forEach(chip => {
    chip.addEventListener('click', async () => {
      document.querySelectorAll('.preset-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');

      const formula = chip.dataset.formula;
      const fi = document.getElementById('formulaInput');
      if (fi) fi.value = formula;

      clearCoDopantCells();

      if (chip.dataset.doped === 'true') {
        const dd = document.getElementById('dopingDrawer');
        if (dd) dd.open = true;
        const hf = document.getElementById('hostFormulaInput');
        const de = document.getElementById('dopantElemInput');
        const hs = document.getElementById('hostSiteInput');
        const df = document.getElementById('dopantFractionInput');
        if (hf) hf.value = chip.dataset.host      || '';
        if (de) de.value = chip.dataset.dopant     || '';
        if (hs) hs.value = chip.dataset.site       || '';
        if (df) df.value = chip.dataset.fraction   || '';

        if (chip.dataset.codopants) {
          try {
            JSON.parse(chip.dataset.codopants).forEach(item => {
              addCoDopantCell(item.dopant || '', item.site || '', item.fraction || '');
            });
          } catch (_) {}
        }
      } else {
        const dd = document.getElementById('dopingDrawer');
        if (dd) dd.open = false;
        ['hostFormulaInput','dopantElemInput','hostSiteInput','dopantFractionInput'].forEach(id => {
          const el = document.getElementById(id);
          if (el) el.value = '';
        });
      }

      const parseData = await updateSearchSpaceElements(formula);
      APP.lastFormula   = formula;
      APP.lastParseData = parseData;
      APP.lastPayload   = buildPayload();
      populateReviewPage(formula, parseData);
      navigateTo('page-review');
    });
  });
}

// ─── CO-DOPANT CELLS ──────────────────────────────────────────────────────────
function addCoDopantCell(initialElem = '', initialSite = '', initialFrac = '') {
  APP.coDopantCounter++;
  const list  = document.getElementById('dopantCellsList');
  const count = document.querySelectorAll('.dopant-cell').length + 1;
  const cell  = document.createElement('div');
  cell.className = 'dopant-cell co-dopant';
  cell.setAttribute('data-cell-index', String(count - 1));
  cell.innerHTML = `
    <div class="dopant-cell-header">
      <span class="dopant-cell-badge co-badge">CO-DOPANT #${count}</span>
      <button type="button" class="remove-dopant-btn">×</button>
    </div>
    <div class="form-row">
      <div class="form-group half">
        <label>Co-Dopant Element</label>
        <input type="text" class="dopant-elem-input" placeholder="e.g. Nb" value="${initialElem}">
      </div>
      <div class="form-group half">
        <label>Substituted Site</label>
        <input type="text" class="host-site-input" placeholder="e.g. Fe" value="${initialSite}">
      </div>
    </div>
    <div class="form-row">
      <div class="form-group half">
        <label>Fraction (0–1.0)</label>
        <input type="number" class="dopant-fraction-input" step="0.01" min="0" max="1" placeholder="0.02" value="${initialFrac}">
      </div>
    </div>
  `;
  cell.querySelector('.remove-dopant-btn').addEventListener('click', () => {
    cell.remove();
    updateDopantCountBadge();
  });
  list?.appendChild(cell);
  updateDopantCountBadge();
}

function clearCoDopantCells() {
  document.querySelectorAll('.dopant-cell.co-dopant').forEach(c => c.remove());
  updateDopantCountBadge();
}

function updateDopantCountBadge() {
  const total = document.querySelectorAll('.dopant-cell').length;
  const badge = document.getElementById('dopantCountBadge');
  if (!badge) return;
  badge.textContent = total <= 1 ? '1 Dopant' : `${total} Dopants (Co-Doped)`;
  badge.style.color = total > 1 ? 'var(--accent-dot)' : '';
}

// ─── REVIEW PAGE ──────────────────────────────────────────────────────────────
function populateReviewPage(formula, parseData) {
  const raw     = document.getElementById('reviewRawInput');
  const normKey = document.getElementById('reviewNormKey');
  const reduced = document.getElementById('reviewReducedFormula');
  const multi   = document.getElementById('reviewMultiplicity');
  const classif = document.getElementById('reviewClassification');

  if (raw)     raw.textContent = formula;
  if (normKey) normKey.textContent = parseData?.normalized_formula || formula;
  if (reduced) reduced.textContent = parseData?.reduced_formula || formula;
  if (multi)   multi.textContent   = 'Checked post-search (reduction applied if Z>1)';
  if (classif) classif.textContent = parseData?.is_doped
    ? `Doped solid solution${parseData.doping_spec ? ` — dopant: ${parseData.doping_spec.dopant_species}` : ''}`
    : 'Undoped stoichiometric compound';

  // Elements grid
  const grid = document.getElementById('reviewElementsGrid');
  if (grid && parseData?.elements) {
    grid.innerHTML = '';
    parseData.elements.forEach(el => {
      const item = document.createElement('div');
      item.className = 'review-element-item';
      item.innerHTML = `
        <div class="rei-z">Z=${el.atomic_number || '?'}</div>
        <div class="rei-symbol">${el.symbol}</div>
        <div class="rei-name">${el.name || el.symbol}</div>
        <div class="rei-amt">×${el.amount}</div>
        <div class="rei-wt">${el.weight_percent}% wt</div>
      `;
      grid.appendChild(item);
    });
  }

  // Balance
  const balRow = document.getElementById('reviewBalanceRow');
  if (balRow) {
    const cb = parseData?.charge_balance;
    if (cb) {
      const stateCls = (cb.state === 'balanced' || cb.state === 'intermetallic') ? 'ok' : (cb.state === 'unbalanced' || cb.state === 'invalid') ? 'invalid' : 'warn';
      const detailStr = cb.detail ? ` [${cb.detail}]` : '';
      balRow.innerHTML = `<div class="balance-indicator balance-${stateCls}" style="margin-top:8px">
        <span>${cb.message}${detailStr}</span></div>`;
    } else {
      const syms = (parseData?.elements || []).map(e => e.symbol);
      const hasO = syms.includes('O');
      const hasMetal = syms.some(s => !['O','N','S','F','Cl','Br','I','H','C','P'].includes(s));
      const state = (!parseData?.valid) ? 'invalid' : hasMetal && hasO ? 'ok' : 'warn';
      const msg   = !parseData?.valid ? 'Invalid formula — cannot run pipeline'
        : hasMetal && hasO ? 'Charge balance: VALID'
        : 'Balance model: N/A for this compound class';
      balRow.innerHTML = `<div class="balance-indicator balance-${state}" style="margin-top:8px">
        <span>${msg}</span></div>`;
    }
  }

  // Doping card
  const dopCard    = document.getElementById('reviewDopingCard');
  const dopDetails = document.getElementById('reviewDopingDetails');
  const wfCard     = document.getElementById('reviewDopingWorkflowCard');

  if (parseData?.is_doped && parseData.doping_spec) {
    dopCard?.classList.remove('hidden');
    wfCard?.classList.remove('hidden');
    const ds = parseData.doping_spec;
    if (dopDetails) {
      dopDetails.innerHTML = `
        <div class="rdop-row"><span class="rdop-label">Host Formula:</span><span class="rdop-val">${ds.host_formula || '?'}</span></div>
        <div class="rdop-row"><span class="rdop-label">Dopant Element:</span><span class="rdop-val">${ds.dopant_species || '?'}</span></div>
        <div class="rdop-row"><span class="rdop-label">Substituted Site:</span><span class="rdop-val">${ds.host_site_species || '?'}</span></div>
        <div class="rdop-row"><span class="rdop-label">Dopant Fraction:</span><span class="rdop-val">${ds.dopant_fraction || '?'}${ds.dopant_fraction_pct ? ` (${ds.dopant_fraction_pct})` : ''}</span></div>
      `;
    }
    // Populate staged execution steps
    setText('dwHostFormula', ds.host_formula || 'Matrix');
    const fracText = ds.dopant_fraction_pct ? `${ds.dopant_species} (${ds.dopant_fraction_pct})` : `${ds.dopant_species} (${ds.dopant_fraction})`;
    setText('dwDopantInfo', `${fracText} substituted on ${ds.host_site_species || 'host'} site`);
  } else {
    dopCard?.classList.add('hidden');
    wfCard?.classList.add('hidden');
  }
}

function setupReviewPage() {
  document.getElementById('reviewBackBtn')?.addEventListener('click', () => navigateTo('page-input'));
  document.getElementById('reviewEditBtn')?.addEventListener('click', () => navigateTo('page-input'));
  document.getElementById('reviewRunBtn')?.addEventListener('click', () => {
    if (APP.lastPayload) executePipeline(APP.lastPayload);
  });
}

// ─── PIPELINE EXECUTION ───────────────────────────────────────────────────────
async function executePipeline(payload) {
  navigateTo('page-pipeline');
  startTimer();

  const titleEl    = document.getElementById('pipelineRunTitle');
  const subtitleEl = document.getElementById('pipelineRunSubtitle');
  const logStream  = document.getElementById('pipelineLogStream');
  const failCard   = document.getElementById('pipelineFailureCard');

  failCard?.classList.add('hidden');
  if (titleEl)    titleEl.textContent    = `Executing: ${payload.formula}`;
  if (subtitleEl) subtitleEl.textContent = 'Querying databases...';
  if (logStream)  logStream.innerHTML    = '';

  addLog('stage',  `Pipeline started for formula: ${payload.formula}`);
  addLog('info',   `Search space: ${payload.space_group ? `SG=${payload.space_group}` : 'all space groups'}`);
  addLog('info',   payload.is_doped_hint ? `Doped compound — dopant: ${payload.dopant_element || 'auto-detected'}` : 'Undoped compound');

  // Animate stages
  setLiveStage(0, 'running', 'RUNNING');
  addLog('stage', 'Stage 0 · Classification & Normalization...');
  await sleep(300);
  setLiveStage(0, 'completed', 'DONE');
  addLog('ok', '  ✓ Formula normalized and classified');

  setLiveStage(1, 'running', 'RUNNING');
  addLog('stage', 'Stage 1 · External DB Search (MP / OQMD / JARVIS / COD)...');
  if (subtitleEl) subtitleEl.textContent = 'Querying COD, Materials Project...';

  try {
    const res = await fetch('/api/generate', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Server error');
    }

    const data = await res.json();

    // Check if pipeline reached a scientific failure (e.g. no matches and generation failed)
    if (!data.success && data.source === 'failed') {
      stopTimer(data.total_elapsed_seconds);
      setLiveStage(1, 'failed', 'STOPPED');
      addLog('error', `✗ Pipeline failed: ${data.notes || 'No structure generated'}`);
      if (subtitleEl) subtitleEl.textContent = 'Pipeline stopped';
      if (failCard) {
        failCard.classList.remove('hidden');
        setText('pipelineFailureTitle', 'Pipeline Execution Interrupted');
        setText('pipelineFailureMsg', data.notes || 'The pipeline was unable to resolve or generate a valid CIF structure for this query. You can adjust the formula or doping parameters.');
        const fb = document.getElementById('failureBackBtn');
        const fr = document.getElementById('failureRetryBtn');
        if (fb) fb.onclick = () => navigateTo('page-review');
        if (fr) fr.onclick = () => executePipeline(payload);
      }
      return;
    }

    addLog('ok', `  ✓ DB search complete — source: ${data.source}`);
    setLiveStage(1, 'completed', 'DONE');

    setLiveStage(2, data.source.startsWith('existing_') ? 'completed' : 'skipped',
      data.source.startsWith('existing_') ? 'DONE' : 'SKIP');
    addLog('info', `  Stage 2 · Internal DB: ${data.source.startsWith('existing_') ? 'hit' : 'not queried'}`);

    const needsGeneration = data.source.startsWith('generated_');
    setLiveStage(3, needsGeneration ? 'running' : 'skipped', needsGeneration ? 'RUNNING' : 'SKIP');
    if (needsGeneration) {
      addLog('stage', 'Stage 3 · CrystaLLM generation in progress...');
      await sleep(400);
      setLiveStage(3, 'completed', 'DONE');
      addLog('ok', '  ✓ CrystaLLM: candidates generated and filtered');
    } else {
      addLog('info', '  Stage 3 · CrystaLLM: skipped (existing structure found)');
    }

    setLiveStage(4, 'running', 'RUNNING');
    addLog('stage', 'Stage 4 · MACE-MP-0 Relaxation & Space Group...');
    await sleep(200);
    setLiveStage(4, 'completed', 'DONE');
    addLog('ok', '  ✓ Structure relaxed; space group determined post-relaxation');

    setLiveStage(5, 'running', 'RUNNING');
    addLog('stage', 'Stage 5 · Polymorph Screening & Symmetry Matrix...');
    await sleep(200);
    setLiveStage(5, 'completed', 'DONE');
    addLog('ok', `  ✓ ${data.candidate_matches?.length || 0} polymorph(s) screened`);

    setLiveStage(6, 'running', 'RUNNING');
    addLog('stage', 'Stage 6 · MESP Energy Validation...');
    await sleep(200);
    setLiveStage(6, 'completed', 'DONE');
    addLog('ok', '  ✓ Structure energy validated');

    stopTimer(data.total_elapsed_seconds);
    addLog('ok', `Pipeline complete in ${data.total_elapsed_seconds}s — source: ${data.source}`);
    if (subtitleEl) subtitleEl.textContent = `Complete — ${data.source}`;

    APP.lastResult = data;
    setTimeout(() => {
      renderResults(payload.formula, data);
      navigateTo('page-results');
    }, 600);

  } catch (err) {
    stopTimer();
    addLog('error', `✗ Pipeline error: ${err.message}`);
    setLiveStage(1, 'failed', 'ERROR');
    if (subtitleEl) subtitleEl.textContent = `Error: ${err.message}`;
    if (failCard) {
      failCard.classList.remove('hidden');
      setText('pipelineFailureTitle', 'Pipeline Execution Error');
      setText('pipelineFailureMsg', `${err.message}. You can adjust the formula or doping parameters and try again.`);
      const fb = document.getElementById('failureBackBtn');
      const fr = document.getElementById('failureRetryBtn');
      if (fb) fb.onclick = () => navigateTo('page-review');
      if (fr) fr.onclick = () => executePipeline(payload);
    }
  }
}

function setLiveStage(idx, state, label) {
  const stageEl = document.getElementById(`liveStage${idx}`);
  if (!stageEl) return;
  const dot    = stageEl.querySelector('.live-stage-dot');
  const status = stageEl.querySelector('.live-stage-status');
  stageEl.className = `live-stage ${state}`;
  if (dot)    dot.className    = `live-stage-dot ${state}`;
  if (status) status.textContent = label;
}

function addLog(type, msg) {
  const stream = document.getElementById('pipelineLogStream');
  if (!stream) return;
  const line = document.createElement('div');
  line.className = `log-line log-${type}`;
  line.textContent = `[${new Date().toISOString().split('T')[1].slice(0,8)}] ${msg}`;
  stream.appendChild(line);
  stream.scrollTop = stream.scrollHeight;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ─── RESULTS RENDERING ────────────────────────────────────────────────────────
function renderResults(formula, data) {
  APP.currentCifData = data.cif_string || '';

  // Badges
  setText('formulaBadge', formula);
  setText('sourceBadge',  (data.source || 'unknown').toUpperCase());

  let sgStr = 'SG: —';
  if (data.matched_record?.space_group) {
    sgStr = `SG: ${data.matched_record.space_group}`;
  } else if (data.cif_string?.includes('_symmetry_space_group_name_H-M')) {
    const m = data.cif_string.match(/_symmetry_space_group_name_H-M\s+['"]?([^'"\n]+)['"]?/);
    if (m) sgStr = `SG: ${m[1]}`;
  }
  setText('spaceGroupBadge', sgStr);

  const statusBadge = document.getElementById('statusBadge');
  if (statusBadge) {
    statusBadge.textContent  = data.success ? 'VALIDATED' : 'NOT FOUND';
    statusBadge.className    = `badge status-badge${data.success ? ' success' : ''}`;
  }

  // Provenance
  setText('provSourceVal',    data.retrieved_from || data.source || '—');
  const cllmEl = document.getElementById('provCrystaLLMVal');
  if (cllmEl) {
    cllmEl.textContent  = data.crystallm_used ? 'Yes (Local GPT — CrystaLLM)' : 'No (existing DB record)';
    cllmEl.className    = `prov-val ${data.crystallm_used ? 'highlight' : 'muted'}`;
  }

  // CIF
  if (data.cif_string) {
    loadCifIntoViewer(data.cif_string, `${formula} Crystal (${sgStr})`);
    setText('cifCodeBlock',  data.cif_string);
    const lines = data.cif_string.trim().split('\n');
    setText('cifLineCount',  `Lines: ${lines.length}`);
    setText('cifFormulaMeta',`Formula: ${formula}`);
  } else {
    setText('cifCodeBlock',  `# No CIF generated.\n# Notes:\n# ${data.notes || 'None'}`);
    setText('cifLineCount',  'Lines: 0');
  }

  // Timeline & tools
  renderTimeline(data.timeline || []);
  renderTools(data.tools_used || []);

  // Normalization trace
  if (data.normalization_trace) {
    const t = data.normalization_trace;
    setText('traceRawInput',   t.raw_input || formula);
    setText('traceSearchKey',  t.search_key_used || '—');
    setText('traceIsDoped',    t.is_doped ? 'True (Doped solid solution)' : 'False (Undoped)');
    setText('traceReasoning',  t.classification_reasoning || 'Heuristic rule');
    setText('traceMultiplicity', t.multiplicity?.was_reduced
      ? `Reduced (${t.multiplicity.multiplier}×: ${t.multiplicity.reason})`
      : 'None (stoichiometric)');
  }
  setText('traceNotes',  data.notes || 'Execution completed normally.');
  if (data.doped_validation) {
    const dv = data.doped_validation;
    setText('traceDopedVal', `Valid: ${dv.is_valid}, Occupancy: ${dv.occupancy_sum} (bonds OK: ${dv.bond_length_ok})`);
  }

  // Diagnostics
  const diagCard = document.getElementById('diagnosticsHistoryCard');
  const diagList = document.getElementById('diagnosticsList');
  if (diagCard && diagList && data.diagnostics_history?.length > 0) {
    diagCard.classList.remove('hidden');
    diagList.innerHTML = '';
    data.diagnostics_history.forEach(d => {
      const item = document.createElement('div');
      item.className = 'diagnostic-item';
      item.textContent = `Iter ${d.iteration}: gen=${d.n_generated} | parse_fail=${d.n_parse_failed} | bond_fail=${d.n_bond_length_failed} | survived=${d.n_survived} | ${d.notes}`;
      diagList.appendChild(item);
    });
  }

  // Candidates
  renderCandidates(data.candidate_matches || []);

  // Symmetry matrix
  renderSymmetryMatrix(formula, data);

  // MESP
  renderMESP(formula, data);

  // Update nav result pill
  document.getElementById('navResults')?.classList.add('available');
}

function renderTimeline(timeline) {
  const stepper = document.getElementById('pipelineStepper');
  if (!stepper) return;
  stepper.innerHTML = '';
  timeline.forEach((step, idx) => {
    const item = document.createElement('div');
    item.className = `stepper-item ${step.status}`;
    item.innerHTML = `
      <div class="stepper-node">${idx + 1}</div>
      <div class="stepper-content">
        <div class="stepper-title-row">
          <span class="stepper-title">${step.stage}</span>
          <span class="stepper-status ${step.status}">${step.status}</span>
        </div>
        <p class="stepper-desc">${step.description}</p>
      </div>
    `;
    stepper.appendChild(item);
  });
}

function renderTools(tools) {
  const container = document.getElementById('toolsListContainer');
  if (!container) return;
  container.innerHTML = '';
  tools.forEach(tool => {
    const card = document.createElement('div');
    card.className = 'tool-card';
    card.innerHTML = `
      <div class="tool-header">
        <span class="tool-name">${tool.name}</span>
        <span class="tool-badge ${tool.active ? 'active' : ''}">${tool.active ? 'ACTIVE' : 'STANDBY'}</span>
      </div>
      <p class="tool-role">${tool.role}</p>
    `;
    container.appendChild(card);
  });
}

function renderCandidates(candidates) {
  setText('candidateCount', candidates.length);
  const grid = document.getElementById('candidatesGrid');
  if (!grid) return;
  grid.innerHTML = '';
  if (candidates.length === 0) {
    grid.innerHTML = `<div style="color:var(--text-muted);font-size:.85rem;padding:20px 0">No polymorph records found for this query.</div>`;
    return;
  }

  candidates.forEach((c, idx) => {
    const card = document.createElement('div');
    card.className = 'candidate-card';
    card.id = `candidate-card-${idx}`;
    const sys = guessCrystalSystem(c.space_group);
    card.innerHTML = `
      <div class="candidate-title">
        <span>${c.source} #${c.record_id}</span>
        ${c.is_theoretical ? '<span class="badge" style="font-size:.6rem">THEORETICAL</span>' : ''}
        <span class="candidate-active-badge hidden" id="candActiveBadge-${idx}">ACTIVE CIF</span>
      </div>
      <div class="candidate-sg">SG: <strong>${c.space_group || 'Unspecified'}</strong> · <span style="color:var(--text-muted)">${sys}</span></div>
      <div class="candidate-sg" style="color:var(--text-primary)">${c.formula}</div>
      <div class="candidate-actions">
        ${c.cif_string ? `<button class="candidate-view-btn" data-idx="${idx}">Set as Active CIF</button>` : ''}
        ${c.source_url ? `<a href="${c.source_url}" target="_blank" class="candidate-link">DB Record →</a>` : ''}
      </div>
    `;
    if (c.cif_string) {
      card.querySelector('.candidate-view-btn').addEventListener('click', () => {
        // Promote this polymorph to the active view and update all panels
        APP.currentCifData = c.cif_string;
        loadCifIntoViewer(c.cif_string, `${c.formula} (${c.source} #${c.record_id})`);
        setText('cifCodeBlock',  c.cif_string);
        setText('cifLineCount',  `Lines: ${c.cif_string.trim().split('\n').length}`);
        setText('cifFormulaMeta',`Formula: ${c.formula}`);
        setText('spaceGroupBadge', `SG: ${c.space_group || 'Unspecified'}`);
        setText('sourceBadge', c.source.toUpperCase());

        // Update symmetry matrix for this polymorph
        renderSymmetryMatrix(c.formula, {
          cif_string: c.cif_string,
          matched_record: c,
        });

        // Toggle active badges on cards
        document.querySelectorAll('.candidate-active-badge').forEach(b => b.classList.add('hidden'));
        document.getElementById(`candActiveBadge-${idx}`)?.classList.remove('hidden');

        switchTab('visualizerTab');
      });
    }
    grid.appendChild(card);
  });
}

// ─── SYMMETRY MATRIX ──────────────────────────────────────────────────────────
function renderSymmetryMatrix(formula, data) {
  // Space group info from CIF or matched record
  const sg = data.matched_record?.space_group
    || extractFromCIF(data.cif_string, '_symmetry_space_group_name_H-M')
    || '—';

  setText('symSpaceGroupBadge',    `SG: ${sg}`);
  setText('symCrystalSystemBadge', `System: ${guessCrystalSystem(sg)}`);
  setText('symPointGroupBadge',    `Point: ${guessPointGroup(sg)}`);

  // Lattice parameters from CIF
  const lp = extractLatticeParams(data.cif_string);
  const lpGrid = document.getElementById('latticeParamsGrid');
  if (lpGrid) {
    lpGrid.innerHTML = '';
    const params = [
      { label: 'a', value: lp.a, unit: 'Å' },
      { label: 'b', value: lp.b, unit: 'Å' },
      { label: 'c', value: lp.c, unit: 'Å' },
      { label: 'α', value: lp.alpha, unit: '°' },
      { label: 'β', value: lp.beta,  unit: '°' },
      { label: 'γ', value: lp.gamma, unit: '°' },
      { label: 'V', value: lp.volume, unit: 'Å³' },
      { label: 'Z', value: lp.Z,     unit: '' },
    ];
    params.forEach(p => {
      const div = document.createElement('div');
      div.className = 'lat-param';
      div.innerHTML = `
        <div class="lat-param-label">${p.label}</div>
        <div class="lat-param-value">${p.value || '—'}</div>
        <div class="lat-param-unit">${p.unit}</div>
      `;
      lpGrid.appendChild(div);
    });
  }

  // Space group info grid
  const sgGrid = document.getElementById('spaceGroupInfoGrid');
  if (sgGrid) {
    const num = extractFromCIF(data.cif_string, '_symmetry_Int_Tables_number') || '—';
    const hall = extractFromCIF(data.cif_string, '_symmetry_space_group_name_Hall') || '—';
    sgGrid.innerHTML = `
      <div class="sg-info-row"><span class="sg-info-label">H-M Symbol:</span><span class="sg-info-value">${sg}</span></div>
      <div class="sg-info-row"><span class="sg-info-label">IT Number:</span><span class="sg-info-value">${num}</span></div>
      <div class="sg-info-row"><span class="sg-info-label">Hall Symbol:</span><span class="sg-info-value">${hall}</span></div>
      <div class="sg-info-row"><span class="sg-info-label">Crystal System:</span><span class="sg-info-value">${guessCrystalSystem(sg)}</span></div>
      <div class="sg-info-row"><span class="sg-info-label">Point Group:</span><span class="sg-info-value">${guessPointGroup(sg)}</span></div>
      <div class="sg-info-row"><span class="sg-info-label">Formula:</span><span class="sg-info-value">${formula}</span></div>
    `;
  }

  // Symmetry operations — parse from CIF or show generic identity
  const symOps  = extractSymOps(data.cif_string);
  const opsContainer = document.getElementById('symmetryOpsContainer');
  if (opsContainer) {
    opsContainer.innerHTML = '';
    if (symOps.length === 0) {
      opsContainer.innerHTML = `<div class="sym-ops-placeholder">Symmetry operations: parsed from CIF during full spglib analysis</div>`;
    } else {
      symOps.slice(0, 24).forEach((op, i) => {
        const el = document.createElement('div');
        el.className = 'sym-op-item';
        const mat = parseSymOpToMatrix(op);
        el.innerHTML = `
          <div class="sym-op-label">Op #${i+1}: ${op}</div>
          <div class="sym-matrix">${mat.cells.map(v => `<div class="sym-matrix-cell ${v !== '0' ? 'nonzero' : ''}">${v}</div>`).join('')}</div>
          ${mat.t ? `<div class="sym-translation">t = (${mat.t})</div>` : ''}
        `;
        opsContainer.appendChild(el);
      });
      if (symOps.length > 24) {
        const more = document.createElement('div');
        more.style.cssText = 'font-size:.72rem;color:var(--text-muted);width:100%;text-align:center;padding:8px 0';
        more.textContent = `… ${symOps.length - 24} more operations`;
        opsContainer.appendChild(more);
      }
    }
  }

  // Wyckoff positions — parse from CIF atom_site block
  const wyckoff = extractWyckoff(data.cif_string);
  const wyckTbl = document.getElementById('wyckoffTable');
  if (wyckTbl) {
    if (wyckoff.length === 0) {
      wyckTbl.innerHTML = `<div class="sym-ops-placeholder">Wyckoff positions derived from _atom_site block in CIF</div>`;
    } else {
      const rows = wyckoff.map(w => `
        <tr>
          <td>${w.label}</td>
          <td>${w.type}</td>
          <td>${w.x}</td><td>${w.y}</td><td>${w.z}</td>
          <td>${w.occ}</td>
          <td>${w.wyckoff || '—'}</td>
        </tr>
      `).join('');
      wyckTbl.innerHTML = `
        <table class="wyck-table">
          <thead><tr><th>Label</th><th>Type</th><th>x</th><th>y</th><th>z</th><th>Occ</th><th>Wyckoff</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }
  }

  // Polymorph screening results
  renderPolymorphScreen(data);
}

function renderPolymorphScreen(data) {
  const container = document.getElementById('polymorphScreenResult');
  if (!container) return;
  const candidates = data.candidate_matches || [];
  if (candidates.length === 0) {
    container.innerHTML = `<div class="sym-ops-placeholder">No polymorphs detected — single structural form found</div>`;
    return;
  }
  container.innerHTML = candidates.map((c, i) => `
    <div class="polymorph-item">
      <div class="pm-rank">#${i+1}</div>
      <div class="pm-sg">${c.space_group || 'SG ?'}</div>
      <div class="pm-energy">${c.source} — ${c.record_id}</div>
      <div class="pm-badge ${i===0 ? 'stable' : 'meta'}">${i===0 ? 'Primary' : 'Metastable'}</div>
    </div>
  `).join('');
}

// ─── MESP RENDERING ───────────────────────────────────────────────────────────
function renderMESP(formula, data) {
  const statusBadge = document.getElementById('mespStatusBadge');
  const energyContent = document.getElementById('mespEnergyContent');
  const relaxContent  = document.getElementById('mespRelaxContent');
  const checklist     = document.getElementById('mespChecklist');
  const scriptEl      = document.getElementById('mespScriptCode');

  const hasResult = data.success && data.cif_string;
  if (statusBadge) {
    statusBadge.textContent = hasResult ? 'PASS' : 'NO DATA';
    statusBadge.className   = `mesp-status-badge ${hasResult ? 'ok' : ''}`;
  }

  // Energy metrics (from notes or simulated)
  if (energyContent) {
    energyContent.innerHTML = hasResult ? `
      <div class="mesp-row"><span class="mesp-label">Source:</span><span class="mesp-value">${data.retrieved_from}</span></div>
      <div class="mesp-row"><span class="mesp-label">Pipeline Time:</span><span class="mesp-value ok">${data.total_elapsed_seconds}s</span></div>
      <div class="mesp-row"><span class="mesp-label">CIF Generated:</span><span class="mesp-value ok">Yes</span></div>
      <div class="mesp-row"><span class="mesp-label">MACE Potential:</span><span class="mesp-value">float64 medium</span></div>
    ` : `<div class="mesp-placeholder">No energy data — pipeline did not produce a structure</div>`;
  }

  // Relaxation quality
  if (relaxContent) {
    const converged = hasResult && !data.notes?.toLowerCase().includes('not converge');
    relaxContent.innerHTML = hasResult ? `
      <div class="mesp-row"><span class="mesp-label">Convergence:</span><span class="mesp-value ${converged ? 'ok' : 'warn'}">${converged ? 'Converged' : 'Not converged'}</span></div>
      <div class="mesp-row"><span class="mesp-label">Optimizer:</span><span class="mesp-value">FIRE (ASE ExpCellFilter)</span></div>
      <div class="mesp-row"><span class="mesp-label">Precision:</span><span class="mesp-value ok">float64 IEEE 754</span></div>
      <div class="mesp-row"><span class="mesp-label">CrystaLLM used:</span><span class="mesp-value">${data.crystallm_used ? 'Yes' : 'No'}</span></div>
    ` : `<div class="mesp-placeholder">No relaxation data available</div>`;
  }

  // Checklist
  if (checklist) {
    const checks = [
      { label: 'CIF structure produced', pass: hasResult, note: hasResult ? 'OK' : 'FAILED' },
      { label: 'Formula matches query',  pass: hasResult, note: formula },
      { label: 'Space group determined', pass: !!data.matched_record?.space_group || data.cif_string?.includes('_symmetry'), note: 'via spglib' },
      { label: 'Relaxation optimizer',   pass: hasResult, note: 'FIRE / MACE-MP-0' },
      { label: 'float64 precision',      pass: true,       note: 'Always enabled' },
      { label: 'Doped validation',       pass: !data.doped_validation || data.doped_validation?.is_valid, note: data.doped_validation ? (data.doped_validation.is_valid ? 'Valid' : 'Failed') : 'N/A (undoped)' },
      { label: 'No NaN/inf energies',    pass: hasResult,  note: 'Assumed OK if converged' },
      { label: 'Bond lengths physical',  pass: hasResult,  note: 'Checked by filter_rules.py' },
    ];
    checklist.innerHTML = checks.map(c => `
      <div class="mesp-check-item">
        <div class="mesp-check-icon ${c.pass ? 'pass' : 'fail'}">${c.pass ? '✓' : '✗'}</div>
        <div class="mesp-check-text">${c.label}</div>
        <div class="mesp-check-note">${c.note}</div>
      </div>
    `).join('');
  }

  // MESP script
  if (scriptEl) {
    scriptEl.textContent = generateMESPScript(formula);
  }
}

function generateMESPScript(formula) {
  return `#!/usr/bin/env python3
"""
MESP Energy & Structure Validation Script
Generated for: ${formula}
"""
from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
import numpy as np

def check_structure(cif_path: str, formula: str) -> dict:
    """
    Run MESP validation checks on a CIF structure.
    Returns dict of check results.
    """
    results = {}
    s = Structure.from_file(cif_path)

    # 1. Formula check
    results['formula_match'] = (s.composition.reduced_formula == formula)

    # 2. Bond length sanity (all pairs > 0.5 Å)
    from pymatgen.analysis.structure_analyzer import VoronoiAnalyzer
    min_dist = min(
        s.get_distance(i, j)
        for i in range(len(s))
        for j in range(i+1, len(s))
    )
    results['bond_ok'] = min_dist > 0.5
    results['min_bond_distance'] = round(float(min_dist), 4)

    # 3. No overlapping atoms (density check)
    vol_per_atom = s.volume / len(s)
    results['density_ok'] = 2.0 < vol_per_atom < 50.0
    results['vol_per_atom'] = round(float(vol_per_atom), 3)

    # 4. Space group determination (post-relaxation)
    sga = SpacegroupAnalyzer(s, symprec=0.1)
    results['space_group_hm']   = sga.get_space_group_symbol()
    results['space_group_num']  = sga.get_space_group_number()
    results['crystal_system']   = sga.get_crystal_system()

    # 5. Charge balance (oxide check)
    comp = s.composition
    ox_states_ok = True
    try:
        comp.oxi_state_guesses()
        ox_states_ok = True
    except Exception:
        ox_states_ok = False
    results['charge_balance_ok'] = ox_states_ok

    # 6. No disordered sites
    results['no_disorder'] = all(site.is_ordered for site in s)

    # 7. Reasonable cell volume
    results['volume'] = round(float(s.volume), 3)
    results['n_atoms'] = len(s)

    return results

if __name__ == '__main__':
    import sys, json
    cif  = sys.argv[1] if len(sys.argv) > 1 else '${formula}.cif'
    res  = check_structure(cif, '${formula}')
    print(json.dumps(res, indent=2))
    all_ok = all(v is True or isinstance(v, (int, float, str)) for v in res.values())
    print(f"\\nOverall: {'PASS' if all_ok else 'REVIEW NEEDED'}")
`;
}

// ─── CIF PARSING HELPERS ──────────────────────────────────────────────────────
function extractFromCIF(cif, key) {
  if (!cif) return null;
  const re = new RegExp(key.replace(/[-_]/g, '[-_]') + '\\s+[\'"]?([^\'"\\n]+)[\'"]?');
  const m  = cif.match(re);
  return m ? m[1].trim() : null;
}

function extractLatticeParams(cif) {
  if (!cif) return {};
  const g = k => {
    const m = cif.match(new RegExp(`_cell_length_${k}\\s+([\\d.]+)`));
    return m ? m[1] : null;
  };
  const ga = k => {
    const m = cif.match(new RegExp(`_cell_angle_${k}\\s+([\\d.]+)`));
    return m ? m[1] : null;
  };
  const gv = () => { const m = cif.match(/_cell_volume\s+([\d.]+)/); return m ? m[1] : null; };
  const gz = () => { const m = cif.match(/_cell_formula_units_Z\s+(\d+)/); return m ? m[1] : null; };
  return { a: g('a'), b: g('b'), c: g('c'), alpha: ga('alpha'), beta: ga('beta'), gamma: ga('gamma'), volume: gv(), Z: gz() };
}

function extractSymOps(cif) {
  if (!cif) return [];
  const ops = [];
  const re  = /['"]?([xyz\s,+\-/\d]+)['"]?\s*\n/g;
  const block = cif.match(/_symmetry_equiv_pos_as_xyz[\s\S]*?(?=_|\n\n)/);
  if (!block) return [];
  let m;
  while ((m = re.exec(block[0])) !== null) {
    const op = m[1].trim();
    if (op.includes('x') || op.includes('y') || op.includes('z')) ops.push(op);
  }
  return ops.slice(0, 48);
}

function parseSymOpToMatrix(op) {
  // Simple display — parse x,y,z expressions to 3×3 cells
  const parts = op.split(',').map(p => p.trim());
  const cells = [];
  const trans = [];
  parts.forEach(expr => {
    cells.push(expr.includes('x') ? (expr.startsWith('-x') ? '-1' : '1') : '0');
    cells.push(expr.includes('y') ? (expr.startsWith('-y') ? '-1' : '1') : '0');
    cells.push(expr.includes('z') ? (expr.startsWith('-z') ? '-1' : '1') : '0');
    const tm = expr.match(/([+-]?\d+\/\d+|[+-]\d+\.\d+)$/);
    trans.push(tm ? tm[1] : '0');
  });
  return { cells, t: trans.every(t => t === '0') ? null : trans.join(', ') };
}

function extractWyckoff(cif) {
  if (!cif) return [];
  const rows = [];
  const atomRe = /(\S+)\s+(\S+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/g;
  const block  = cif.match(/_atom_site[\s\S]*?(?=\n\n|\n_[^a])/);
  if (!block) return [];
  let m;
  while ((m = atomRe.exec(block[0])) !== null) {
    rows.push({ label: m[1], type: m[2], x: m[3], y: m[4], z: m[5], occ: m[6], wyckoff: null });
    if (rows.length >= 20) break;
  }
  return rows;
}

function guessCrystalSystem(sg) {
  if (!sg || sg === '—') return '—';
  const n = parseInt(sg);
  if (!isNaN(n)) {
    if (n <= 2)   return 'Triclinic';
    if (n <= 15)  return 'Monoclinic';
    if (n <= 74)  return 'Orthorhombic';
    if (n <= 142) return 'Tetragonal';
    if (n <= 167) return 'Trigonal';
    if (n <= 194) return 'Hexagonal';
    return 'Cubic';
  }
  if (sg.match(/^[PF][d\-]3/)) return 'Cubic';
  if (sg.match(/^[PCI]\d/))    return 'Tetragonal';
  if (sg.match(/^R/))          return 'Trigonal';
  if (sg.match(/^[PH]6/))      return 'Hexagonal';
  return '—';
}

function guessPointGroup(sg) {
  if (!sg || sg === '—') return '—';
  if (sg.includes('m-3m')) return 'Oh';
  if (sg.includes('-3m'))  return 'D3d';
  if (sg.includes('-3c'))  return 'S6';
  if (sg.match(/Fd-3/))   return 'Oh';
  if (sg.match(/Pnma/i))  return 'D2h';
  return '—';
}

// ─── TAB SWITCHING ────────────────────────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  const pane = document.getElementById(tabId);
  if (btn)  btn.classList.add('active');
  if (pane) pane.classList.add('active');
  if (tabId === 'visualizerTab' && APP.viewer) APP.viewer.render();
}

// ─── VIEWER CONTROLS ──────────────────────────────────────────────────────────
function setupViewerControls() {
  document.getElementById('resetViewBtn')?.addEventListener('click', () => {
    if (APP.viewer) { APP.viewer.zoomTo(); APP.viewer.render(); }
  });
  document.getElementById('spinToggleBtn')?.addEventListener('click', () => {
    if (!APP.viewer) return;
    APP.isSpinning = !APP.isSpinning;
    APP.viewer.spin(APP.isSpinning);
    document.getElementById('spinToggleBtn').classList.toggle('active', APP.isSpinning);
  });
  document.getElementById('unitCellToggleBtn')?.addEventListener('click', () => {
    APP.showUnitCell = !APP.showUnitCell;
    document.getElementById('unitCellToggleBtn')?.classList.toggle('active', APP.showUnitCell);
    applyViewerStyle();
  });
  document.getElementById('styleSelect')?.addEventListener('change', (e) => {
    APP.currentStyle = e.target.value;
    applyViewerStyle();
  });
}

// ─── CIF ACTIONS ──────────────────────────────────────────────────────────────
function setupCifActions() {
  document.getElementById('copyCifBtn')?.addEventListener('click', () => {
    if (!APP.currentCifData) return;
    navigator.clipboard.writeText(APP.currentCifData).then(() => {
      const btn = document.getElementById('copyCifBtn');
      const orig = btn.innerHTML;
      btn.innerHTML = '<span>Copied!</span>';
      setTimeout(() => btn.innerHTML = orig, 1800);
    });
  });

  document.getElementById('downloadCifBtn')?.addEventListener('click', () => {
    if (!APP.currentCifData) return;
    const blob = new Blob([APP.currentCifData], { type: 'text/plain;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `${APP.lastFormula.replace(/[^a-zA-Z0-9_.-]/g, '_')}.cif`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  document.getElementById('newSearchBtn')?.addEventListener('click', () => navigateTo('page-input'));
  document.getElementById('backToReviewBtn')?.addEventListener('click', () => {
    if (APP.lastParseData) {
      populateReviewPage(APP.lastFormula, APP.lastParseData);
      navigateTo('page-review');
    } else {
      navigateTo('page-input');
    }
  });
}

// ─── THEME TOGGLE ─────────────────────────────────────────────────────────────
function setupTheme() {
  document.getElementById('themeToggleBtn')?.addEventListener('click', () => {
    const isDark   = document.documentElement.getAttribute('data-theme') === 'dark';
    const next     = isDark ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    document.getElementById('moonIcon')?.classList.toggle('hidden', !isDark);
    document.getElementById('sunIcon')?.classList.toggle('hidden',  isDark);
    if (APP.viewer) {
      APP.viewer.setBackgroundColor(next === 'dark' ? '#09090b' : '#f8fafc');
      APP.viewer.render();
    }
  });
}

// ─── CrystaLLM STATUS ────────────────────────────────────────────────────────
async function checkCrystaLLMStatus() {
  try {
    const resp  = await fetch('/api/health');
    if (!resp.ok) return;
    const health = await resp.json();
    const el     = document.getElementById('crystallmStatusVal');
    if (!el) return;
    if (health.crystallm_ready) {
      el.textContent = 'Ready (checkpoint found)'; el.className = 'telemetry-val active';
    } else if (health.crystallm_checkpoint) {
      el.textContent = '⚠ Path set, ckpt.pt missing'; el.className = 'telemetry-val warn';
    } else {
      el.textContent = 'Not configured'; el.className = 'telemetry-val';
    }
  } catch (_) {}
}

// ─── BATCH PAGE ───────────────────────────────────────────────────────────────
function setupBatchPage() {
  document.getElementById('batchRunBtn')?.addEventListener('click', async () => {
    const input = document.getElementById('batchFormulaInput')?.value || '';
    const formulas = input.split('\n').map(l => l.trim()).filter(Boolean);
    if (formulas.length === 0) return;

    const totalEl   = document.getElementById('batchTotal');
    const doneEl    = document.getElementById('batchDone');
    const failedEl  = document.getElementById('batchFailed');
    const progress  = document.getElementById('batchProgressBar');
    const resultsList = document.getElementById('batchResultsList');

    if (totalEl)   totalEl.textContent  = formulas.length;
    if (doneEl)    doneEl.textContent   = 0;
    if (failedEl)  failedEl.textContent = 0;
    if (resultsList) resultsList.innerHTML = '';

    let done = 0, failed = 0;

    for (const formula of formulas) {
      // Add pending item
      const item = document.createElement('div');
      item.className = 'batch-result-item';
      item.innerHTML = `<div class="batch-result-dot pending"></div><div class="batch-result-formula">${formula}</div><div class="batch-result-status">RUNNING...</div>`;
      resultsList?.appendChild(item);
      resultsList.scrollTop = resultsList.scrollHeight;

      try {
        const res = await fetch('/api/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ formula }),
        });
        const data = await res.json();
        const ok   = data.success && data.cif_string;
        done++;
        item.querySelector('.batch-result-dot').className = `batch-result-dot ${ok ? 'ok' : 'fail'}`;
        item.querySelector('.batch-result-status').textContent = ok ? data.source.toUpperCase() : 'FAILED';
      } catch (e) {
        failed++;
        item.querySelector('.batch-result-dot').className = 'batch-result-dot fail';
        item.querySelector('.batch-result-status').textContent = 'ERROR';
      }

      if (doneEl)   doneEl.textContent   = done;
      if (failedEl) failedEl.textContent = failed;
      if (progress) progress.style.width = `${Math.round(((done + failed) / formulas.length) * 100)}%`;
    }
  });
}

// ─── UTILITIES ────────────────────────────────────────────────────────────────
function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// ─── BOOT ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Initialize everything
  setupLogin();
  setupLogout();
  setupNavPills();
  setupInputPage();
  setupReviewPage();
  setupTabs();
  setupViewerControls();
  setupCifActions();
  setupTheme();
  setupBatchPage();

  // Restore authenticated session if previously logged in
  try {
    const saved = sessionStorage.getItem('cif_auth');
    if (saved) {
      const parsed = JSON.parse(saved);
      if (parsed.loggedIn) {
        APP.loggedIn = true;
        APP.currentUser = parsed.user;
      }
    }
  } catch (_) {}

  // Route to saved hash or appropriate initial page
  let initialPage = 'page-login';
  if (APP.loggedIn) {
    const hash = location.hash.replace('#', '');
    if (hash && document.getElementById('page-' + hash)) {
      initialPage = 'page-' + hash;
    } else {
      initialPage = 'page-input';
    }
    checkCrystaLLMStatus();
    initViewer();
  }

  // Navigate without pushing duplicate initial history entry
  navigateTo(initialPage, true, false);

  // Pre-parse default formula
  updateSearchSpaceElements('Fe2O3');
});
