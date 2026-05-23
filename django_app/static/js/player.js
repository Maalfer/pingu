/* =============================================
   BaluHome — Persistent music player
   Loaded on every authenticated page so the song
   keeps playing while you navigate between apps.
   ============================================= */

'use strict';

function _balu_formatTime(secs) {
  const s = Math.floor(secs || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`
    : `${m}:${String(sec).padStart(2,'0')}`;
}
window.formatTime = _balu_formatTime;

const Player = {
  audio: null,
  queue: [],
  currentIndex: -1,
  isSeeking: false,
  _pendingSeek: null,
  _initialized: false,

  init() {
    if (this._initialized) return;
    this.audio = document.getElementById('audio-player');
    if (!this.audio) return;             // página sin reproductor (página de login etc.)
    this._initialized = true;
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
      // Solo persistimos el tiempo en cada timeupdate (≈4/s). Lo hacemos a un
      // ritmo de ~1/s para no hacer 16 escrituras de localStorage por segundo.
      const now = Date.now();
      if (!this._lastTimeSave || now - this._lastTimeSave > 900) {
        this._lastTimeSave = now;
        try { localStorage.setItem('bls_time', a.currentTime || 0); } catch(_){}
      }
    });
    a.addEventListener('play',  () => { this.setPlayIcon(true);  try { localStorage.setItem('bls_paused','0'); } catch(_){} });
    a.addEventListener('pause', () => { this.setPlayIcon(false); try { localStorage.setItem('bls_paused','1'); } catch(_){} });
    a.addEventListener('ended', () => this.playNext());
    a.addEventListener('loadedmetadata', () => {
      // Aplica un seek pendiente cuando ya conocemos la duración real.
      if (this._pendingSeek != null && isFinite(this._pendingSeek)) {
        try { a.currentTime = Math.min(this._pendingSeek, (a.duration || this._pendingSeek)); } catch(_){}
        this._pendingSeek = null;
      }
      const pct = a.duration ? (a.currentTime / a.duration) * 100 : 0;
      this.updateSeekUI(pct, a.currentTime, a.duration);
    });
    a.addEventListener('error', e => console.warn('Audio error', e));
  },

  bindControls() {
    const on = (id, ev, fn) => { const el = document.getElementById(id); if (el) el.addEventListener(ev, fn); };
    on('btn-play-pause',  'click', () => this.togglePlay());
    on('btn-prev',        'click', () => this.playPrev());
    on('btn-next',        'click', () => this.playNext());
    on('player-expand-btn','click', () => this.openFullscreen());
    on('pf-play-pause',   'click', () => this.togglePlay());
    on('pf-prev',         'click', () => this.playPrev());
    on('pf-next',         'click', () => this.playNext());
    on('pf-close',        'click', () => this.closeFullscreen());

    const seekBar = document.getElementById('pf-seek');
    if (seekBar) {
      seekBar.addEventListener('input', () => {
        this.isSeeking = true;
        const pct = parseFloat(seekBar.value);
        seekBar.style.setProperty('--fill', pct + '%');
        if (this.audio.duration) {
          document.getElementById('pf-current').textContent = _balu_formatTime(this.audio.duration * pct / 100);
        }
      });
      seekBar.addEventListener('change', () => {
        if (this.audio.duration) {
          this.audio.currentTime = this.audio.duration * parseFloat(seekBar.value) / 100;
        }
        this.isSeeking = false;
      });
    }

    // Cerrar el player a pantalla completa con Escape.
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        const fs = document.getElementById('player-fullscreen');
        if (fs && !fs.classList.contains('hidden')) this.closeFullscreen();
      }
    });
  },

  playSong(song, queue, index) {
    this.queue = queue || [];
    this.currentIndex = (index != null) ? index : -1;
    this._loadAndPlay(song);
  },

  _loadAndPlay(song) {
    if (!song) return;
    this.audio.src = `/api/stream/${song.id}`;
    this.audio.load();
    this._pendingSeek = null;
    this.audio.play().catch(() => {});
    this.updateUI(song);
    this.setupMediaSession(song);
    this.saveState();
    this.highlightCurrent();
  },

  updateUI(song) {
    const bar = document.getElementById('player-bar');
    if (bar) bar.classList.remove('hidden');
    document.body.classList.add('has-player');
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.src !== undefined ? (el.src = val) : (el.textContent = val); };
    const setSrc  = (id, v) => { const el = document.getElementById(id); if (el && 'src' in el) el.src = v || ''; };
    const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v || ''; };
    setSrc('player-thumb',  song.thumbnail || '');
    setText('player-title', song.title || '');
    setText('player-artist',song.artist || '');
    setSrc('pf-thumb',      song.thumbnail || '');
    setText('pf-title',     song.title || '');
    setText('pf-artist',    song.artist || '');
  },

  togglePlay() {
    if (!this.audio || !this.audio.src) return;
    if (this.audio.paused) this.audio.play().catch(() => {});
    else this.audio.pause();
  },
  playNext() {
    if (!this.queue.length) return;
    this.currentIndex = (this.currentIndex + 1) % this.queue.length;
    this._loadAndPlay(this.queue[this.currentIndex]);
  },
  playPrev() {
    if (!this.queue.length) return;
    if (this.audio.currentTime > 3) { this.audio.currentTime = 0; return; }
    this.currentIndex = (this.currentIndex - 1 + this.queue.length) % this.queue.length;
    this._loadAndPlay(this.queue[this.currentIndex]);
  },

  setPlayIcon(playing) {
    ['icon-play','pf-icon-play'].forEach(id => {
      const el = document.getElementById(id); if (el) el.style.display = playing ? 'none' : '';
    });
    ['icon-pause','pf-icon-pause'].forEach(id => {
      const el = document.getElementById(id); if (el) el.style.display = playing ? '' : 'none';
    });
  },

  updateSeekUI(pct, cur, dur) {
    const seek = document.getElementById('pf-seek');
    if (seek && !this.isSeeking) {
      seek.value = pct;
      seek.style.setProperty('--fill', pct + '%');
    }
    const c = document.getElementById('pf-current'); if (c) c.textContent = _balu_formatTime(cur || 0);
    const d = document.getElementById('pf-duration'); if (d) d.textContent = _balu_formatTime(dur || 0);
  },

  openFullscreen()  { const fs = document.getElementById('player-fullscreen'); if (fs) { fs.classList.remove('hidden'); document.body.style.overflow = 'hidden'; } },
  closeFullscreen() { const fs = document.getElementById('player-fullscreen'); if (fs) { fs.classList.add('hidden');    document.body.style.overflow = ''; } },

  highlightCurrent() {
    document.querySelectorAll('.song-item').forEach(el => el.classList.remove('playing'));
    if (this.currentIndex < 0 || !this.queue.length) return;
    const cur = this.queue[this.currentIndex];
    if (!cur) return;
    const el = document.querySelector(`.song-item[data-song-id="${cur.id}"]`);
    if (el) el.classList.add('playing');
  },

  setupMediaSession(song) {
    if (!('mediaSession' in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title:  song.title  || 'Unknown',
      artist: song.artist || 'Unknown',
      artwork: song.thumbnail ? [{ src: song.thumbnail, sizes: '480x360', type: 'image/jpeg' }] : []
    });
    navigator.mediaSession.setActionHandler('play',          () => this.audio.play().catch(()=>{}));
    navigator.mediaSession.setActionHandler('pause',         () => this.audio.pause());
    navigator.mediaSession.setActionHandler('nexttrack',     () => this.playNext());
    navigator.mediaSession.setActionHandler('previoustrack', () => this.playPrev());
    navigator.mediaSession.setActionHandler('seekto', d => { if (d.seekTime != null) this.audio.currentTime = d.seekTime; });
  },

  saveState() {
    if (this.currentIndex < 0 || !this.queue.length) return;
    try {
      localStorage.setItem('bls_song',  JSON.stringify(this.queue[this.currentIndex]));
      localStorage.setItem('bls_queue', JSON.stringify(this.queue));
      localStorage.setItem('bls_index', this.currentIndex);
      localStorage.setItem('bls_time',  this.audio.currentTime || 0);
      // bls_paused se actualiza en los listeners 'play' / 'pause' para no escribirlo en cada timeupdate.
    } catch(_) {}
  },

  restoreState() {
    try {
      const songRaw  = localStorage.getItem('bls_song');
      const queueRaw = localStorage.getItem('bls_queue');
      if (!songRaw || !queueRaw) return;
      const song  = JSON.parse(songRaw);
      const queue = JSON.parse(queueRaw);
      const index = parseInt(localStorage.getItem('bls_index') || '0');
      const time  = parseFloat(localStorage.getItem('bls_time') || '0');
      const wasPlaying = localStorage.getItem('bls_paused') === '0';
      if (!song || !queue || !queue.length) return;

      this.queue = queue;
      this.currentIndex = Math.max(0, Math.min(index, queue.length - 1));
      this.audio.src = `/api/stream/${song.id}`;
      this.audio.load();
      this._pendingSeek = time;             // se aplica en loadedmetadata
      this.updateUI(song);
      this.setupMediaSession(song);
      this.setPlayIcon(!!wasPlaying);
      if (wasPlaying) {
        // Intenta reanudar; los navegadores permiten autoplay si el sitio tiene
        // engagement-score alto o si venimos de un click de navegación.
        this.audio.play().catch(() => this.setPlayIcon(false));
      }
    } catch(_) {}
  }
};

window.Player = Player;
document.addEventListener('DOMContentLoaded', () => Player.init());
