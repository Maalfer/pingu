/* cast.js — BaluHome Cast Button v2 */
(function () {
  'use strict';

  const CAST_ICON = `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22" aria-hidden="true">
    <path d="M21 3H3c-1.1 0-2 .9-2 2v3h2V5h18v14h-7v2h7c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM1 18v3h3c0-1.66-1.34-3-3-3zm0-4v2c2.76 0 5 2.24 5 5h2c0-3.87-3.13-7-7-7zm0-4v2c4.97 0 9 4.03 9 9h2C12 14.14 7.03 9 1 9z"/>
  </svg>`;

  /* ── Inject modal CSS directly to avoid overflow:hidden on body ── */
  const STYLE = `
    #cast-portal { position:fixed; top:0; right:0; bottom:0; left:0; z-index:99999; display:flex; align-items:flex-end; justify-content:center; background:rgba(0,0,0,.6); animation:_cfadeIn .2s ease; }
    @keyframes _cfadeIn { from{opacity:0} to{opacity:1} }
    #cast-portal .cm { background:#1a1a2e; border-radius:20px 20px 0 0; width:100%; max-width:520px; padding-bottom:max(env(safe-area-inset-bottom),16px); animation:_cslideUp .25s cubic-bezier(.4,0,.2,1); box-shadow:0 -4px 32px rgba(0,0,0,.5); }
    @keyframes _cslideUp { from{transform:translateY(100%)} to{transform:translateY(0)} }
    #cast-portal .cm-handle { width:40px; height:4px; background:rgba(255,255,255,.18); border-radius:2px; margin:10px auto 0; }
    #cast-portal .cm-head { display:flex; align-items:center; gap:.6rem; padding:.9rem 1.2rem .7rem; font-weight:700; font-size:1rem; color:#fff; border-bottom:1px solid rgba(255,255,255,.07); }
    #cast-portal .cm-head svg:first-child { color:#06b6d4; flex-shrink:0; }
    #cast-portal .cm-head span { flex:1; }
    #cast-portal .cm-close { background:rgba(255,255,255,.08); border:none; color:rgba(255,255,255,.6); cursor:pointer; padding:5px; border-radius:8px; display:flex; align-items:center; }
    #cast-portal .cm-close:hover { background:rgba(255,255,255,.15); color:#fff; }
    #cast-portal .cm-body { padding:1rem 1.3rem 1.2rem; color:rgba(255,255,255,.85); font-size:.93rem; }
    #cast-portal .cm-search-btn { display:flex; align-items:center; justify-content:center; gap:.5rem; width:100%; padding:.85rem; background:linear-gradient(135deg,#06b6d4,#0891b2); color:#fff; font-weight:700; font-size:1rem; border:none; border-radius:14px; cursor:pointer; margin-bottom:1rem; }
    #cast-portal .cm-search-btn:active { opacity:.85; }
    #cast-portal .cm-searching { text-align:center; color:rgba(255,255,255,.6); font-size:.88rem; padding:.5rem 0 1rem; }
    #cast-portal .cm-devs { list-style:none; margin:0 0 1rem; padding:0; }
    #cast-portal .cm-devs li { display:flex; align-items:center; gap:.7rem; padding:.75rem .9rem; background:rgba(255,255,255,.06); border-radius:12px; margin-bottom:.4rem; cursor:pointer; color:#fff; }
    #cast-portal .cm-devs li:hover { background:rgba(255,255,255,.12); }
    #cast-portal .cm-lead { font-weight:600; margin:0 0 .5rem; font-size:.95rem; }
    #cast-portal .cm-steps { margin:.5rem 0 .8rem; padding-left:1.3rem; line-height:2; color:rgba(255,255,255,.8); }
    #cast-portal .cm-steps strong { color:#fff; }
    #cast-portal .cm-steps a { color:#67e8f9; text-decoration:underline; cursor:pointer; }
    #cast-portal .cm-tip { font-size:.82rem; color:rgba(255,255,255,.45); margin:0; padding:.55rem .8rem; background:rgba(255,255,255,.04); border-radius:8px; }
    #cast-portal .cm-divider { border:none; border-top:1px solid rgba(255,255,255,.07); margin:.75rem 0; }
  `;
  if (!document.getElementById('_cast_styles')) {
    const s = document.createElement('style');
    s.id = '_cast_styles';
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  /* ── Button factory ── */
  function mkBtn(sidebar) {
    const btn = document.createElement('button');
    btn.className = sidebar ? 'cast-btn cast-btn-sidebar' : 'cast-btn';
    btn.title = 'Emitir a la TV';
    btn.setAttribute('aria-label', 'Emitir a la TV');
    btn.innerHTML = CAST_ICON;
    btn.addEventListener('click', handleCast);
    return btn;
  }

  /* ── Main cast handler ── */
  async function handleCast(e) {
    e.stopPropagation();

    // 1. Remote Playback API for video/audio (Chromecast + AirPlay)
    const media = document.querySelector('video') || document.getElementById('audio-player');
    if (media && media.src && 'remote' in HTMLMediaElement.prototype) {
      try {
        await media.remote.prompt();
        return;
      } catch (err) {
        if (err.name === 'AbortError') return;
        // InvalidStateError (no src) or NotSupportedError → continue
      }
    }

    // 2. Show modal with real device search + fallback instructions
    showCastModal();
  }

  /* ── Network info via Network Information API ── */
  function getNetworkInfo() {
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const type = conn ? conn.type : null;          // 'wifi','cellular','ethernet','none','other','unknown'
    const effective = conn ? conn.effectiveType : null; // '4g','3g','2g'
    const isWifi = type === 'wifi';
    const isCellular = type === 'cellular' || (!isWifi && effective && effective !== 'unknown');
    return { isWifi, isCellular, type, effective };
  }

  /* ── Cast modal with device discovery ── */
  function showCastModal() {
    if (document.getElementById('cast-portal')) return;

    const isAndroid = /Android/i.test(navigator.userAgent);
    const isIOS = /iPad|iPhone|iPod/i.test(navigator.userAgent) && !window.MSStream;

    const portal = document.createElement('div');
    portal.id = 'cast-portal';

    portal.innerHTML = `
      <div class="cm" role="dialog" aria-modal="true" aria-label="Emitir a la TV">
        <div class="cm-handle"></div>
        <div class="cm-head">
          ${CAST_ICON}
          <span>Emitir a la TV</span>
          <button class="cm-close" aria-label="Cerrar">
            <svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
          </button>
        </div>
        <div class="cm-body" id="cm-body-inner">
          ${buildBody(isAndroid, isIOS)}
        </div>
      </div>`;

    portal.querySelector('.cm-close').addEventListener('click', closeModal);
    portal.addEventListener('click', e => { if (e.target === portal) closeModal(); });

    // Append to <html> to bypass body overflow:hidden
    document.documentElement.appendChild(portal);

    // Wire search button
    const searchBtn = portal.querySelector('#cm-search-btn');
    if (searchBtn) searchBtn.addEventListener('click', searchDevices);

    // Show network info
    const { isWifi, isCellular } = getNetworkInfo();
    const body = document.getElementById('cm-body-inner');
    if (body) {
      const banner = document.createElement('div');
      let dot, label, sub, bg;
      if (isWifi) {
        dot = '🟢'; label = 'Conectado a WiFi';
        sub = 'Los dispositivos deben estar en la misma red WiFi';
        bg = 'rgba(34,197,94,.1)';
      } else if (isCellular) {
        dot = '🔴'; label = 'Datos móviles activos';
        sub = 'Conéctate a tu WiFi de casa para poder emitir a la TV';
        bg = 'rgba(239,68,68,.12)';
      } else {
        dot = '🟡'; label = 'Red desconocida';
        sub = 'Asegúrate de estar en la misma WiFi que tu TV';
        bg = 'rgba(255,255,255,.05)';
      }
      banner.style.cssText = 'display:flex;align-items:flex-start;gap:.55rem;background:' + bg + ';border-radius:10px;padding:.6rem .85rem;margin-bottom:.9rem;font-size:.83rem;';
      banner.innerHTML = '<span style="font-size:1.2rem;line-height:1.4">' + dot + '</span>'
        + '<span style="color:rgba(255,255,255,.85)"><strong style="color:#fff;display:block;margin-bottom:2px">' + label + '</strong>' + sub + '</span>';
      body.insertBefore(banner, body.firstChild);
    }
  }

  function closeModal() {
    const p = document.getElementById('cast-portal');
    if (p) p.remove();
  }

  function buildBody(isAndroid, isIOS) {
    const searchBtn = `
      <button class="cm-search-btn" id="cm-search-btn">
        <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
        Buscar dispositivos en la red
      </button>
      <div id="cm-devlist"></div>
      <hr class="cm-divider">`;

    if (isAndroid) {
      return `${searchBtn}
        <p class="cm-lead">Cómo emitir desde Android:</p>
        <ol class="cm-steps">
          <li>En Chrome: pulsa <strong>⋮ → Emitir…</strong> y elige tu TV</li>
          <li>O desliza el <strong>Panel de notificaciones</strong> → <strong>Transmitir / Smart View</strong></li>
          <li>Selecciona tu <strong>Chromecast o Smart TV</strong> en la lista</li>
        </ol>
        <p class="cm-tip">💡 Asegúrate de que el móvil y la TV están en la misma WiFi.</p>`;
    } else if (isIOS) {
      return `${searchBtn}
        <p class="cm-lead">Cómo emitir desde iPhone/iPad:</p>
        <ol class="cm-steps">
          <li>Desliza desde la <strong>esquina superior derecha</strong> → Centro de control</li>
          <li>Pulsa <strong>Duplicar pantalla</strong> 🔲</li>
          <li>Selecciona tu <strong>Apple TV</strong> o Smart TV compatible con AirPlay</li>
        </ol>
        <p class="cm-tip">💡 Samsung, LG, Sony modernas suelen ser compatibles con AirPlay.</p>`;
    } else {
      return `${searchBtn}
        <p class="cm-lead">Cómo emitir desde el ordenador:</p>
        <ol class="cm-steps">
          <li>En <strong>Chrome</strong>: pulsa <strong>⋮ → Guardar y compartir → Emitir…</strong></li>
          <li>En <strong>Edge</strong>: pulsa <strong>… → Más → Proyectar multimedia a un dispositivo</strong></li>
        </ol>
        <p class="cm-tip">💡 Asegúrate de que el ordenador y la TV están en la misma red WiFi.</p>`;
    }
  }

  /* ── Device discovery via Presentation API ── */
  async function searchDevices() {
    const btn = document.getElementById('cm-search-btn');
    const devlist = document.getElementById('cm-devlist');
    if (!btn || !devlist) return;

    btn.disabled = true;
    btn.textContent = 'Buscando…';
    devlist.innerHTML = '<p class="cm-searching">Buscando dispositivos en tu red…</p>';

    if (!window.PresentationRequest) {
      devlist.innerHTML = '<p class="cm-searching">La búsqueda automática no está disponible en este navegador.<br>Usa las instrucciones de abajo.</p>';
      btn.textContent = 'Reintentar';
      btn.disabled = false;
      return;
    }

    try {
      const req = new PresentationRequest([location.href]);

      // Check availability first
      let available = false;
      try {
        const avail = await req.getAvailability();
        available = avail.value;
        avail.onchange = () => { available = avail.value; };
      } catch (_) { /* getAvailability not supported → try start directly */ }

      // Start presentation (shows Chrome's native device picker)
      const conn = await req.start();
      devlist.innerHTML = '';
      closeModal();
      // Keep connection alive
      conn.onmessage = () => {};
    } catch (err) {
      if (err.name === 'AbortError') {
        devlist.innerHTML = '<p class="cm-searching">Búsqueda cancelada.</p>';
      } else if (err.name === 'NotFoundError') {
        devlist.innerHTML = '<p class="cm-searching">⚠️ No se encontraron dispositivos.<br>Comprueba que la TV está encendida y en la misma WiFi.</p>';
      } else {
        devlist.innerHTML = `<p class="cm-searching">⚠️ ${err.message || 'No se pudo conectar.'}<br>Usa las instrucciones de abajo.</p>`;
      }
      btn.textContent = 'Buscar de nuevo';
      btn.disabled = false;
    }
  }

  /* ── Insert button into page header ── */
  function insertButton() {
    if (document.querySelector('.cast-btn')) return; // already present

    const homeUserInfo = document.querySelector('.home-user-info');
    if (homeUserInfo) {
      const btn = mkBtn(false);
      const refreshBtn = homeUserInfo.querySelector('#btn-refresh');
      homeUserInfo.insertBefore(btn, refreshBtn || homeUserInfo.firstChild);
      return;
    }

    const header = document.querySelector('.subapp-header, .vid-header');
    if (header) { header.appendChild(mkBtn(false)); return; }

    const sidebarUser = document.querySelector('.sidebar-user');
    if (sidebarUser) { sidebarUser.insertBefore(mkBtn(true), sidebarUser.firstChild); }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', insertButton);
  } else {
    insertButton();
  }
})();
