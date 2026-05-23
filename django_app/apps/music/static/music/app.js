/* =============================================
   BALUSONG — Main Application JS
   ============================================= */

'use strict';

// =============================================
// ROUTER
// =============================================
const Router = {
  currentPage: null,

  async navigate(page, params = {}) {
    if (this.currentPage === page && !Object.keys(params).length) return;

    // Update nav active states
    document.querySelectorAll('[data-page]').forEach(el => {
      el.classList.toggle('active', el.dataset.page === page);
    });

    const contentArea = document.getElementById('content-area');
    contentArea.innerHTML = '<div class="loading-spinner"><div class="spinner"></div></div>';

    try {
      const qs = new URLSearchParams(params).toString();
      const url = `/fragments/${page}${qs ? '?' + qs : ''}`;
      const res = await fetch(url, { headers: { 'X-Fragment': '1' } });

      if (res.status === 401) { window.location = '/'; return; }
      if (res.status === 403) {
        contentArea.innerHTML = `<div class="empty-state"><p>Sin permisos para esta sección</p></div>`;
        return;
      }
      if (!res.ok) throw new Error('HTTP ' + res.status);

      const html = await res.text();
      contentArea.innerHTML = html;
      // innerHTML does not execute <script> tags — re-run them manually
      contentArea.querySelectorAll('script').forEach(s => {
        const t = document.createElement('script');
        t.textContent = s.textContent;
        s.replaceWith(t);
      });
      this.currentPage = page;
      this.afterLoad(page);
    } catch (e) {
      contentArea.innerHTML = `<div class="empty-state"><p>Error cargando página</p></div>`;
    }
  },

  afterLoad(page) {
    if (page === 'library') {
      Library.init();
    }
    // Restore playing state highlight
    Player.highlightCurrent();
  }
};

// Player + formatTime ahora viven en /static/js/player.js (cargado por _base.html).

// =============================================
// LIBRARY
// =============================================
const Library = {
  init() {
    this.bindSearch();
    this.bindSongList();
    this.bindContextMenu();
    this.bindCacheClear();

    const addBtn = document.getElementById('open-add-modal');
    if (addBtn) addBtn.addEventListener('click', () => Modal.open());
  },

  bindCacheClear() {
    const btn = document.getElementById('btn-clear-music-cache');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      if (!confirm('¿Borrar las canciones descargadas en este dispositivo?\n\nVolverás a oírlas desde el servidor la próxima vez.')) return;
      btn.disabled = true;
      const prev = btn.innerHTML;
      btn.innerHTML = '<div class="spinner" style="width:18px;height:18px;border-width:2px"></div>';
      try {
        // Borrado directo (por si el SW no contesta) + ping al SW para que limpie su referencia interna.
        if ('caches' in window) await caches.delete('pingu-music-v1').catch(() => {});
        if (navigator.serviceWorker && navigator.serviceWorker.controller) {
          navigator.serviceWorker.controller.postMessage({ type: 'clear-music-cache' });
        }
        // Feedback visual: cambiar a tick durante 1.2s.
        btn.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';
        setTimeout(() => { btn.innerHTML = prev; btn.disabled = false; }, 1200);
      } catch (e) {
        btn.innerHTML = prev; btn.disabled = false;
        alert('No se pudo borrar la caché: ' + e.message);
      }
    });
  },

  bindSearch() {
    const input = document.getElementById('search-input');
    if (!input) return;

    let debounce;
    input.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        Router.navigate(Router.currentPage, { search: input.value });
      }, 400);
    });

    const clearBtn = document.getElementById('search-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        Router.navigate(Router.currentPage, {});
      });
    }
  },

  bindSongList() {
    const list = document.getElementById('song-list');
    if (!list) return;

    list.addEventListener('click', (e) => {
      const item = e.target.closest('.song-item');
      if (!item) return;

      // If menu button clicked, let bindContextMenu handle it
      if (e.target.closest('.song-menu-btn')) return;

      const index = parseInt(item.dataset.index);
      const songs = window._librarySongs || [];
      if (songs[index]) {
        Player.playSong(songs[index], songs, index);
        Player.highlightCurrent();
      }
    });
  },

  bindContextMenu() {
    const menu = document.getElementById('song-context-menu');
    if (!menu) return;

    let activeSongId = null;

    document.querySelectorAll('.song-menu-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        activeSongId = parseInt(btn.dataset.songId);
        const rect = btn.getBoundingClientRect();
        menu.classList.remove('hidden');

        // Position menu
        const menuH = 100;
        const top = rect.bottom + 4;
        const adjustedTop = (top + menuH > window.innerHeight) ? rect.top - menuH - 4 : top;
        menu.style.top  = adjustedTop + 'px';
        menu.style.right = (window.innerWidth - rect.right) + 'px';
        menu.style.left = 'auto';
      });
    });

    const ctxPlay = document.getElementById('ctx-play');
    if (ctxPlay) {
      ctxPlay.addEventListener('click', () => {
        menu.classList.add('hidden');
        const songs = window._librarySongs || [];
        const idx = songs.findIndex(s => s.id == activeSongId);
        if (idx >= 0) {
          Player.playSong(songs[idx], songs, idx);
          Player.highlightCurrent();
        }
      });
    }

    const ctxDelete = document.getElementById('ctx-delete');
    if (ctxDelete) {
      ctxDelete.addEventListener('click', () => {
        menu.classList.add('hidden');
        if (!activeSongId) return;
        if (!confirm('¿Eliminar esta canción de tu biblioteca?')) return;
        this.deleteSong(activeSongId);
      });
    }

    document.addEventListener('click', (e) => {
      if (!menu.contains(e.target) && !e.target.closest('.song-menu-btn')) {
        menu.classList.add('hidden');
      }
    });
  },

  async deleteSong(songId) {
    try {
      const res = await fetch('/api/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: songId, csrf_token: APP.csrf })
      });
      const data = await res.json();
      if (data.success) {
        // Remove from DOM immediately
        const el = document.querySelector(`.song-item[data-song-id="${songId}"]`);
        if (el) {
          el.style.opacity = '0';
          el.style.height = '0';
          el.style.overflow = 'hidden';
          el.style.transition = 'all 0.25s ease';
          setTimeout(() => el.remove(), 250);
        }
        // Remove from queue if currently playing
        const idx = Player.queue.findIndex(s => s.id == songId);
        if (idx >= 0) {
          Player.queue.splice(idx, 1);
          if (Player.currentIndex >= idx) Player.currentIndex--;
          Player.saveState();
        }
      } else {
        alert(data.error || 'Error al eliminar');
      }
    } catch(e) {
      alert('Error de conexión');
    }
  }
};

// =============================================
// MODAL (Add Song)
// =============================================
const Modal = {
  init() {
    document.getElementById('modal-backdrop').addEventListener('click', () => this.close());
    document.getElementById('modal-close-btn').addEventListener('click', () => this.close());
    document.getElementById('btn-add-song').addEventListener('click', () => this.submitSong());

    document.getElementById('yt-url').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this.submitSong();
    });
  },

  open() {
    document.getElementById('modal-add-song').classList.remove('hidden');
    document.getElementById('yt-url').value = '';
    document.getElementById('add-song-status').className = 'hidden';
    document.getElementById('yt-url').focus();
  },

  close() {
    document.getElementById('modal-add-song').classList.add('hidden');
  },

  setStatus(type, msg) {
    const el = document.getElementById('add-song-status');
    el.className = 'status-' + type;
    el.innerHTML = type === 'loading'
      ? `<div class="spinner" style="width:20px;height:20px;border-width:2px;margin:0"></div> ${msg}`
      : msg;
  },

  async submitSong() {
    const urlInput = document.getElementById('yt-url');
    const url = urlInput.value.trim();

    if (!url) {
      this.setStatus('error', 'Introduce un enlace de YouTube.');
      return;
    }

    const btn = document.getElementById('btn-add-song');
    btn.disabled = true;
    this.setStatus('loading', 'Descargando audio... puede tardar hasta un minuto.');

    try {
      const res = await fetch('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, csrf_token: APP.csrf })
      });

      const data = await res.json();

      if (data.success) {
        this.setStatus('success', `✓ "${data.song.title}" añadida a tu biblioteca.`);
        urlInput.value = '';

        // Reload current page to show new song
        setTimeout(() => {
          this.close();
          const page = Router.currentPage || 'library';
          Router.currentPage = null; // Force reload
          Router.navigate(page);
        }, 1500);
      } else {
        this.setStatus('error', data.error || 'Error desconocido.');
      }
    } catch(e) {
      this.setStatus('error', 'Error de conexión. Comprueba el servidor.');
    } finally {
      btn.disabled = false;
    }
  }
};

// =============================================
// INIT
// =============================================
document.addEventListener('DOMContentLoaded', () => {
  // Player.init() lo dispara player.js (cargado globalmente desde _base.html).
  if (typeof Modal !== 'undefined') Modal.init();

  // Navigation click handlers
  document.querySelectorAll('[data-page]').forEach(el => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      Router.navigate(el.dataset.page);
    });
  });

  // Load initial page
  Router.navigate('library');

  // (Service Worker + Escape→closeFullscreen ahora globales en _base.html / player.js)
});
