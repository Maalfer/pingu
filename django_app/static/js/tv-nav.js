/**
 * tv-nav.js — D-pad / TV remote spatial navigation
 * Activates when: (a) RemotePlayback is connected, (b) device has no touch/pointer,
 * or (c) window.tvNavForce = true (debug).
 */
(function () {
  'use strict';

  const FOCUSABLE = [
    'a[href]', 'button:not([disabled])', 'input:not([disabled])',
    'select:not([disabled])', 'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])', '[role="button"]'
  ].join(',');

  let tvMode = false;
  let lastFocused = null;

  /* ── Detect TV/remote context ── */
  function activateTvMode() {
    if (tvMode) return;
    tvMode = true;
    document.documentElement.classList.add('tv-nav');
    // Focus first meaningful element
    const first = document.querySelector('.app-card, .nav-item, .bnav-item, ' + FOCUSABLE);
    if (first) first.focus();
  }

  // Activate if no fine pointer (coarse = touch screen, none = TV/remote)
  if (window.matchMedia('(pointer: none), (pointer: coarse)').matches) {
    // Don't activate on coarse-only (mobile), only on pointer:none (TV)
    if (window.matchMedia('(pointer: none)').matches) activateTvMode();
  }

  // Activate when arrow keys are pressed (TV remote sends arrow keys)
  document.addEventListener('keydown', function onFirstKey(e) {
    if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
      activateTvMode();
      document.removeEventListener('keydown', onFirstKey);
    }
  }, { once: false });

  /* ── Spatial navigation ── */
  function getFocusable() {
    return Array.from(document.querySelectorAll(FOCUSABLE)).filter(el => {
      const s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetParent !== null;
    });
  }

  function rect(el) {
    return el.getBoundingClientRect();
  }

  function center(r) {
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function dist(a, b) {
    return Math.sqrt(Math.pow(a.x - b.x, 2) + Math.pow(a.y - b.y, 2));
  }

  function isInDirection(fromC, toC, dir) {
    const dx = toC.x - fromC.x;
    const dy = toC.y - fromC.y;
    switch (dir) {
      case 'ArrowRight': return dx > 0 && Math.abs(dx) >= Math.abs(dy) * 0.5;
      case 'ArrowLeft':  return dx < 0 && Math.abs(dx) >= Math.abs(dy) * 0.5;
      case 'ArrowDown':  return dy > 0 && Math.abs(dy) >= Math.abs(dx) * 0.5;
      case 'ArrowUp':    return dy < 0 && Math.abs(dy) >= Math.abs(dx) * 0.5;
    }
    return false;
  }

  function moveFocus(dir) {
    const current = document.activeElement;
    const all = getFocusable();
    if (!all.length) return;

    if (!current || current === document.body) {
      all[0].focus({ preventScroll: false });
      return;
    }

    const fromR = rect(current);
    const fromC = center(fromR);

    let best = null;
    let bestScore = Infinity;

    all.forEach(el => {
      if (el === current) return;
      const toR = rect(el);
      const toC = center(toR);
      if (!isInDirection(fromC, toC, dir)) return;

      // Score: weighted distance (prefer elements more aligned with direction)
      const d = dist(fromC, toC);
      const perpendicular = dir === 'ArrowLeft' || dir === 'ArrowRight'
        ? Math.abs(toC.y - fromC.y) : Math.abs(toC.x - fromC.x);
      const score = d + perpendicular * 0.7;

      if (score < bestScore) { bestScore = score; best = el; }
    });

    if (best) {
      best.focus({ preventScroll: false });
      best.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    }
  }

  /* ── Key handler ── */
  document.addEventListener('keydown', function (e) {
    if (!tvMode && !window.tvNavForce) return;

    switch (e.key) {
      case 'ArrowUp':
      case 'ArrowDown':
      case 'ArrowLeft':
      case 'ArrowRight': {
        // Let video/audio elements handle arrow keys natively when focused
        const active = document.activeElement;
        if (active && (active.tagName === 'VIDEO' || active.tagName === 'AUDIO')) return;
        // Let inputs handle left/right
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) {
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') return;
        }
        e.preventDefault();
        moveFocus(e.key);
        break;
      }
      case 'Enter': {
        const active = document.activeElement;
        if (active && active !== document.body) {
          // Trigger click for non-button elements
          if (active.tagName !== 'BUTTON' && active.tagName !== 'A') {
            active.click();
          }
        }
        break;
      }
      case 'Backspace':
      case 'GoBack': {
        // TV back button → go back in history
        if (history.length > 1) { e.preventDefault(); history.back(); }
        break;
      }
    }
  });

  /* ── Make app-cards and list items focusable ── */
  function makeFocusable() {
    const selectors = [
      '.app-card', '.vid-card', '.nav-item', '.bnav-item',
      '.shop-item', '.note-card', '.todo-item', '.friend-card',
      '.msg-bubble', '.cast-btn'
    ];
    selectors.forEach(sel => {
      document.querySelectorAll(sel).forEach(el => {
        if (!el.getAttribute('tabindex')) el.setAttribute('tabindex', '0');
        // Enter key activates the element
        el.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            el.click();
          }
        });
      });
    });
  }

  // Run on load and after dynamic content changes
  document.addEventListener('DOMContentLoaded', makeFocusable);
  // Re-run when new content is added (shopping sync, notes sync, etc.)
  const observer = new MutationObserver(makeFocusable);
  document.addEventListener('DOMContentLoaded', () => {
    observer.observe(document.body, { childList: true, subtree: true });
  });

  /* ── Video player D-pad controls (OSD + volume; seek/play handled by videos.html) ── */
  document.addEventListener('DOMContentLoaded', function () {
    const video = document.getElementById('main-video');
    if (!video) return;

    // Patch existing videos.html keydown to show OSD feedback
    const _orig = video._tvPatched;
    if (_orig) return;
    video._tvPatched = true;

    document.addEventListener('keydown', function (e) {
      const playerOpen = document.getElementById('player-overlay');
      const isOpen = playerOpen && !playerOpen.classList.contains('hidden');
      if (!isOpen) return;

      switch (e.key) {
        case 'ArrowRight': showOSD('+10s'); break;
        case 'ArrowLeft':  showOSD('-10s'); break;
        case 'ArrowUp':
          e.preventDefault();
          video.volume = Math.min(1, video.volume + 0.1);
          showOSD('🔊 ' + Math.round(video.volume * 100) + '%');
          break;
        case 'ArrowDown':
          e.preventDefault();
          video.volume = Math.max(0, video.volume - 0.1);
          showOSD('🔉 ' + Math.round(video.volume * 100) + '%');
          break;
        case ' ':  showOSD(video.paused ? '▶' : '⏸'); break;
        case 'Enter': showOSD(video.paused ? '▶' : '⏸'); break;
      }
    });
  });

  /* ── On-screen display for video controls ── */
  let osdTimer = null;
  function showOSD(text) {
    let osd = document.getElementById('tv-osd');
    if (!osd) {
      osd = document.createElement('div');
      osd.id = 'tv-osd';
      osd.style.cssText = [
        'position:fixed', 'bottom:10%', 'left:50%', 'transform:translateX(-50%)',
        'background:rgba(0,0,0,0.75)', 'color:#fff', 'font-size:2.5rem',
        'padding:0.5rem 1.5rem', 'border-radius:12px', 'z-index:9999',
        'pointer-events:none', 'transition:opacity 0.3s'
      ].join(';');
      document.body.appendChild(osd);
    }
    osd.textContent = text;
    osd.style.opacity = '1';
    clearTimeout(osdTimer);
    osdTimer = setTimeout(() => { osd.style.opacity = '0'; }, 1500);
  }

  /* ── Remote Playback state indicator ── */
  document.addEventListener('DOMContentLoaded', function () {
    const video = document.getElementById('main-video');
    if (!video || !video.remote) return;

    const indicator = document.createElement('div');
    indicator.id = 'tv-cast-indicator';
    indicator.style.cssText = [
      'display:none', 'position:fixed', 'top:1rem', 'right:1rem',
      'background:rgba(0,0,0,0.7)', 'color:#fff', 'font-size:0.9rem',
      'padding:0.4rem 0.8rem', 'border-radius:20px', 'z-index:9000',
      'align-items:center', 'gap:0.4rem'
    ].join(';');
    indicator.innerHTML = '📺 Emitiendo en TV';
    document.body.appendChild(indicator);

    video.remote.onconnect = () => {
      indicator.style.display = 'flex';
      activateTvMode();
    };
    video.remote.ondisconnect = () => {
      indicator.style.display = 'none';
    };
  });

  window._tvNav = { activate: activateTvMode, moveFocus };
})();
