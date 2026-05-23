/* BHVideoPlayer: reproductor de video reutilizable.

   Uso:
     BHVideoPlayer.open({ src: '/api/files/view/42', title: 'mi.mp4' });
     BHVideoPlayer.close();
*/
(function () {
  'use strict';

  function fmt(s) {
    s = Math.floor(s || 0);
    var h = Math.floor(s / 3600),
        m = Math.floor((s % 3600) / 60),
        sec = s % 60;
    if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
    return m + ':' + String(sec).padStart(2, '0');
  }

  var state = {
    ready: false,
    overlay: null, wrap: null, video: null, controls: null,
    title: null, seek: null, cur: null, total: null,
    btnPlay: null, iconPlay: null, iconPause: null,
    btnVol: null, vol: null, iconVolOn: null, iconVolOff: null,
    btnFs: null, iconFsEnter: null, iconFsExit: null,
    btnClose: null, btnCast: null,
    hideTimer: null,
    onKey: null,
    onFsChange: null,
    open: false,
  };

  function $(id) { return document.getElementById(id); }

  function showControls() {
    state.controls.classList.remove('bh-player-hide');
    scheduleHide();
  }
  function scheduleHide() {
    clearTimeout(state.hideTimer);
    if (!state.video.paused) {
      state.hideTimer = setTimeout(function () {
        state.controls.classList.add('bh-player-hide');
      }, 3500);
    }
  }
  function updateVolIcon() {
    var muted = state.video.muted || state.video.volume === 0;
    state.iconVolOn.style.display = muted ? 'none' : '';
    state.iconVolOff.style.display = muted ? '' : 'none';
  }
  function toggleFullscreen() {
    var el = state.overlay;
    if (!document.fullscreenElement) {
      (el.requestFullscreen || el.webkitRequestFullscreen || function () {}).call(el);
    } else {
      (document.exitFullscreen || document.webkitExitFullscreen || function () {}).call(document);
    }
  }

  function bind() {
    if (state.ready) return true;
    var o = $('bhvp-overlay');
    if (!o) return false;
    state.overlay   = o;
    state.wrap      = $('bhvp-wrap');
    state.video     = $('bhvp-video');
    state.controls  = $('bhvp-controls');
    state.title     = $('bhvp-title');
    state.seek      = $('bhvp-seek');
    state.cur       = $('bhvp-current');
    state.total     = $('bhvp-total');
    state.btnPlay   = $('bhvp-play');
    state.iconPlay  = $('bhvp-icon-play');
    state.iconPause = $('bhvp-icon-pause');
    state.btnVol    = $('bhvp-vol-btn');
    state.vol       = $('bhvp-vol');
    state.iconVolOn = $('bhvp-vol-on');
    state.iconVolOff= $('bhvp-vol-off');
    state.btnFs     = $('bhvp-fs');
    state.iconFsEnter = $('bhvp-icon-fs-enter');
    state.iconFsExit  = $('bhvp-icon-fs-exit');
    state.btnClose  = $('bhvp-close');
    state.btnCast   = $('bhvp-cast');

    state.btnClose.addEventListener('click', api.close);
    state.btnPlay.addEventListener('click', function () {
      if (state.video.paused) state.video.play(); else state.video.pause();
      showControls();
    });
    state.video.addEventListener('play',  function () { state.iconPlay.style.display = 'none'; state.iconPause.style.display = ''; scheduleHide(); });
    state.video.addEventListener('pause', function () { state.iconPlay.style.display = '';     state.iconPause.style.display = 'none'; showControls(); });
    state.video.addEventListener('ended', function () { state.iconPlay.style.display = '';     state.iconPause.style.display = 'none'; showControls(); });
    state.video.addEventListener('timeupdate', function () {
      if (!state.video.duration || isNaN(state.video.duration)) return;
      state.seek.value = Math.floor((state.video.currentTime / state.video.duration) * 1000);
      state.cur.textContent = fmt(state.video.currentTime);
    });
    state.video.addEventListener('loadedmetadata', function () {
      state.total.textContent = fmt(state.video.duration);
      state.cur.textContent = '0:00';
    });
    state.video.addEventListener('error', function () {
      state.title.textContent = 'No se puede reproducir este archivo';
    });

    state.seek.addEventListener('input', function () {
      if (!state.video.duration) return;
      state.video.currentTime = (state.seek.value / 1000) * state.video.duration;
      showControls();
    });
    state.vol.addEventListener('input', function () {
      state.video.volume = parseFloat(state.vol.value);
      state.video.muted = (state.video.volume === 0);
      updateVolIcon();
    });
    state.btnVol.addEventListener('click', function () {
      state.video.muted = !state.video.muted;
      state.vol.value = state.video.muted ? 0 : (state.video.volume || 1);
      updateVolIcon();
    });
    state.btnFs.addEventListener('click', toggleFullscreen);
    state.btnCast.addEventListener('click', function () {
      if ('remote' in HTMLMediaElement.prototype) {
        state.video.remote.prompt().catch(function (e) { if (e.name !== 'AbortError') showHint(); });
        return;
      }
      if (window.PresentationRequest && state.video.src) {
        try { new PresentationRequest([state.video.src]).start().catch(function () {}); return; } catch (e) {}
      }
      showHint();
    });

    state.onFsChange = function () {
      var fs = !!document.fullscreenElement;
      state.iconFsEnter.style.display = fs ? 'none' : '';
      state.iconFsExit.style.display = fs ? '' : 'none';
    };
    document.addEventListener('fullscreenchange', state.onFsChange);

    state.wrap.addEventListener('click', function (e) {
      if (e.target.closest('button') || e.target.closest('input[type=range]')) return;
      if (state.controls.classList.contains('bh-player-hide')) showControls();
      else if (!state.video.paused) {
        state.controls.classList.add('bh-player-hide');
        clearTimeout(state.hideTimer);
      }
    });

    state.ready = true;
    return true;
  }

  function showHint() {
    state.title.textContent = 'Usa el botón de emitir de tu navegador';
    setTimeout(function () { if (state.video) state.title.textContent = state.video._originalTitle || ''; }, 2500);
  }

  var api = {
    open: function (opts) {
      if (!bind()) { console.warn('[BHVideoPlayer] partial markup missing'); return; }
      opts = opts || {};
      state.video._originalTitle = opts.title || '';
      state.title.textContent = opts.title || '';
      state.video.src = opts.src || '';
      state.overlay.classList.remove('hidden');
      state.overlay.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      state.open = true;
      // Pausar el audio de música mientras se ve un video (mismo dispositivo).
      var music = document.getElementById('audio-player');
      if (music && !music.paused) { state._musicWasPlaying = true; music.pause(); }
      else { state._musicWasPlaying = false; }
      state.video.play().catch(function () {});
      showControls();
      if (!state.onKey) {
        state.onKey = function (e) {
          if (!state.open) return;
          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
          if (e.key === 'Escape') { api.close(); return; }
          if (e.key === ' ' || e.key === 'k') {
            e.preventDefault();
            if (state.video.paused) state.video.play(); else state.video.pause();
          }
          if (e.key === 'ArrowRight') { state.video.currentTime = Math.min(state.video.duration || 0, state.video.currentTime + 10); showControls(); }
          if (e.key === 'ArrowLeft')  { state.video.currentTime = Math.max(0, state.video.currentTime - 10); showControls(); }
          if (e.key === 'f') toggleFullscreen();
          if (e.key === 'm') { state.video.muted = !state.video.muted; updateVolIcon(); }
        };
        document.addEventListener('keydown', state.onKey);
      }
    },
    close: function () {
      if (!state.ready || !state.open) return;
      state.video.pause();
      state.video.removeAttribute('src');
      state.video.load();
      state.overlay.classList.add('hidden');
      state.overlay.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
      clearTimeout(state.hideTimer);
      state.open = false;
      // Si la música estaba sonando antes de abrir el video, la retomamos.
      if (state._musicWasPlaying) {
        var music = document.getElementById('audio-player');
        if (music) music.play().catch(function () {});
      }
    },
    isOpen: function () { return !!state.open; },
  };

  window.BHVideoPlayer = api;
})();
