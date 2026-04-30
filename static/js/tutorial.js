/**
 * OpenFrequency 新手引导 / Onboarding Tutorial
 * Spotlight + step cards + confetti
 */
(function () {
  'use strict';

  /* ─── Step definitions ─────────────────────────────────────────────────── */
  const STEPS = [
    {
      page: '/',
      target: 'a[href="/settings"]',
      i18n: 'tut_step_settings',
      icon: '⚙️',
    },
    {
      page: '/settings',
      target: '#conf-provider',
      i18n: 'tut_step_provider',
      icon: '🧠',
    },
    {
      page: '/settings',
      target: '#conf-api-key',
      i18n: 'tut_step_apikey',
      icon: '🔑',
    },
    {
      page: '/settings',
      target: '#conf-sim-provider',
      i18n: 'tut_step_sim',
      icon: '✈️',
    },
    {
      page: '/settings',
      target: '#conf-callsign',
      i18n: 'tut_step_callsign',
      icon: '📡',
    },
    {
      page: '/settings',
      target: 'button[type="submit"]',
      i18n: 'tut_step_save',
      icon: '💾',
      autoAdvanceOnClick: true,
    },
    {
      page: '/',
      target: '#mode-selection',
      i18n: 'tut_step_fly',
      icon: '🚀',
      final: true,
    },
  ];

  /* ─── i18n strings ──────────────────────────────────────────────────────── */
  const T = {
    en: {
      tut_btn: '🎓 Tutorial',
      tut_skip: 'Skip tutorial',
      tut_next: 'Next →',
      tut_finish: '🎉 Done!',
      tut_step_settings: { title: 'Welcome to OpenFrequency!', body: "Let's get you set up in a few steps. First, open the Settings page." },
      tut_step_provider: { title: 'Step 1 — Choose AI provider', body: 'Select your LLM provider. Gemini is recommended (free tier available).' },
      tut_step_apikey:   { title: 'Step 2 — API Key', body: 'Paste your API key here. Get a free Gemini key at aistudio.google.com.' },
      tut_step_sim:      { title: 'Step 3 — Simulator', body: 'Choose your simulator: MSFS 2020/2024 or X-Plane 12.' },
      tut_step_callsign: { title: 'Step 4 — Callsign', body: 'Enter your aircraft callsign, e.g. N12345 or CSN301.' },
      tut_step_save:     { title: 'Save your settings', body: 'Click Save Configuration to apply all changes.' },
      tut_step_fly:      { title: "You're all set! 🎉", body: 'Choose a flight mode below and enjoy your flight. Have fun!' },
    },
    zh: {
      tut_btn: '🎓 新手引导',
      tut_skip: '跳过引导',
      tut_next: '下一步 →',
      tut_finish: '🎉 完成！',
      tut_step_settings: { title: '欢迎使用 OpenFrequency！', body: '只需几步即可完成配置，首先打开设置页面。' },
      tut_step_provider: { title: '第 1 步 — 选择 AI 提供商', body: '选择你的大语言模型提供商，推荐使用 Gemini（有免费额度）。' },
      tut_step_apikey:   { title: '第 2 步 — API Key', body: '在这里粘贴你的 API Key，可在 aistudio.google.com 免费获取 Gemini Key。' },
      tut_step_sim:      { title: '第 3 步 — 选择模拟器', body: '选择你使用的模拟器：MSFS 2020/2024 或 X-Plane 12。' },
      tut_step_callsign: { title: '第 4 步 — 飞机呼号', body: '输入你的飞机呼号，例如 CSN301 或 N12345。' },
      tut_step_save:     { title: '保存设置', body: '点击"保存配置"按钮使所有更改生效。' },
      tut_step_fly:      { title: '准备完毕！🎉', body: '在下方选择飞行模式，出发吧！祝飞行愉快！' },
    },
    ja: {
      tut_btn: '🎓 チュートリアル',
      tut_skip: 'スキップ',
      tut_next: '次へ →',
      tut_finish: '🎉 完了！',
      tut_step_settings: { title: 'OpenFrequencyへようこそ！', body: 'いくつかのステップで設定を完了しましょう。まず設定ページを開きます。' },
      tut_step_provider: { title: 'ステップ1 — AIプロバイダー', body: 'LLMプロバイダーを選択します。Geminiを推奨します（無料枠あり）。' },
      tut_step_apikey:   { title: 'ステップ2 — APIキー', body: 'APIキーを貼り付けてください。aistudio.google.comで無料のGeminiキーを取得できます。' },
      tut_step_sim:      { title: 'ステップ3 — シミュレーター', body: '使用するシミュレーターを選択：MSFS 2020/2024またはX-Plane 12。' },
      tut_step_callsign: { title: 'ステップ4 — コールサイン', body: '航空機のコールサインを入力します（例：JA8086）。' },
      tut_step_save:     { title: '設定を保存', body: '「設定を保存」ボタンをクリックして変更を適用します。' },
      tut_step_fly:      { title: '準備完了！🎉', body: 'フライトモードを選んで出発しましょう！良いフライトを！' },
    },
  };

  /* ─── State ─────────────────────────────────────────────────────────────── */
  const LS_KEY = 'of_tutorial_step';
  let currentStep = -1;
  let overlayEl, cardEl, topEl, rightEl, bottomEl, leftEl;
  let rafId = null;

  function getLang() {
    if (window.TRANSLATIONS) {
      const sel = document.getElementById('lang-selector');
      if (sel) return sel.value || 'en';
    }
    const stored = localStorage.getItem('of_lang') || 'en';
    return ['en', 'zh', 'ja'].includes(stored) ? stored : 'en';
  }

  function t(key) {
    const lang = getLang();
    const table = T[lang] || T.en;
    return table[key] || T.en[key] || key;
  }

  function stepText(step) {
    const lang = getLang();
    const table = T[lang] || T.en;
    return table[step.i18n] || T.en[step.i18n] || { title: step.i18n, body: '' };
  }

  /* ─── Spotlight DOM ──────────────────────────────────────────────────────── */
  function buildDOM() {
    if (document.getElementById('tut-overlay')) return;

    // 4-quadrant dark mask
    const mkDiv = (id, extra) => {
      const d = document.createElement('div');
      d.id = id;
      Object.assign(d.style, {
        position: 'fixed', background: 'rgba(0,0,0,0.58)',
        pointerEvents: 'none', zIndex: '99990', transition: 'all 0.25s ease',
        ...extra,
      });
      document.body.appendChild(d);
      return d;
    };
    topEl    = mkDiv('tut-mask-top',    { top: 0, left: 0, right: 0 });
    bottomEl = mkDiv('tut-mask-bot',    { bottom: 0, left: 0, right: 0 });
    leftEl   = mkDiv('tut-mask-left',   {});
    rightEl  = mkDiv('tut-mask-right',  {});

    // Card
    cardEl = document.createElement('div');
    cardEl.id = 'tut-card';
    Object.assign(cardEl.style, {
      position: 'fixed', zIndex: '99999', maxWidth: '340px', minWidth: '260px',
      background: 'white', borderRadius: '14px',
      boxShadow: '0 8px 32px rgba(0,0,0,0.22)',
      padding: '20px 22px 16px', fontFamily: 'system-ui,sans-serif',
      transition: 'top 0.25s ease, left 0.25s ease, opacity 0.2s',
    });
    cardEl.innerHTML = `
      <div id="tut-progress" style="height:4px;background:#e9ecef;border-radius:2px;margin-bottom:14px;">
        <div id="tut-progress-fill" style="height:100%;background:#0d6efd;border-radius:2px;transition:width 0.3s ease;"></div>
      </div>
      <div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:10px;">
        <span id="tut-icon" style="font-size:1.6rem;line-height:1;"></span>
        <div>
          <div id="tut-title" style="font-weight:700;font-size:1rem;color:#212529;margin-bottom:4px;"></div>
          <div id="tut-body" style="font-size:0.875rem;color:#495057;line-height:1.5;"></div>
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">
        <button id="tut-skip" style="background:none;border:none;color:#868e96;font-size:0.8rem;cursor:pointer;padding:0;"></button>
        <div style="display:flex;align-items:center;gap:8px;">
          <span id="tut-counter" style="font-size:0.75rem;color:#adb5bd;"></span>
          <button id="tut-next" style="background:#0d6efd;color:white;border:none;border-radius:8px;padding:7px 16px;font-size:0.875rem;font-weight:600;cursor:pointer;"></button>
        </div>
      </div>`;
    document.body.appendChild(cardEl);

    document.getElementById('tut-skip').addEventListener('click', endTutorial);
    document.getElementById('tut-next').addEventListener('click', advanceStep);
  }

  /* ─── Spotlight positioning ──────────────────────────────────────────────── */
  const PAD = 8;

  function positionSpotlight(el) {
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    const x1 = Math.max(0, r.left - PAD), y1 = Math.max(0, r.top - PAD);
    const x2 = Math.min(vw, r.right + PAD), y2 = Math.min(vh, r.bottom + PAD);

    topEl.style.cssText    += `;top:0;left:0;right:0;height:${y1}px`;
    bottomEl.style.cssText += `;bottom:0;left:0;right:0;top:${y2}px`;
    leftEl.style.cssText   += `;top:${y1}px;left:0;width:${x1}px;height:${y2-y1}px`;
    rightEl.style.cssText  += `;top:${y1}px;left:${x2}px;right:0;height:${y2-y1}px`;

    // Card placement: prefer below, else above
    const cardW = 340, cardH = 200;
    let cx, cy;
    if (y2 + cardH + 12 < vh) {
      cy = y2 + 12;
    } else if (y1 - cardH - 12 > 0) {
      cy = y1 - cardH - 12;
    } else {
      cy = Math.max(10, Math.min(vh - cardH - 10, y1));
    }
    cx = Math.max(10, Math.min(vw - cardW - 10, r.left));
    cardEl.style.top  = cy + 'px';
    cardEl.style.left = cx + 'px';
  }

  function showMasks() {
    [topEl, bottomEl, leftEl, rightEl].forEach(d => d.style.display = 'block');
  }
  function hideMasks() {
    [topEl, bottomEl, leftEl, rightEl].forEach(d => d.style.display = 'none');
  }

  /* ─── Show step ──────────────────────────────────────────────────────────── */
  function showStep(index) {
    const step = STEPS[index];
    if (!step) { endTutorial(); return; }

    const el = document.querySelector(step.target);
    if (!el) {
      // element not on this page — skip render but persist state
      return;
    }

    buildDOM();
    showMasks();
    cardEl.style.display = 'block';
    cardEl.style.opacity = '1';

    // Scroll target into view
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });

    setTimeout(() => {
      positionSpotlight(el);

      // Update card content
      const txt = stepText(step);
      document.getElementById('tut-icon').textContent  = step.icon;
      document.getElementById('tut-title').textContent = txt.title;
      document.getElementById('tut-body').textContent  = txt.body;
      document.getElementById('tut-skip').textContent  = t('tut_skip');
      document.getElementById('tut-next').textContent  = step.final ? t('tut_finish') : t('tut_next');
      document.getElementById('tut-counter').textContent = `${index + 1} / ${STEPS.length}`;

      const pct = ((index + 1) / STEPS.length * 100).toFixed(0);
      document.getElementById('tut-progress-fill').style.width = pct + '%';

      // Pulse ring on target
      el.style.outline = '3px solid #0d6efd';
      el.style.outlineOffset = '3px';
      el.style.borderRadius = '6px';
      el.style.transition = 'outline 0.3s';
      el._tut_highlighted = true;

      // auto-advance on click (e.g. save button)
      if (step.autoAdvanceOnClick) {
        el._tut_listener = () => setTimeout(advanceStep, 600);
        el.addEventListener('click', el._tut_listener, { once: true });
      }
    }, 120);

    // Keep spotlight synced on resize/scroll
    if (rafId) cancelAnimationFrame(rafId);
    function sync() {
      if (currentStep !== index) return;
      const r = document.querySelector(step.target);
      if (r) positionSpotlight(r);
      rafId = requestAnimationFrame(sync);
    }
    rafId = requestAnimationFrame(sync);
  }

  function clearHighlight(index) {
    if (index < 0 || index >= STEPS.length) return;
    const step = STEPS[index];
    const el = document.querySelector(step.target);
    if (el && el._tut_highlighted) {
      el.style.outline = '';
      el.style.outlineOffset = '';
      el._tut_highlighted = false;
      if (el._tut_listener) {
        el.removeEventListener('click', el._tut_listener);
        el._tut_listener = null;
      }
    }
  }

  /* ─── Navigation ─────────────────────────────────────────────────────────── */
  function advanceStep() {
    const step = STEPS[currentStep];
    clearHighlight(currentStep);

    if (step && step.final) {
      // Show confetti then end
      launchConfetti();
      setTimeout(endTutorial, 3500);
      return;
    }

    const nextIndex = currentStep + 1;
    if (nextIndex >= STEPS.length) { endTutorial(); return; }

    const nextStep = STEPS[nextIndex];
    currentStep = nextIndex;
    localStorage.setItem(LS_KEY, String(currentStep));

    if (nextStep.page !== window.location.pathname) {
      // Navigate to next page; step will auto-render on load
      hideMasks();
      cardEl.style.opacity = '0';
      setTimeout(() => { window.location.href = nextStep.page; }, 200);
    } else {
      showStep(currentStep);
    }
  }

  function markDoneOnServer() {
    fetch('/api/tutorial/done', { method: 'POST' }).catch(() => {});
  }

  function endTutorial() {
    clearHighlight(currentStep);
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    localStorage.removeItem(LS_KEY);
    localStorage.setItem('of_tutorial_completed', '1');
    currentStep = -1;
    if (cardEl) { cardEl.style.display = 'none'; }
    hideMasks();
    markDoneOnServer();
  }

  /* ─── Confetti 🎊 ────────────────────────────────────────────────────────── */
  function launchConfetti() {
    // Load canvas-confetti from CDN lazily
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/canvas-confetti@1.9.3/dist/confetti.browser.min.js';
    script.onload = () => {
      const fire = (angle, origin) => window.confetti({
        angle, spread: 70, particleCount: 80,
        origin, colors: ['#0d6efd','#198754','#ffc107','#dc3545','#6f42c1','#20c997'],
      });
      fire(60,  { x: 0,   y: 0.65 });
      fire(120, { x: 1,   y: 0.65 });
      setTimeout(() => fire(90, { x: 0.5, y: 0.5 }), 300);
      setTimeout(() => fire(60, { x: 0.2, y: 0.7 }), 600);
      setTimeout(() => fire(120,{ x: 0.8, y: 0.7 }), 600);
    };
    document.head.appendChild(script);
  }

  /* ─── Start ──────────────────────────────────────────────────────────────── */
  function startTutorial() {
    currentStep = 0;
    localStorage.setItem(LS_KEY, '0');

    const step = STEPS[0];
    if (step.page !== window.location.pathname) {
      window.location.href = step.page;
    } else {
      showStep(0);
    }
  }

  /* ─── Auto-resume on page load ───────────────────────────────────────────── */
  function tryResume() {
    // If mid-tutorial (cross-page navigation), resume immediately
    const saved = localStorage.getItem(LS_KEY);
    if (saved !== null) {
      const index = parseInt(saved, 10);
      if (!isNaN(index) && index >= 0 && index < STEPS.length) {
        const step = STEPS[index];
        if (step.page === window.location.pathname) {
          currentStep = index;
          buildDOM();
          showStep(index);
          return;
        }
      } else {
        localStorage.removeItem(LS_KEY);
      }
    }
  }

  /* ─── Auto-start on first visit (driven by server config) ───────────────── */
  function tryAutoStart() {
    // Already completed or currently mid-tutorial → skip
    if (localStorage.getItem('of_tutorial_completed') === '1') return;
    if (localStorage.getItem(LS_KEY) !== null) return;
    // Wait for config_sync from server to confirm tutorial_completed flag
    if (window.socket) {
      window.socket.once('config_sync', (cfg) => {
        if (cfg && cfg.ui && cfg.ui.tutorial_completed === false) {
          // Only auto-start from the home page
          if (window.location.pathname === '/') {
            setTimeout(startTutorial, 800);
          }
        } else if (cfg && cfg.ui && cfg.ui.tutorial_completed === true) {
          // Server says done — sync local flag
          localStorage.setItem('of_tutorial_completed', '1');
        }
      });
    }
  }

  /* ─── Tutorial button injection ──────────────────────────────────────────── */
  function injectButton() {
    // Don't inject on dashboard (cluttered) or career
    if (['/dashboard', '/career'].some(p => window.location.pathname.startsWith(p))) return;
    const navbar = document.querySelector('.navbar .d-flex');
    if (!navbar || document.getElementById('tut-start-btn')) return;

    const btn = document.createElement('button');
    btn.id = 'tut-start-btn';
    btn.className = 'btn btn-sm btn-outline-primary';
    btn.style.cssText = 'font-size:0.75rem;';
    btn.addEventListener('click', startTutorial);

    // Update label with current lang
    function updateLabel() {
      const lang = getLang();
      btn.textContent = (T[lang] || T.en).tut_btn;
    }
    updateLabel();
    // Re-label on lang change
    document.addEventListener('of-lang-changed', updateLabel);

    navbar.insertBefore(btn, navbar.firstChild);
  }

  /* ─── Init ───────────────────────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', () => {
    injectButton();
    tryResume();
    // Slight delay so socket is ready before we listen for config_sync
    setTimeout(tryAutoStart, 400);
  });

  // Expose for external trigger
  window.ofTutorial = { start: startTutorial, end: endTutorial };
})();
