/**
 * liquid-glass.js — OpenFrequency
 *
 * 使用 liquid-glass-react (npm) 的真实置换贴图与算法：
 *  • SVG feDisplacementMap + 边缘遮罩（保持中心清晰，边缘折射）
 *  • 三通道色差（R / G-5% / B-10%）
 *  • 鼠标追踪边框高光（mix-blend-mode: screen + overlay）
 *  • 弹性形变（calculateDirectionalScale + calculateElasticTranslation）
 *
 * 置换贴图来自：node_modules/liquid-glass-react/dist/index.esm.js
 * 已保存至：    static/img/disp-standard.jpg
 */

(function () {
  'use strict';

  if (!document.documentElement.classList.contains('theme-apple')) return;

  /* ─────────────────────────────────────────────────────────────
     参数（与 liquid-glass-react 默认值一致）
  ───────────────────────────────────────────────────────────── */
  const DISPLACEMENT_SCALE   = 70;   // 与库默认值一致
  const ABERRATION_INTENSITY = 2;    // 色差强度
  const ELASTICITY           = 0.15; // 弹性系数（与库 elasticity prop 一致）
  const CORNER_RADIUS        = 20;   // px，与卡片 border-radius 一致
  const ACTIVATION_ZONE      = 200;  // 弹性激活距离（px）
  const DISP_MAP_URL         = '/static/img/disp-standard.jpg';

  /* 由贴图计算 blur stdDeviation（与库公式一致） */
  const BLUR_STD = Math.max(0.1, 0.5 - ABERRATION_INTENSITY * 0.1); // 0.3

  /* ─────────────────────────────────────────────────────────────
     1. SVG 滤镜（完整复制 liquid-glass-react GlassFilter）
  ───────────────────────────────────────────────────────────── */
  function ensureSVGDefs() {
    if (document.getElementById('lg-svg-defs')) return;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.id = 'lg-svg-defs';
    svg.setAttribute('style', 'position:fixed;width:0;height:0;overflow:hidden;pointer-events:none;top:0;left:0;');
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = '<defs></defs>';
    document.body.appendChild(svg);
  }

  function createFilterForElement(index) {
    ensureSVGDefs();
    const defs     = document.querySelector('#lg-svg-defs defs');
    const filterId = `lg-refract-${index}`;
    const imgId    = `lg-disp-img-${index}`;
    const maskId   = `lg-edge-mask-${index}`;

    if (document.getElementById(filterId)) {
      return { filterId, feImageEl: document.getElementById(imgId) };
    }

    const scaleR = -DISPLACEMENT_SCALE;
    const scaleG = -(DISPLACEMENT_SCALE * (1 - ABERRATION_INTENSITY * 0.05));
    const scaleB = -(DISPLACEMENT_SCALE * (1 - ABERRATION_INTENSITY * 0.10));
    const maskStop = Math.max(30, 80 - ABERRATION_INTENSITY * 2); // 76

    const ns = 'http://www.w3.org/2000/svg';
    const filter = document.createElementNS(ns, 'filter');
    filter.id = filterId;
    filter.setAttribute('x', '-35%');
    filter.setAttribute('y', '-35%');
    filter.setAttribute('width', '170%');
    filter.setAttribute('height', '170%');
    filter.setAttribute('color-interpolation-filters', 'sRGB');

    filter.innerHTML = `
      <radialGradient id="${maskId}" cx="50%" cy="50%" r="50%">
        <stop offset="0%"          stop-color="black" stop-opacity="0"/>
        <stop offset="${maskStop}%" stop-color="black" stop-opacity="0"/>
        <stop offset="100%"        stop-color="white" stop-opacity="1"/>
      </radialGradient>

      <!-- 置换贴图（liquid-glass-react 真实贴图） -->
      <feImage id="${imgId}"
        x="0" y="0" width="100%" height="100%"
        preserveAspectRatio="xMidYMid slice"
        result="DISPLACEMENT_MAP"
        href="${DISP_MAP_URL}"/>

      <!-- 边缘强度掩码：只在边缘折射，中心保持清晰 -->
      <feColorMatrix in="DISPLACEMENT_MAP" type="matrix"
        values="0.3 0.3 0.3 0 0
                0.3 0.3 0.3 0 0
                0.3 0.3 0.3 0 0
                0   0   0   1 0"
        result="EDGE_INTENSITY"/>
      <feComponentTransfer in="EDGE_INTENSITY" result="EDGE_MASK">
        <feFuncA type="discrete" tableValues="0 ${ABERRATION_INTENSITY * 0.05} 1"/>
      </feComponentTransfer>

      <!-- 原始中心（无位移） -->
      <feOffset in="SourceGraphic" dx="0" dy="0" result="CENTER_ORIGINAL"/>

      <!-- R 通道 -->
      <feDisplacementMap in="SourceGraphic" in2="DISPLACEMENT_MAP"
        scale="${scaleR}" xChannelSelector="R" yChannelSelector="B"
        result="RED_DISPLACED"/>
      <feColorMatrix in="RED_DISPLACED" type="matrix"
        values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"
        result="RED_CHANNEL"/>

      <!-- G 通道（色差 -5%） -->
      <feDisplacementMap in="SourceGraphic" in2="DISPLACEMENT_MAP"
        scale="${scaleG.toFixed(3)}" xChannelSelector="R" yChannelSelector="B"
        result="GREEN_DISPLACED"/>
      <feColorMatrix in="GREEN_DISPLACED" type="matrix"
        values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"
        result="GREEN_CHANNEL"/>

      <!-- B 通道（色差 -10%） -->
      <feDisplacementMap in="SourceGraphic" in2="DISPLACEMENT_MAP"
        scale="${scaleB.toFixed(3)}" xChannelSelector="R" yChannelSelector="B"
        result="BLUE_DISPLACED"/>
      <feColorMatrix in="BLUE_DISPLACED" type="matrix"
        values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"
        result="BLUE_CHANNEL"/>

      <!-- 三通道 screen 混合 -->
      <feBlend in="GREEN_CHANNEL" in2="BLUE_CHANNEL" mode="screen" result="GB_COMBINED"/>
      <feBlend in="RED_CHANNEL"   in2="GB_COMBINED"  mode="screen" result="RGB_COMBINED"/>

      <!-- 轻微模糊柔化色差 -->
      <feGaussianBlur in="RGB_COMBINED" stdDeviation="${BLUR_STD}" result="ABERRATED_BLURRED"/>

      <!-- 只在边缘应用折射效果 -->
      <feComposite in="ABERRATED_BLURRED" in2="EDGE_MASK" operator="in" result="EDGE_ABERRATION"/>

      <!-- 反转遮罩，保留中心干净原图 -->
      <feComponentTransfer in="EDGE_MASK" result="INVERTED_MASK">
        <feFuncA type="table" tableValues="1 0"/>
      </feComponentTransfer>
      <feComposite in="CENTER_ORIGINAL" in2="INVERTED_MASK" operator="in" result="CENTER_CLEAN"/>

      <!-- 边缘折射 + 中心原图 -->
      <feComposite in="EDGE_ABERRATION" in2="CENTER_CLEAN" operator="over"/>
    `;

    defs.appendChild(filter);
    return { filterId, feImageEl: document.getElementById(imgId) };
  }

  /* ─────────────────────────────────────────────────────────────
     2. 高光边框 Overlay（来自 liquid-glass-react span 元素）
        两层：mix-blend-mode screen(opacity 0.2) + overlay
        使用 WebkitMask xor 技巧只在边框上显示
  ───────────────────────────────────────────────────────────── */
  function createHighlightOverlays(el) {
    const shared = {
      position: 'absolute',
      inset: '0',
      borderRadius: `${CORNER_RADIUS}px`,
      pointerEvents: 'none',
      padding: '1.5px',
      WebkitMask: 'linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)',
      WebkitMaskComposite: 'xor',
      maskComposite: 'exclude',
      boxShadow: '0 0 0 0.5px rgba(255,255,255,0.5) inset, 0 1px 3px rgba(255,255,255,0.25) inset, 0 1px 4px rgba(0,0,0,0.35)',
      transition: 'background 0.06s linear',
    };

    const s1 = document.createElement('span');
    Object.assign(s1.style, shared, { mixBlendMode: 'screen', opacity: '0.2', zIndex: '2' });
    s1.setAttribute('aria-hidden', 'true');

    const s2 = document.createElement('span');
    Object.assign(s2.style, shared, { mixBlendMode: 'overlay', zIndex: '3' });
    s2.setAttribute('aria-hidden', 'true');

    // 确保父元素 position:relative，不截断 overlay
    const pos = getComputedStyle(el).position;
    if (pos === 'static') el.style.position = 'relative';

    el.appendChild(s1);
    el.appendChild(s2);
    return { s1, s2 };
  }

  function updateHighlight(s1, s2, offsetX, offsetY) {
    const angle = 135 + offsetX * 1.2;
    const a     = Math.abs(offsetX);
    const grad = `linear-gradient(
      ${angle.toFixed(1)}deg,
      rgba(255,255,255,0.0) 0%,
      rgba(255,255,255,${(0.12 + a * 0.008).toFixed(3)}) ${Math.max(10, 33 + offsetY * 0.3).toFixed(1)}%,
      rgba(255,255,255,${(0.40 + a * 0.012).toFixed(3)}) ${Math.min(90, 66 + offsetY * 0.4).toFixed(1)}%,
      rgba(255,255,255,0.0) 100%
    )`;
    const grad2 = `linear-gradient(
      ${angle.toFixed(1)}deg,
      rgba(255,255,255,0.0) 0%,
      rgba(255,255,255,${(0.32 + a * 0.008).toFixed(3)}) ${Math.max(10, 33 + offsetY * 0.3).toFixed(1)}%,
      rgba(255,255,255,${(0.60 + a * 0.012).toFixed(3)}) ${Math.min(90, 66 + offsetY * 0.4).toFixed(1)}%,
      rgba(255,255,255,0.0) 100%
    )`;
    s1.style.background = grad;
    s2.style.background = grad2;
  }

  /* ─────────────────────────────────────────────────────────────
     3. 弹性形变（calculateDirectionalScale 完整移植）
  ───────────────────────────────────────────────────────────── */
  function calcScale(rect, mx, my) {
    if (!mx && !my) return 'scale(1)';

    const cx = rect.left + rect.width  / 2;
    const cy = rect.top  + rect.height / 2;
    const dx = mx - cx, dy = my - cy;

    const edX = Math.max(0, Math.abs(dx) - rect.width  / 2);
    const edY = Math.max(0, Math.abs(dy) - rect.height / 2);
    const ed  = Math.hypot(edX, edY);

    if (ed > ACTIVATION_ZONE) return 'scale(1)';

    const fadeIn = 1 - ed / ACTIVATION_ZONE;
    const cd     = Math.hypot(dx, dy) || 1;
    const nx = dx / cd, ny = dy / cd;
    const si = Math.min(cd / 300, 1) * ELASTICITY * fadeIn;

    const sx = 1 + Math.abs(nx) * si * 0.3 - Math.abs(ny) * si * 0.15;
    const sy = 1 + Math.abs(ny) * si * 0.3 - Math.abs(nx) * si * 0.15;
    return `scaleX(${Math.max(0.8, sx).toFixed(4)}) scaleY(${Math.max(0.8, sy).toFixed(4)})`;
  }

  function calcTranslate(rect, mx, my) {
    if (!mx && !my) return { x: 0, y: 0 };

    const cx = rect.left + rect.width  / 2;
    const cy = rect.top  + rect.height / 2;

    const edX = Math.max(0, Math.abs(mx - cx) - rect.width  / 2);
    const edY = Math.max(0, Math.abs(my - cy) - rect.height / 2);
    const ed  = Math.hypot(edX, edY);
    const fadeIn = ed > ACTIVATION_ZONE ? 0 : 1 - ed / ACTIVATION_ZONE;

    return {
      x: (mx - cx) * ELASTICITY * 0.1 * fadeIn,
      y: (my - cy) * ELASTICITY * 0.1 * fadeIn,
    };
  }

  /* ─────────────────────────────────────────────────────────────
     4. 实例管理
  ───────────────────────────────────────────────────────────── */
  let _instanceCount = 0;
  const _instances = new WeakMap();
  const mouse = { x: 0, y: 0 };

  document.addEventListener('mousemove', function (e) {
    mouse.x = e.clientX;
    mouse.y = e.clientY;
    _instances._allEls && _instances._allEls.forEach(el => tickEl(el));
  }, { passive: true });
  // Track all registered elements globally
  const _allEls = new Set();

  function initGlassElement(el) {
    if (_instances.has(el)) return;

    const index = _instanceCount++;
    const { filterId } = createFilterForElement(index);
    const { s1, s2 }  = createHighlightOverlays(el);

    el.style.filter = `url(#${filterId})`;

    const inst = { filterId, s1, s2, offsetX: 0, offsetY: 0 };
    _instances.set(el, inst);
    _allEls.add(el);
  }

  function tickEl(el) {
    if (!el.isConnected) { _allEls.delete(el); return; }
    const inst = _instances.get(el);
    if (!inst) return;

    const rect = el.getBoundingClientRect();
    const cx   = rect.left + rect.width  / 2;
    const cy   = rect.top  + rect.height / 2;

    inst.offsetX = ((mouse.x - cx) / rect.width)  * 100;
    inst.offsetY = ((mouse.y - cy) / rect.height) * 100;

    // 高光边框
    updateHighlight(inst.s1, inst.s2, inst.offsetX, inst.offsetY);

    // 弹性形变
    const tr = calcTranslate(rect, mouse.x, mouse.y);
    const sc = calcScale(rect, mouse.x, mouse.y);
    el.style.transform     = `${sc} translate(${tr.x.toFixed(2)}px, ${tr.y.toFixed(2)}px)`;
    el.style.transition    = 'transform 0.2s ease-out';
  }

  /* ─────────────────────────────────────────────────────────────
     5. 仪表数据刷新闪光
  ───────────────────────────────────────────────────────────── */
  const GAUGE_IDS = ['data-altitude', 'data-airspeed', 'data-heading', 'data-vs'];

  function flashGauge(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('lg-flash');
    void el.offsetWidth;
    el.classList.add('lg-flash');
    el.addEventListener('animationend', () => el.classList.remove('lg-flash'), { once: true });
  }

  GAUGE_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    new MutationObserver(() => flashGauge(id))
      .observe(el, { childList: true, subtree: true, characterData: true });
  });

  /* ─────────────────────────────────────────────────────────────
     6. 聊天气泡：data-sender 属性
  ───────────────────────────────────────────────────────────── */
  function classifyLogEntry(div) {
    if (div.dataset.sender) return;
    const strong = div.querySelector('strong');
    div.dataset.sender = strong ? strong.textContent.replace(/:$/, '').trim() : 'SYSTEM';
  }

  function watchLogContainer() {
    const log = document.getElementById('log-container');
    if (!log) return;
    Array.from(log.children).forEach(classifyLogEntry);
    new MutationObserver(ms => ms.forEach(m =>
      m.addedNodes.forEach(n => n.nodeType === 1 && classifyLogEntry(n))
    )).observe(log, { childList: true });
  }

  /* ─────────────────────────────────────────────────────────────
     7. 仪表行入场动画
  ───────────────────────────────────────────────────────────── */
  function addStaggerClass() {
    const first = document.querySelector('.gauge-card');
    if (!first) return;
    const row = first.closest('.row');
    if (row && !row.classList.contains('lg-stagger')) row.classList.add('lg-stagger');
  }

  /* ─────────────────────────────────────────────────────────────
     初始化
  ───────────────────────────────────────────────────────────── */
  function registerAll() {
    document.querySelectorAll('.gauge-card, #ptt-btn, .lg-glass-filter').forEach(el => {
      initGlassElement(el);
    });
  }

  function init() {
    ensureSVGDefs();
    registerAll();
    addStaggerClass();
    watchLogContainer();

    // 鼠标全局追踪 → 所有元素实时更新
    document.addEventListener('mousemove', () => {
      _allEls.forEach(el => tickEl(el));
    }, { passive: true });

    // 动态新增元素
    new MutationObserver(mutations => {
      mutations.forEach(m => {
        m.addedNodes.forEach(node => {
          if (node.nodeType !== 1) return;
          ['gauge-card', 'lg-glass-filter'].forEach(cls => {
            if (node.classList.contains(cls)) initGlassElement(node);
            node.querySelectorAll('.' + cls).forEach(child => initGlassElement(child));
          });
          if (node.id === 'ptt-btn') initGlassElement(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
