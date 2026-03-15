async function callApi(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    alert(data.error || 'Action impossible');
    return null;
  }

  return response.json();
}

async function fetchState() {
  const response = await fetch('/api/state');
  if (!response.ok) return null;
  return response.json();
}

const _systemQuery = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)');

function _isManualOverride() {
  const v = sessionStorage.getItem('dashboard-theme');
  return v === 'light' || v === 'dark';
}

function applyTheme(theme, synced) {
  const body = document.body;
  const toggle = document.getElementById('themeToggle');
  body.dataset.theme = theme;
  if (toggle) {
    const isLight = theme === 'light';
    if (synced) {
      toggle.textContent = isLight ? '☀️ Système' : '🌙 Système';
      toggle.setAttribute('aria-label', 'Thème synchronisé avec le système — cliquez pour choisir manuellement');
      toggle.classList.add('synced');
    } else {
      toggle.textContent = isLight ? '☀️ Jour' : '🌙 Nuit';
      toggle.setAttribute('aria-label', isLight ? 'Activer le thème nuit' : 'Activer le thème jour');
      toggle.classList.remove('synced');
    }
  }
}

function initTheme() {
  if (_isManualOverride()) {
    applyTheme(sessionStorage.getItem('dashboard-theme'), false);
  } else {
    const preferLight = _systemQuery && _systemQuery.matches;
    applyTheme(preferLight ? 'light' : 'dark', true);
  }

  // Écoute les changements du thème système en temps réel
  if (_systemQuery && _systemQuery.addEventListener) {
    _systemQuery.addEventListener('change', (e) => {
      if (!_isManualOverride()) {
        applyTheme(e.matches ? 'light' : 'dark', true);
      }
    });
  }
}

function render(state) {
  if (!state) return;

  const services = state.services || {};
  const librespotifyOnline = Boolean(services.spotify?.online);
  const airplayOnline = Boolean(services.airplay?.online);

  document.getElementById('deviceLine').textContent = `${state.device_name} · ${state.room}`;
  document.getElementById('trackName').textContent = state.current_track;
  document.getElementById('artistName').textContent = state.current_artist;
  document.getElementById('volumeValue').textContent = state.volume;
  document.getElementById('volumeSlider').value = state.volume;
  document.getElementById('battery').textContent = `${state.battery}%`;
  const wifiStrength = Number(state.wifi_strength) || 0;
  const wifiIndicator = document.getElementById('wifi');
  wifiIndicator.setAttribute('aria-label', `Signal Wi-Fi ${wifiStrength} sur 5`);
  wifiIndicator.querySelectorAll('.wifi-bar').forEach((bar, index) => {
    bar.classList.toggle('active', index < wifiStrength);
  });
  document.getElementById('firmware').textContent = state.firmware;
  document.getElementById('updatedAt').textContent = state.updated_since || state.updated_at;

  const librespotifyStatus = document.getElementById('status-spotify');
  const airplayStatus = document.getElementById('status-airplay');
  const librespotifyChip = document.getElementById('chip-spotify');
  const airplayChip = document.getElementById('chip-airplay');
  const activeService = document.getElementById('activeService');
  const playbackSource = document.getElementById('playbackSource');

  if (librespotifyStatus) librespotifyStatus.textContent = librespotifyOnline ? 'En ligne' : 'Hors ligne';
  if (airplayStatus) airplayStatus.textContent = airplayOnline ? 'En ligne' : 'Hors ligne';

  if (librespotifyChip) {
    librespotifyChip.classList.toggle('online', librespotifyOnline);
    librespotifyChip.classList.toggle('offline', !librespotifyOnline);
    librespotifyChip.classList.toggle('active', state.active_service === 'spotify');
  }

  if (airplayChip) {
    airplayChip.classList.toggle('online', airplayOnline);
    airplayChip.classList.toggle('offline', !airplayOnline);
    airplayChip.classList.toggle('active', state.active_service === 'airplay');
  }

  if (activeService) {
    activeService.textContent =
      state.active_service_name || services[state.active_service]?.name || 'Aucune';
  }

  if (playbackSource) {
    playbackSource.textContent =
      state.active_service_name || services[state.active_service]?.name || 'Aucune';
  }

  const toggleLibrespotify = document.getElementById('toggleSpotify');
  const toggleAirplay = document.getElementById('toggleAirplay');
  if (toggleLibrespotify) {
    toggleLibrespotify.textContent = librespotifyOnline
      ? 'Couper Spotify'
      : 'Activer Spotify';
  }
  if (toggleAirplay) {
    toggleAirplay.textContent = airplayOnline
      ? 'Couper AirPlay'
      : 'Activer AirPlay';
  }

  const hasPlayableSource = Boolean(state.active_service) && Boolean(services[state.active_service]?.online);
  document.querySelectorAll('[data-playback]').forEach((button) => {
    button.disabled = !hasPlayableSource || !state.power;
  });

  const powerBtn = document.getElementById('powerBtn');
  powerBtn.textContent = state.power ? 'Éteindre' : 'Allumer';
  powerBtn.classList.toggle('on', state.power);
  powerBtn.classList.toggle('off', !state.power);

  const playBtn = document.getElementById('playBtn');
  playBtn.textContent = state.is_playing ? '⏸' : '▶';
  playBtn.setAttribute('aria-label', state.is_playing ? 'Pause' : 'Lecture');
  playBtn.setAttribute('title', state.is_playing ? 'Pause' : 'Lecture');
  document.getElementById('muteBtn').textContent = state.muted ? 'Activer le son' : 'Muet';

  const eqPreset = document.getElementById('eqPreset');
  if (state.eq_preset && eqPreset) {
    eqPreset.value = state.eq_preset;
  }

  const eqBands = state.eq_bands || {};
  const eqIds = {
    '60Hz': 'eq60',
    '230Hz': 'eq230',
    '910Hz': 'eq910',
    '3.6kHz': 'eq3600',
    '14kHz': 'eq14000'
  };

  Object.entries(eqIds).forEach(([band, id]) => {
    const slider = document.getElementById(id);
    const value = eqBands[band] ?? 0;
    if (slider) slider.value = value;
    const label = document.getElementById(`${id}Value`);
    if (label) label.textContent = `${value} dB`;
  });
}

document.getElementById('powerBtn').addEventListener('click', async () => {
  const state = await callApi('/api/power', { action: 'toggle' });
  render(state);
});

document.querySelectorAll('[data-playback]').forEach((button) => {
  button.addEventListener('click', async () => {
    const state = await callApi('/api/playback', { action: button.dataset.playback });
    render(state);
  });
});

document.getElementById('volDown').addEventListener('click', async () => {
  const state = await callApi('/api/volume', { delta: -5 });
  render(state);
});

document.getElementById('volUp').addEventListener('click', async () => {
  const state = await callApi('/api/volume', { delta: 5 });
  render(state);
});

document.getElementById('volumeSlider').addEventListener('change', async (event) => {
  const state = await callApi('/api/volume', { volume: Number(event.target.value) });
  render(state);
});

document.getElementById('muteBtn').addEventListener('click', async () => {
  const currentText = document.getElementById('muteBtn').textContent;
  const mute = currentText === 'Muet';
  const state = await callApi('/api/volume', { mute });
  render(state);
});

document.getElementById('eqPreset').addEventListener('change', async (event) => {
  if (event.target.value === 'custom') return;
  const state = await callApi('/api/eq', { preset: event.target.value });
  render(state);
});

document.querySelectorAll('.eq-slider').forEach((slider) => {
  slider.addEventListener('input', () => {
    const label = document.getElementById(`${slider.id}Value`);
    if (label) label.textContent = `${slider.value} dB`;
  });

  slider.addEventListener('change', async () => {
    const state = await callApi('/api/eq', {
      band: slider.dataset.band,
      gain: Number(slider.value)
    });
    render(state);
  });
});

document.getElementById('themeToggle').addEventListener('click', () => {
  const nextTheme = document.body.dataset.theme === 'light' ? 'dark' : 'light';
  sessionStorage.setItem('dashboard-theme', nextTheme);
  applyTheme(nextTheme, false);
});

// Double-clic : retour à la synchronisation avec le système
document.getElementById('themeToggle').addEventListener('dblclick', (e) => {
  e.preventDefault();
  sessionStorage.removeItem('dashboard-theme');
  const preferLight = _systemQuery && _systemQuery.matches;
  applyTheme(preferLight ? 'light' : 'dark', true);
});

document.getElementById('chip-spotify').addEventListener('click', async () => {
  const state = await callApi('/api/services', { service: 'spotify', action: 'select' });
  render(state);
});

document.getElementById('chip-airplay').addEventListener('click', async () => {
  const state = await callApi('/api/services', { service: 'airplay', action: 'select' });
  render(state);
});

document.getElementById('toggleSpotify').addEventListener('click', async () => {
  const state = await callApi('/api/services', { service: 'spotify', action: 'toggle' });
  render(state);
});

document.getElementById('toggleAirplay').addEventListener('click', async () => {
  const state = await callApi('/api/services', { service: 'airplay', action: 'toggle' });
  render(state);
});

initTheme();

fetchState().then((state) => {
  if (state) render(state);
});

// ── Spectrum Analyzer (Web Audio API + FFT) ───────────────────────────
(function () {
  const canvas = document.getElementById('spectrumCanvas');
  const toggleBtn = document.getElementById('spectrumToggle');
  const ctx = canvas.getContext('2d');

  // Bandes EQ : fréquence centrale → fréquence du filtre
  const EQ_BANDS = [
    { band: '60Hz',   freq: 60 },
    { band: '230Hz',  freq: 230 },
    { band: '910Hz',  freq: 910 },
    { band: '3.6kHz', freq: 3600 },
    { band: '14kHz',  freq: 14000 },
  ];

  let audioCtx = null;
  let analyser = null;
  let filters = [];
  let sourceNode = null;
  let rafId = null;
  let running = false;

  // Lire les gains EQ actuels depuis les sliders
  function readEqGains() {
    return EQ_BANDS.map(({ band }) => {
      const slider = document.querySelector(`.eq-slider[data-band="${band}"]`);
      return slider ? Number(slider.value) : 0;
    });
  }

  // Mettre à jour les filtres audio quand l'EQ change
  function updateFilters() {
    if (!filters.length) return;
    const gains = readEqGains();
    filters.forEach((f, i) => { f.gain.value = gains[i]; });
  }

  // Initialiser le graphe audio : bruit blanc → filtres EQ → analyser → silencieux
  function initAudio() {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();

    // Générateur de bruit blanc (buffer de 2s en boucle)
    const bufferSize = audioCtx.sampleRate * 2;
    const buffer = audioCtx.createBuffer(1, bufferSize, audioCtx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) data[i] = Math.random() * 2 - 1;

    sourceNode = audioCtx.createBufferSource();
    sourceNode.buffer = buffer;
    sourceNode.loop = true;

    // Créer un filtre peaking par bande EQ
    filters = EQ_BANDS.map(({ freq }, i) => {
      const f = audioCtx.createBiquadFilter();
      f.type = 'peaking';
      f.frequency.value = freq;
      f.Q.value = 1.4;
      f.gain.value = readEqGains()[i];
      return f;
    });

    // Chaîner les filtres
    let prev = sourceNode;
    filters.forEach(f => { prev.connect(f); prev = f; });

    // Analyser (FFT size 2048 → 1024 bins)
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.82;
    prev.connect(analyser);

    // Sortie silencieuse (gain 0)
    const silence = audioCtx.createGain();
    silence.gain.value = 0;
    analyser.connect(silence);
    silence.connect(audioCtx.destination);

    sourceNode.start();
  }

  // ── Rendu canvas ──────────────────────────────────────────────────────
  function getThemeColors() {
    const style = getComputedStyle(document.body);
    return {
      primary:  style.getPropertyValue('--primary').trim()  || '#2563eb',
      muted:    style.getPropertyValue('--muted').trim()    || '#9db0ca',
      panelBg:  style.getPropertyValue('--panel').trim()    || 'rgba(17,24,39,0.82)',
      text:     style.getPropertyValue('--text').trim()     || '#e7edf7',
    };
  }

  function draw() {
    if (!running) return;
    rafId = requestAnimationFrame(draw);

    const W = canvas.width;
    const H = canvas.height;
    const bufLen = analyser.frequencyBinCount;   // 1024
    const freqData = new Uint8Array(bufLen);
    analyser.getByteFrequencyData(freqData);

    const colors = getThemeColors();
    ctx.clearRect(0, 0, W, H);

    // Grille horizontale (dB)
    ctx.strokeStyle = 'rgba(148,163,184,0.12)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = Math.round((H / 4) * i) + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }

    // Convertir l'échelle linéaire de bins → axe logarithmique (20 Hz … 20 kHz)
    const sampleRate = audioCtx.sampleRate;
    const nyquist = sampleRate / 2;
    const logMin = Math.log10(20);
    const logMax = Math.log10(20000);

    function freqToX(freq) {
      return ((Math.log10(freq) - logMin) / (logMax - logMin)) * W;
    }
    function binToFreq(bin) {
      return (bin / bufLen) * nyquist;
    }

    // Construire le chemin du spectre
    const gradient = ctx.createLinearGradient(0, 0, 0, H);
    gradient.addColorStop(0,   colors.primary);
    gradient.addColorStop(0.6, colors.primary + '99');
    gradient.addColorStop(1,   colors.primary + '11');

    ctx.beginPath();
    ctx.moveTo(0, H);

    let started = false;
    for (let bin = 1; bin < bufLen; bin++) {
      const freq = binToFreq(bin);
      if (freq < 20 || freq > 20000) continue;
      const x = freqToX(freq);
      const amplitude = freqData[bin] / 255;
      const y = H - amplitude * H * 0.92;
      if (!started) { ctx.lineTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    }
    ctx.lineTo(W, H);
    ctx.closePath();

    // Remplissage avec dégradé
    ctx.fillStyle = gradient;
    ctx.fill();

    // Trait du dessus
    ctx.beginPath();
    started = false;
    for (let bin = 1; bin < bufLen; bin++) {
      const freq = binToFreq(bin);
      if (freq < 20 || freq > 20000) continue;
      const x = freqToX(freq);
      const amplitude = freqData[bin] / 255;
      const y = H - amplitude * H * 0.92;
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = colors.primary;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Marqueurs des bandes EQ
    EQ_BANDS.forEach(({ freq }) => {
      const x = Math.round(freqToX(freq)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, 0); ctx.lineTo(x, H);
      ctx.strokeStyle = 'rgba(148,163,184,0.20)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    });
  }

  // Redimensionner le canvas au pixel près
  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    canvas.width  = Math.round(rect.width  * devicePixelRatio);
    canvas.height = Math.round(rect.height * devicePixelRatio);
    ctx.scale(devicePixelRatio, devicePixelRatio);
    canvas.style.width  = rect.width  + 'px';
    canvas.style.height = rect.height + 'px';
  }

  // Démarrer / arrêter
  function startSpectrum() {
    if (!audioCtx) initAudio();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    resizeCanvas();
    running = true;
    toggleBtn.textContent = '⏹ Stop';
    toggleBtn.classList.add('active');
    draw();
  }

  function stopSpectrum() {
    running = false;
    cancelAnimationFrame(rafId);
    toggleBtn.textContent = '▶ Start';
    toggleBtn.classList.remove('active');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  toggleBtn.addEventListener('click', () => {
    running ? stopSpectrum() : startSpectrum();
  });

  // Resync filtres quand les sliders EQ bougent
  document.querySelectorAll('.eq-slider').forEach(slider => {
    slider.addEventListener('input', updateFilters);
  });

  // Redimensionner si la fenêtre change
  window.addEventListener('resize', () => {
    if (running) { resizeCanvas(); }
  });
})();
