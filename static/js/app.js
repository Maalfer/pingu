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
    if (page === 'library' || page === 'admin') {
      Library.init();
    }
    if (page === 'dashboard') {
      Dashboard.init();
    }
    if (page === 'admin') {
      Admin.init();
    }
    // Restore playing state highlight
    Player.highlightCurrent();
  }
};

// =============================================
// PLAYER
// =============================================
const Player = {
  audio: null,
  queue: [],
  currentIndex: -1,
  isSeeking: false,
  mediaSessionActive: false,

  init() {
    this.audio = document.getElementById('audio-player');
    this.bindAudioEvents();
    this.bindControls();
    this.restoreState();
  },

  bindAudioEvents() {
    const a = this.audio;

    a.addEventListener('timeupdate', () => {
      if (this.isSeeking) return;
      const pct = a.duration ? (a.currentTime / a.duration) * 100 : 0;
      this.updateSeekUI(pct, a.currentTime, a.duration);
      this.saveState();
    });

    a.addEventListener('play', () => this.setPlayIcon(true));
    a.addEventListener('pause', () => this.setPlayIcon(false));
    a.addEventListener('ended', () => this.playNext());
    a.addEventListener('loadedmetadata', () => {
      const pct = a.duration ? (a.currentTime / a.duration) * 100 : 0;
      this.updateSeekUI(pct, a.currentTime, a.duration);
    });

    a.addEventListener('error', (e) => {
      console.warn('Audio error', e);
    });
  },

  bindControls() {
    // Mini player
    document.getElementById('btn-play-pause').addEventListener('click', () => this.togglePlay());
    document.getElementById('btn-prev').addEventListener('click', () => this.playPrev());
    document.getElementById('btn-next').addEventListener('click', () => this.playNext());
    document.getElementById('player-expand-btn').addEventListener('click', () => this.openFullscreen());

    // Full-screen player
    document.getElementById('pf-play-pause').addEventListener('click', () => this.togglePlay());
    document.getElementById('pf-prev').addEventListener('click', () => this.playPrev());
    document.getElementById('pf-next').addEventListener('click', () => this.playNext());
    document.getElementById('pf-close').addEventListener('click', () => this.closeFullscreen());

    const seekBar = document.getElementById('pf-seek');
    seekBar.addEventListener('input', () => {
      this.isSeeking = true;
      const pct = parseFloat(seekBar.value);
      seekBar.style.setProperty('--fill', pct + '%');
      if (this.audio.duration) {
        document.getElementById('pf-current').textContent = formatTime(this.audio.duration * pct / 100);
      }
    });
    seekBar.addEventListener('change', () => {
      if (this.audio.duration) {
        this.audio.currentTime = this.audio.duration * parseFloat(seekBar.value) / 100;
      }
      this.isSeeking = false;
    });
  },

  playSong(song, queue, index) {
    this.queue = queue;
    this.currentIndex = index;
    this._loadAndPlay(song);
  },

  _loadAndPlay(song) {
    const streamUrl = `/api/stream/${song.id}`;
    this.audio.src = streamUrl;
    this.audio.load();
    this.audio.play().catch(e => console.warn('Autoplay blocked:', e));

    this.updateUI(song);
    this.setupMediaSession(song);
    this.saveState();
    this.highlightCurrent();
  },

  updateUI(song) {
    const bar = document.getElementById('player-bar');
    bar.classList.remove('hidden');

    const thumb = song.thumbnail || '';
    document.getElementById('player-thumb').src = thumb;
    document.getElementById('player-title').textContent = song.title;
    document.getElementById('player-artist').textContent = song.artist || '';

    document.getElementById('pf-thumb').src = thumb;
    document.getElementById('pf-title').textContent = song.title;
    document.getElementById('pf-artist').textContent = song.artist || '';
  },

  togglePlay() {
    if (!this.audio.src) return;
    if (this.audio.paused) {
      this.audio.play().catch(() => {});
    } else {
      this.audio.pause();
    }
  },

  playNext() {
    if (!this.queue.length) return;
    this.currentIndex = (this.currentIndex + 1) % this.queue.length;
    this._loadAndPlay(this.queue[this.currentIndex]);
  },

  playPrev() {
    if (!this.queue.length) return;
    if (this.audio.currentTime > 3) {
      this.audio.currentTime = 0;
      return;
    }
    this.currentIndex = (this.currentIndex - 1 + this.queue.length) % this.queue.length;
    this._loadAndPlay(this.queue[this.currentIndex]);
  },

  setPlayIcon(playing) {
    ['icon-play','pf-icon-play'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = playing ? 'none' : '';
    });
    ['icon-pause','pf-icon-pause'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = playing ? '' : 'none';
    });
  },

  updateSeekUI(pct, current, duration) {
    const seek = document.getElementById('pf-seek');
    if (!this.isSeeking) {
      seek.value = pct;
      seek.style.setProperty('--fill', pct + '%');
    }
    document.getElementById('pf-current').textContent = formatTime(current || 0);
    document.getElementById('pf-duration').textContent = formatTime(duration || 0);
  },

  openFullscreen() {
    document.getElementById('player-fullscreen').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  },

  closeFullscreen() {
    document.getElementById('player-fullscreen').classList.add('hidden');
    document.body.style.overflow = '';
  },

  highlightCurrent() {
    document.querySelectorAll('.song-item').forEach(el => el.classList.remove('playing'));
    if (this.currentIndex < 0 || !this.queue.length) return;
    const current = this.queue[this.currentIndex];
    if (!current) return;
    const el = document.querySelector(`.song-item[data-song-id="${current.id}"]`);
    if (el) el.classList.add('playing');
  },

  setupMediaSession(song) {
    if (!('mediaSession' in navigator)) return;

    navigator.mediaSession.metadata = new MediaMetadata({
      title:  song.title  || 'Unknown',
      artist: song.artist || 'Unknown',
      artwork: song.thumbnail ? [
        { src: song.thumbnail, sizes: '480x360', type: 'image/jpeg' }
      ] : []
    });

    navigator.mediaSession.setActionHandler('play',           () => { this.audio.play(); });
    navigator.mediaSession.setActionHandler('pause',          () => { this.audio.pause(); });
    navigator.mediaSession.setActionHandler('nexttrack',      () => { this.playNext(); });
    navigator.mediaSession.setActionHandler('previoustrack',  () => { this.playPrev(); });
    navigator.mediaSession.setActionHandler('seekto', (d) => {
      if (d.seekTime !== undefined) this.audio.currentTime = d.seekTime;
    });
  },

  saveState() {
    if (this.currentIndex < 0 || !this.queue.length) return;
    try {
      localStorage.setItem('bls_song', JSON.stringify(this.queue[this.currentIndex]));
      localStorage.setItem('bls_queue', JSON.stringify(this.queue));
      localStorage.setItem('bls_index', this.currentIndex);
      localStorage.setItem('bls_time', this.audio.currentTime || 0);
    } catch(e) {}
  },

  restoreState() {
    try {
      const song  = JSON.parse(localStorage.getItem('bls_song'));
      const queue = JSON.parse(localStorage.getItem('bls_queue'));
      const index = parseInt(localStorage.getItem('bls_index') || '0');
      const time  = parseFloat(localStorage.getItem('bls_time') || '0');

      if (!song || !queue) return;

      this.queue = queue;
      this.currentIndex = index;

      const streamUrl = `/api/stream/${song.id}`;
      this.audio.src = streamUrl;
      this.audio.load();
      this.audio.currentTime = time;

      this.updateUI(song);
      this.setupMediaSession(song);
      this.setPlayIcon(false); // Paused on restore
    } catch(e) {}
  }
};

// =============================================
// LIBRARY
// =============================================
const Library = {
  init() {
    this.bindSearch();
    this.bindSongList();
    this.bindContextMenu();

    const addBtn = document.getElementById('open-add-modal');
    if (addBtn) addBtn.addEventListener('click', () => Modal.open());
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
// DASHBOARD
// =============================================
const Dashboard = {
  init() {
    const formUser = document.getElementById('form-change-username');
    const formPass = document.getElementById('form-change-password');

    if (formUser) {
      formUser.addEventListener('submit', async (e) => {
        e.preventDefault();
        await this.submitForm(formUser, '/api/update_user', 'msg-username');
      });
    }

    if (formPass) {
      formPass.addEventListener('submit', async (e) => {
        e.preventDefault();
        await this.submitForm(formPass, '/api/update_user', 'msg-password');
      });
    }
  },

  async submitForm(form, endpoint, msgId) {
    const formData = Object.fromEntries(new FormData(form));
    const msgEl = document.getElementById(msgId);
    const btn = form.querySelector('button[type=submit]');

    btn.disabled = true;
    msgEl.className = 'form-msg';
    msgEl.textContent = '';

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...formData, csrf_token: APP.csrf })
      });
      const data = await res.json();

      if (data.success) {
        msgEl.className = 'form-msg show ok';
        msgEl.textContent = '¡Actualizado correctamente!';
        if (data.new_username) {
          APP.username = data.new_username;
          // Update sidebar
          document.querySelectorAll('.sidebar-user span').forEach(el => el.textContent = data.new_username);
          document.querySelectorAll('.sidebar-user-avatar, .profile-avatar').forEach(el => {
            el.textContent = data.new_username[0].toUpperCase();
          });
        }
        form.reset();
      } else {
        msgEl.className = 'form-msg show err';
        msgEl.textContent = data.error || 'Error al actualizar.';
      }
    } catch(e) {
      msgEl.className = 'form-msg show err';
      msgEl.textContent = 'Error de conexión.';
    } finally {
      btn.disabled = false;
    }
  }
};

// =============================================
// ADMIN
// =============================================
const Admin = {
  init() {
    const genBtn = document.getElementById('btn-gen-invite');
    if (!genBtn) return;

    genBtn.addEventListener('click', async () => {
      genBtn.disabled = true;
      try {
        const res = await fetch('/api/invite', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ csrf_token: APP.csrf })
        });
        const data = await res.json();

        if (data.success) {
          const result = document.getElementById('invite-result');
          const input  = document.getElementById('invite-link-input');
          result.classList.remove('hidden');
          input.value = data.link;
        } else {
          alert(data.error || 'Error generando invite');
        }
      } catch(e) {
        alert('Error de conexión');
      } finally {
        genBtn.disabled = false;
      }
    });

    const copyBtn = document.getElementById('btn-copy-invite');
    if (copyBtn) {
      copyBtn.addEventListener('click', async () => {
        const input = document.getElementById('invite-link-input');
        try {
          await navigator.clipboard.writeText(input.value);
          copyBtn.style.background = '#17a041';
          setTimeout(() => copyBtn.style.background = '', 1500);
        } catch(e) {
          input.select();
          document.execCommand('copy');
        }
      });
    }
  }
};

// =============================================
// UTILS
// =============================================
function formatTime(secs) {
  const s = Math.floor(secs);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`
    : `${m}:${String(sec).padStart(2,'0')}`;
}

// =============================================
// INIT
// =============================================
document.addEventListener('DOMContentLoaded', () => {
  Player.init();
  Modal.init();

  // Navigation click handlers
  document.querySelectorAll('[data-page]').forEach(el => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      Router.navigate(el.dataset.page);
    });
  });

  // Load initial page
  Router.navigate('library');

  // Register Service Worker
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // Handle back on full-screen player (Android back button)
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const fs = document.getElementById('player-fullscreen');
      if (!fs.classList.contains('hidden')) Player.closeFullscreen();
    }
  });
});
