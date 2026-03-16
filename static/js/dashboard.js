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

let _stateEventSource = null;

function startRealtimeSync() {
  if (!window.EventSource) return;
  if (_stateEventSource) _stateEventSource.close();

  _stateEventSource = new EventSource('/api/stream');

  _stateEventSource.addEventListener('state', (event) => {
    try {
      const state = JSON.parse(event.data);
      render(state);
    } catch (_) {
      // ignore payload invalid
    }
  });

  _stateEventSource.onerror = () => {
    // tentative de reconnexion contrôlée
    if (_stateEventSource) {
      _stateEventSource.close();
      _stateEventSource = null;
    }
    setTimeout(startRealtimeSync, 1500);
  };
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

startRealtimeSync();

// ── Spectrum Analyzer — source audio serveur Linux ────────────────────
(function () {
  const canvas = document.getElementById('spectrumCanvas');
  const toggleBtn = document.getElementById('spectrumToggle');
  const refreshBtn = document.getElementById('spectrumRefreshDevices');
  const deviceSelect = document.getElementById('spectrumDevice');
  const hint = document.getElementById('spectrumHint');
  const ctx = canvas.getContext('2d');

  let running = false;
  let pollTimer = null;

  function setHint(text) {
    hint.textContent = text;
  }

  async function loadDevices() {
    const response = await fetch('/api/audio/devices');
    const data = await response.json();

    deviceSelect.innerHTML = '';
    const autoOpt = document.createElement('option');
    autoOpt.value = '';
    autoOpt.textContent = 'Défaut serveur';
    deviceSelect.appendChild(autoOpt);

    (data.devices || []).forEach((dev) => {
      const opt = document.createElement('option');
      opt.value = String(dev.id);
      opt.textContent = `${dev.id} · ${dev.name}`;
      deviceSelect.appendChild(opt);
    });

    if (!data.available) {
      setHint('Backend audio serveur indisponible. Installer numpy + sounddevice.');
      toggleBtn.disabled = true;
      deviceSelect.disabled = true;
      refreshBtn.disabled = true;
      return;
    }

    toggleBtn.disabled = false;
    deviceSelect.disabled = false;
    refreshBtn.disabled = false;
    setHint('Spectre basé sur l\'audio du serveur Linux.');
  }

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.round(rect.width * devicePixelRatio);
    canvas.height = Math.round(rect.height * devicePixelRatio);
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  }

  function drawBins(bins) {
    const W = canvas.width / devicePixelRatio;
    const H = canvas.height / devicePixelRatio;

    ctx.clearRect(0, 0, W, H);

    ctx.strokeStyle = 'rgba(148,163,184,0.12)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = Math.round((H / 4) * i) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(W, y);
      ctx.stroke();
    }

    const primary = getComputedStyle(document.body).getPropertyValue('--primary').trim() || '#2563eb';
    const gradient = ctx.createLinearGradient(0, 0, 0, H);
    gradient.addColorStop(0, primary);
    gradient.addColorStop(0.65, `${primary}99`);
    gradient.addColorStop(1, `${primary}11`);

    ctx.beginPath();
    ctx.moveTo(0, H);
    bins.forEach((v, i) => {
      const x = (i / Math.max(1, bins.length - 1)) * W;
      const y = H - Math.max(0, Math.min(1, v)) * H * 0.92;
      ctx.lineTo(x, y);
    });
    ctx.lineTo(W, H);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    bins.forEach((v, i) => {
      const x = (i / Math.max(1, bins.length - 1)) * W;
      const y = H - Math.max(0, Math.min(1, v)) * H * 0.92;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = primary;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  async function pollSpectrum() {
    if (!running) return;
    try {
      const response = await fetch('/api/spectrum');
      const data = await response.json();
      if (!data.running) {
        stopVisual('Capture serveur arrêtée.');
        return;
      }
      if (Array.isArray(data.bins)) {
        drawBins(data.bins);
      }
      if (data.error) {
        setHint(`Avertissement: ${data.error}`);
      }
    } catch (_) {
      stopVisual('Connexion serveur perdue.');
      return;
    }

    pollTimer = setTimeout(pollSpectrum, 180);
  }

  function stopVisual(message) {
    running = false;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    toggleBtn.textContent = '▶ Start serveur';
    toggleBtn.classList.remove('active');
    if (message) setHint(message);
  }

  async function startServerSpectrum() {
    const payload = { device: deviceSelect.value || null };
    const response = await fetch('/api/audio/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setHint(data.error || 'Impossible de démarrer la capture serveur.');
      return;
    }

    running = true;
    toggleBtn.textContent = '⏹ Stop serveur';
    toggleBtn.classList.add('active');
    setHint('Capture serveur active.');
    pollSpectrum();
  }

  async function stopServerSpectrum() {
    try {
      await fetch('/api/audio/stop', { method: 'POST' });
    } catch (_) {
      // ignore
    }
    stopVisual('Capture serveur arrêtée.');
  }

  toggleBtn.addEventListener('click', async () => {
    if (running) {
      await stopServerSpectrum();
    } else {
      await startServerSpectrum();
    }
  });

  refreshBtn.addEventListener('click', loadDevices);
  window.addEventListener('resize', resizeCanvas);

  resizeCanvas();
  loadDevices();
})();
